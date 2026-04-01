"""
CreditCardService — бизнес-логика «Кредитной карусели».

Реализует:
- Правило 120 дней (расчёт grace_deadline)
- Контроль кредитного лимита
- Отслеживание бонусного порога трат
- Аларм при grace-unsafe операциях
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from dateutil.relativedelta import relativedelta
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import AsyncORM, BillingPeriod

logger = logging.getLogger(__name__)
settings = get_settings()


class GraceUnsafeAlarm:
    """
    Аларм при операциях, аннулирующих льготный период.

    В будущем — отправка в брокер сообщений (Redis pub/sub, Celery task).
    Сейчас — логирование + сохранение флага.
    """

    @staticmethod
    async def trigger(
        transaction_id: int,
        amount: Decimal,
        merchant: Optional[str],
        transaction_type: str,
    ) -> None:
        """Немедленный аларм — grace-unsafe операция."""
        logger.critical(
            f"GRACE UNSAFE! Льготный период под угрозой!\n"
            f"   Transaction ID: {transaction_id}\n"
            f"   Сумма: {amount}₽\n"
            f"   Мерчант: {merchant}\n"
            f"   Тип: {transaction_type}\n"
            f"   Снятие наличных / перевод / комиссия — грейс аннулирован!"
        )


class CreditCardService:
    """
    Сервис финансовой логики кредитной карты Сбера.

    Правила «120 дней без процентов»:
    - Грейс-период = месяц покупки + 3 следующих месяца
    - Дедлайн = последний день 3-го месяца после месяца покупки
    """

    # ─── Правило 120 дней ───────────────────────────────────────

    @staticmethod
    def calculate_grace_deadline(transaction_date: date) -> date:
        """
        Рассчитать дедлайн грейс-периода по правилу 120 дней Сбера.

        Грейс начинается 1-го числа месяца покупки и длится
        этот месяц + 3 следующих.

        Примеры:
            01.04 — 30.04  →  дедлайн 31.07
            15.05 — 31.05  →  дедлайн 31.08
            01.01 — 31.01  →  дедлайн 30.04
            01.11 — 30.11  →  дедлайн 28.02 (29.02 в високосный)

        Returns:
            date: Последний день 3-го месяца после месяца покупки.
        """
        # Первое число месяца покупки
        month_start = transaction_date.replace(day=1)

        # +4 месяца от начала → получаем 1-е число 4-го месяца
        # Затем -1 день → последний день 3-го месяца
        deadline = month_start + relativedelta(months=4) - relativedelta(days=1)

        return deadline

    @staticmethod
    def get_billing_month(transaction_date: date) -> date:
        """
        Определить отчётный месяц для транзакции.

        Returns:
            date: Первое число месяца (например, date(2026, 4, 1)).
        """
        return transaction_date.replace(day=1)

    # ─── Обработка транзакции ───────────────────────────────────

    @classmethod
    async def process_transaction(
        cls,
        session: AsyncSession,
        transaction_id: int,
        *,
        amount: Optional[Decimal],
        is_expense: Optional[bool],
        is_grace_safe: Optional[bool],
        merchant: Optional[str],
        transaction_type: str,
        transaction_date: Optional[date] = None,
    ) -> dict:
        """
        Обработать распарсенную транзакцию — привязать к BillingPeriod,
        обновить total_spent, проверить grace safety и лимиты.

        Args:
            session: Async DB session
            transaction_id: ID транзакции в БД
            amount: Сумма
            is_expense: Расход или доход
            is_grace_safe: Безопасна ли для грейс-периода
            merchant: Мерчант
            transaction_type: Тип транзакции (строка)
            transaction_date: Дата транзакции (по умолчанию — сегодня)

        Returns:
            {
                "success": bool,
                "billing_period": BillingPeriod | None,
                "warnings": list[str],
                "errors": list[str],
            }
        """
        warnings = []
        errors = []

        if amount is not None and amount < 0:
            errors.append(f"Отрицательная сумма: {amount}₽")
            return {"success": False, "billing_period": None, "warnings": warnings, "errors": errors}

        if not is_expense or amount is None:
            logger.info(f"Транзакция {transaction_id}: не расход или нет суммы — пропуск")
            return {"success": True, "billing_period": None, "warnings": ["Не расход или пустая сумма"], "errors": []}

        transaction = await AsyncORM.get_transaction_by_id(session, transaction_id)
        if not transaction:
            errors.append(f"Транзакция {transaction_id} не найдена")
            return {"success": False, "billing_period": None, "warnings": warnings, "errors": errors}

        # Обработка разных типов счётов
        if transaction.account_type == "credit":
            return await cls._process_credit_transaction(
                session, transaction, transaction_id, amount, is_grace_safe, merchant, transaction_type, transaction_date
            )
        elif transaction.account_type == "debit":
            return await cls._process_debit_transaction(
                session, transaction, transaction_id, amount, merchant, transaction_type, transaction_date
            )
        elif transaction.account_type == "savings":
            logger.info(f"Транзакция {transaction_id}: накопительный счёт — только отслеживание баланса")
            return {"success": True, "billing_period": None, "warnings": ["Накопительный счет — не требует обработки"], "errors": []}
        else:
            errors.append(f"Неизвестный тип счёта: {transaction.account_type}")
            return {"success": False, "billing_period": None, "warnings": warnings, "errors": errors}

    @classmethod
    async def _process_credit_transaction(
        cls,
        session: AsyncSession,
        transaction,
        transaction_id: int,
        amount: Decimal,
        is_grace_safe: Optional[bool],
        merchant: Optional[str],
        transaction_type: str,
        transaction_date: Optional[date] = None,
    ) -> dict:
        """Обработка транзакции по кредитной карте."""
        warnings = []
        errors = []

        # Проверка платежа (снижение задолженности)
        if transaction_type == "payment":
            return await cls._process_payment(session, transaction, transaction_id, amount)

        # Проверка лимита
        available = await cls.get_available_limit(session)
        if amount > available:
            msg = f"Превышение лимита: попытка потратить {amount}₽, доступно {available}₽"
            errors.append(msg)
            logger.warning(f"Транзакция {transaction_id}: {msg}")
            return {"success": False, "billing_period": None, "warnings": warnings, "errors": errors}

        tx_date = transaction_date or date.today()
        billing_month = cls.get_billing_month(tx_date)
        grace_deadline = cls.calculate_grace_deadline(tx_date)

        # 1. Получаем/создаём BillingPeriod
        period = await AsyncORM.get_or_create_billing_period(
            session, month=billing_month, grace_deadline=grace_deadline,
        )

        # 2. Привязываем транзакцию к периоду
        transaction.billing_period_id = period.id
        transaction.grace_deadline = grace_deadline
        await session.flush()

        # 3. Обновляем total_spent периода
        current_total = await AsyncORM.get_month_expenses_total(session, billing_month)
        await AsyncORM.update_billing_period_spent(session, period.id, current_total)

        logger.info(
            f"Транзакция {transaction_id} → период {billing_month.strftime('%Y-%m')} | "
            f"total_spent={current_total}₽ | deadline={grace_deadline}"
        )

        # 4. Grace-unsafe? → ALARM!
        if is_grace_safe is False:
            await GraceUnsafeAlarm.trigger(
                transaction_id=transaction_id,
                amount=amount,
                merchant=merchant,
                transaction_type=transaction_type,
            )
            warnings.append(f"ВНИМАНИЕ: операция может аннулировать грейс-период!")

        return {"success": True, "billing_period": period, "warnings": warnings, "errors": []}

    @classmethod
    async def _process_debit_transaction(
        cls,
        session: AsyncSession,
        transaction,
        transaction_id: int,
        amount: Decimal,
        merchant: Optional[str],
        transaction_type: str,
        transaction_date: Optional[date] = None,
    ) -> dict:
        """Обработка транзакции по дебетовой карте с проверкой бюджета."""
        warnings = []
        errors = []

        category = transaction.category or "Прочие"
        tx_date = transaction_date or date.today()
        billing_month = cls.get_billing_month(tx_date)

        # Проверка лимита по категории
        category_limit = await AsyncORM.get_budget_limit(session, category)
        if category_limit:
            month_spent = await AsyncORM.get_month_category_expenses(session, category, billing_month)
            remaining = category_limit.monthly_limit - month_spent

            if amount > remaining:
                msg = f"Превышение бюджета по категории '{category}': попытка {amount}₽, осталось {remaining}₽ из {category_limit.monthly_limit}₽"
                warnings.append(msg)
                logger.warning(f"Транзакция {transaction_id}: {msg}")

        logger.info(
            f"Транзакция {transaction_id} (дебетовая): {amount}₽ по '{category}' | мерчант: {merchant}"
        )

        return {"success": True, "billing_period": None, "warnings": warnings, "errors": []}

    @classmethod
    async def _process_payment(
        cls,
        session: AsyncSession,
        transaction,
        transaction_id: int,
        amount: Decimal,
    ) -> dict:
        """Обработка платежа по кредитной карте (погашение задолженности)."""
        from app.db import CreditPayment

        warnings = []
        errors = []

        tx_date = date.today()
        billing_month = cls.get_billing_month(tx_date)

        # Найти незакрытый период (обычно последний месяц)
        open_periods = await AsyncORM.get_open_billing_periods(session)
        if not open_periods:
            warnings.append("Нет открытых периодов для зачисления платежа")
            return {"success": True, "billing_period": None, "warnings": warnings, "errors": []}

        target_period = open_periods[0]  # Самый старый открытый период (FIFO - гасим долг с ближайшим дедлайном)

        # Записать платёж
        payment = CreditPayment(
            billing_period_id=target_period.id,
            transaction_id=transaction_id,
            amount=amount,
            payment_date=tx_date,
        )
        session.add(payment)
        await session.flush()

        # Проверить, полностью ли закрыт период
        total_spent = target_period.total_spent or Decimal("0")
        total_paid = await AsyncORM.get_period_total_payments(session, target_period.id)

        if total_paid >= total_spent:
            await AsyncORM.close_billing_period(session, target_period.id)
            logger.info(f"Период {target_period.month.strftime('%Y-%m')} закрыт (погашен)")
            warnings.append(f"Период {target_period.month.strftime('%Y-%m')} полностью погашен")
        else:
            remaining = total_spent - total_paid
            warnings.append(f"Платёж зачислен. Осталось погасить: {remaining}₽")

        logger.info(f"Платёж {transaction_id}: +{amount}₽ на период {target_period.id}")

        return {"success": True, "billing_period": target_period, "warnings": warnings, "errors": []}

    # ─── Правило лимита ─────────────────────────────────────────

    @classmethod
    async def get_available_limit(cls, session: AsyncSession) -> Decimal:
        """
        Доступный остаток кредитного лимита.

        Формула: CREDIT_LIMIT - сумма всех непогашенных расходов
                 (по незакрытым BillingPeriod).
        """
        total_unpaid = await AsyncORM.get_total_unpaid_expenses(session)
        available = Decimal(str(settings.credit_limit)) - total_unpaid

        logger.info(
            f"Лимит: {settings.credit_limit}₽ | "
            f"Непогашено: {total_unpaid}₽ | "
            f"Доступно: {available}₽"
        )

        return available

    # ─── Правило бонуса ─────────────────────────────────────────

    @classmethod
    async def get_bonus_target_status(
        cls, session: AsyncSession, target_date: Optional[date] = None,
    ) -> dict:
        """
        Статус выполнения бонусного порога трат за текущий месяц.

        Если сумма покупок < TARGET_SPEND_FOR_BONUS, вернуть
        сколько ещё нужно потратить для получения +3.5% на накопительном.

        Returns:
            {
                "month": "2026-04",
                "total_spent": Decimal,
                "target": float,
                "remaining": Decimal,  # 0 если цель достигнута
                "is_target_reached": bool,
                "progress_percent": float,
            }
        """
        check_date = target_date or date.today()
        billing_month = cls.get_billing_month(check_date)
        target = Decimal(str(settings.target_spend_for_bonus))

        total_spent = await AsyncORM.get_month_expenses_total(session, billing_month)

        remaining = max(Decimal("0"), target - total_spent)
        is_reached = total_spent >= target
        progress = min(100.0, float(total_spent / target * 100)) if target > 0 else 100.0

        result = {
            "month": billing_month.strftime("%Y-%m"),
            "total_spent": total_spent,
            "target": float(target),
            "remaining": remaining,
            "is_target_reached": is_reached,
            "progress_percent": round(progress, 1),
        }

        status_text = "OK" if is_reached else "PROGRESS"
        logger.info(
            f"{status_text} Бонус [{billing_month.strftime('%Y-%m')}]: "
            f"{total_spent}₽ / {target}₽ ({progress:.1f}%) | "
            f"{'Достигнуто!' if is_reached else f'Осталось {remaining}₽'}"
        )

        return result

    # ─── Статистика трат ────────────────────────────────────────

    @classmethod
    async def get_spending_statistics(cls, session: AsyncSession, account_type: Optional[str] = None) -> dict:
        """
        Статистика реальных трат (Ежедневно, Еженедельно, Ежемесячно).
        Исключает внутренние переводы (Перевод между счетами).
        """
        from datetime import datetime, time, timedelta

        # Локальное время для расчета границ периода
        now = datetime.now()
        
        # Сегодня: с 00:00:00 до 23:59:59
        today_start = datetime.combine(now.date(), time.min)
        today_end = datetime.combine(now.date(), time.max)
        
        # Неделя: с понедельника до конца воскресенья
        week_start = today_start - timedelta(days=now.weekday())
        week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59, microseconds=999999)
        
        # Месяц: с 1-го числа до конца текущего месяца
        month_start = today_start.replace(day=1)
        if month_start.month == 12:
            next_month_start = month_start.replace(year=month_start.year + 1, month=1, day=1)
        else:
            next_month_start = month_start.replace(month=month_start.month + 1, day=1)
        month_end = next_month_start - timedelta(microseconds=1)

        daily = await AsyncORM.get_real_expenses_for_period(session, today_start, today_end, account_type)
        weekly = await AsyncORM.get_real_expenses_for_period(session, week_start, week_end, account_type)
        monthly = await AsyncORM.get_real_expenses_for_period(session, month_start, month_end, account_type)

        return {
            "daily": abs(float(daily)),
            "weekly": abs(float(weekly)),
            "monthly": abs(float(monthly)),
        }

    # ─── Сводка ─────────────────────────────────────────────────

    @classmethod
    async def get_financial_summary(cls, session: AsyncSession) -> dict:
        """
        Полная финансовая сводка (для дайджестов и аналитики).

        Returns:
            {
                "available_limit": float,
                "credit_limit": float,
                "credit_usage_percent": float,
                "bonus_status": dict,
                "open_periods": list[dict],
                "spending_stats": dict,
                "latest_savings_balance": float,
                "debit_monthly_limit": float,
            }
        """
        available = await cls.get_available_limit(session)
        total_unpaid = await AsyncORM.get_total_unpaid_expenses(session)
        bonus = await cls.get_bonus_target_status(session)
        open_periods = await AsyncORM.get_open_billing_periods(session)
        spending_stats = await cls.get_spending_statistics(session)
        savings_balance = await AsyncORM.get_latest_savings_balance(session)

        periods_data = [
            {
                "month": p.month.strftime("%Y-%m"),
                "total_spent": float(p.total_spent) if p.total_spent else 0,
                "grace_deadline": p.grace_deadline.isoformat(),
                "days_left": (p.grace_deadline - date.today()).days,
                "is_closed": p.is_closed,
            }
            for p in open_periods
        ]

        credit_usage = float(total_unpaid) / settings.credit_limit * 100 if settings.credit_limit > 0 else 0

        return {
            "available_limit": float(available),
            "credit_limit": float(settings.credit_limit),
            "credit_usage_percent": round(credit_usage, 1),
            "total_unpaid": float(total_unpaid),
            "bonus_status": bonus,
            "open_periods": periods_data,
            "spending_stats": spending_stats,
            "latest_savings_balance": float(savings_balance),
            "debit_monthly_limit": float(settings.debit_monthly_limit),
        }
