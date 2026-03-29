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
        # TODO: Celery task → Telegram notification
        # TODO: Redis pub/sub event


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
    ) -> Optional[BillingPeriod]:
        """
        Обработать распарсенную транзакцию — привязать к BillingPeriod,
        обновить total_spent, проверить grace safety.

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
            BillingPeriod, к которому привязана транзакция (или None для доходов).
        """
        if not is_expense or amount is None:
            logger.info(f"Транзакция {transaction_id}: не расход или нет суммы — пропуск")
            return None

        tx_date = transaction_date or date.today()
        billing_month = cls.get_billing_month(tx_date)
        grace_deadline = cls.calculate_grace_deadline(tx_date)

        # 1. Получаем/создаём BillingPeriod
        period = await AsyncORM.get_or_create_billing_period(
            session, month=billing_month, grace_deadline=grace_deadline,
        )

        # 2. Привязываем транзакцию к периоду
        transaction = await AsyncORM.get_transaction_by_id(session, transaction_id)
        if transaction:
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

        return period

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

    # ─── Сводка ─────────────────────────────────────────────────

    @classmethod
    async def get_financial_summary(cls, session: AsyncSession) -> dict:
        """
        Полная финансовая сводка (для дайджестов и Telegram-бота).

        Returns:
            {
                "available_limit": Decimal,
                "credit_limit": float,
                "bonus_status": dict,
                "open_periods": list[dict],
            }
        """
        available = await cls.get_available_limit(session)
        bonus = await cls.get_bonus_target_status(session)
        open_periods = await AsyncORM.get_open_billing_periods(session)

        periods_data = [
            {
                "month": p.month.strftime("%Y-%m"),
                "total_spent": float(p.total_spent) if p.total_spent else 0,
                "grace_deadline": p.grace_deadline.isoformat(),
                "days_left": (p.grace_deadline - date.today()).days,
            }
            for p in open_periods
        ]

        return {
            "available_limit": float(available),
            "credit_limit": settings.credit_limit,
            "bonus_status": bonus,
            "open_periods": periods_data,
        }
