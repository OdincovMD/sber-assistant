"""
AsyncORM — единый класс для всех взаимодействий с базой данных.

Включает:
- Инициализацию движка и сессий
- Создание таблиц через SQLAlchemy metadata (no raw SQL)
- CRUD операции для Transaction и BillingPeriod
"""

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
    AsyncEngine,
)

from app.config import get_settings
from app.db.models import (
    Base, Transaction, TransactionType, BillingPeriod, BudgetLimit, CreditPayment,
    InvestmentLot, InvestmentPrice, FundType,
)

logger = logging.getLogger(__name__)


class AsyncORM:
    """Асинхронный ORM-менеджер — все взаимодействия с БД через этот класс."""

    _engine: Optional[AsyncEngine] = None
    _session_factory: Optional[async_sessionmaker] = None

    # ─── Инициализация / Завершение ─────────────────────────────

    @classmethod
    async def init(cls) -> None:
        """Инициализация движка, сессий и создание таблиц."""
        settings = get_settings()

        cls._engine = create_async_engine(
            settings.database_url,
            echo=settings.app_debug,
            pool_size=5,
            max_overflow=10,
        )

        cls._session_factory = async_sessionmaker(
            cls._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Создаём таблицы если не существуют (no raw SQL)
        async with cls._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info("Database initialized — tables created/verified")

    @classmethod
    async def close(cls) -> None:
        """Закрытие пула соединений."""
        if cls._engine:
            await cls._engine.dispose()
            logger.info("Database connection pool closed")

    @classmethod
    def get_session(cls) -> async_sessionmaker:
        """Получить фабрику сессий."""
        if cls._session_factory is None:
            raise RuntimeError("AsyncORM not initialized. Call AsyncORM.init() first.")
        return cls._session_factory

    @classmethod
    async def get_db(cls) -> AsyncSession:
        """FastAPI dependency — async DB session с авто-коммитом/роллбэком."""
        async with cls.get_session()() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ─── Health Check ───────────────────────────────────────────

    @classmethod
    async def health_check(cls) -> bool:
        """Проверка подключения к БД."""
        try:
            async with cls.get_session()() as session:
                result = await session.execute(select(1))
                result.scalar()
                return True
        except Exception as e:
            logger.error(f"DB health check failed: {e}")
            return False

    # ─── Transaction CRUD ───────────────────────────────────────

    @classmethod
    async def create_transaction(
        cls,
        session: AsyncSession,
        sms_text: str,
        billing_period_id: Optional[int] = None,
    ) -> Transaction:
        """Создать транзакцию с сырым текстом СМС."""
        transaction = Transaction(
            sms_text=sms_text,
            billing_period_id=billing_period_id,
        )
        session.add(transaction)
        await session.flush()
        return transaction

    @classmethod
    async def update_transaction_parsed(
        cls,
        session: AsyncSession,
        transaction: Transaction,
        *,
        card_tail: Optional[str] = None,
        account_type: Optional[str] = None,
        amount: Optional[Decimal] = None,
        transaction_type: TransactionType = TransactionType.UNKNOWN,
        merchant: Optional[str] = None,
        category: Optional[str] = None,
        is_grace_safe: Optional[bool] = None,
        is_expense: Optional[bool] = None,
        balance_after: Optional[Decimal] = None,
        card: Optional[str] = None,
        grace_deadline: Optional[date] = None,
        billing_period_id: Optional[int] = None,
        raw_llm_response: Optional[str] = None,
        is_parsed: bool = False,
    ) -> Transaction:
        """Обновить транзакцию результатами парсинга LLM (v2 — с маршрутизацией по счетам)."""
        transaction.card_tail = card_tail
        transaction.account_type = account_type
        transaction.amount = amount
        transaction.transaction_type = transaction_type
        transaction.merchant = merchant
        transaction.category = category
        transaction.is_grace_safe = is_grace_safe
        transaction.is_expense = is_expense
        transaction.balance_after = balance_after
        transaction.card = card
        transaction.raw_llm_response = raw_llm_response
        transaction.is_parsed = is_parsed
        if grace_deadline is not None:
            transaction.grace_deadline = grace_deadline
        if billing_period_id is not None:
            transaction.billing_period_id = billing_period_id
        await session.flush()
        return transaction

    @classmethod
    async def get_transaction_by_id(
        cls, session: AsyncSession, transaction_id: int
    ) -> Optional[Transaction]:
        """Найти транзакцию по ID."""
        result = await session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_transactions_by_date_range(
        cls,
        session: AsyncSession,
        start_date: date,
        end_date: date,
    ) -> list[Transaction]:
        """Получить транзакции за период."""
        result = await session.execute(
            select(Transaction)
            .where(Transaction.created_at >= start_date)
            .where(Transaction.created_at <= end_date)
            .order_by(Transaction.created_at.desc())
        )
        return list(result.scalars().all())

    @classmethod
    async def get_real_expenses_for_period(
        cls,
        session: AsyncSession,
        start_time: datetime,
        end_time: datetime,
        account_type: Optional[str] = None,
    ) -> Decimal:
        """Суммировать реальные траты за период (исключая переводы самому себе)."""
        query = select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)) \
            .where(Transaction.is_parsed == True) \
            .where(Transaction.is_expense == True) \
            .where(Transaction.category != 'Перевод между счетами') \
            .where(Transaction.created_at >= start_time) \
            .where(Transaction.created_at <= end_time)

        if account_type:
            query = query.where(Transaction.account_type == account_type)

        result = await session.execute(query)
        return result.scalar()
    @classmethod
    async def get_latest_savings_balance(cls, session: AsyncSession) -> Decimal:
        """Получить актуальный остаток по накопительному счёту."""
        result = await session.execute(
            select(Transaction.balance_after)
            .where(Transaction.account_type == "savings")
            .where(Transaction.balance_after.is_not(None))
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
        balance = result.scalar()
        return balance or Decimal("0")

    # ─── BillingPeriod CRUD ─────────────────────────────────────

    @classmethod
    async def get_or_create_billing_period(
        cls,
        session: AsyncSession,
        month: date,
        grace_deadline: date,
    ) -> BillingPeriod:
        """
        Найти или создать отчётный период по дате месяца.

        month: первое число месяца (date(2026, 4, 1))
        grace_deadline: последний день месяца+3
        """
        result = await session.execute(
            select(BillingPeriod).where(BillingPeriod.month == month)
        )
        period = result.scalar_one_or_none()

        if period is None:
            period = BillingPeriod(
                month=month,
                grace_deadline=grace_deadline,
                total_spent=Decimal("0"),
                is_closed=False,
            )
            session.add(period)
            await session.flush()
            logger.info(f"Created BillingPeriod: {month} → deadline {grace_deadline}")

        return period

    @classmethod
    async def get_billing_period_by_month(
        cls, session: AsyncSession, month: date
    ) -> Optional[BillingPeriod]:
        """Найти отчётный период по месяцу."""
        result = await session.execute(
            select(BillingPeriod).where(BillingPeriod.month == month)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_open_billing_periods(
        cls, session: AsyncSession
    ) -> list[BillingPeriod]:
        """Получить все незакрытые отчётные периоды."""
        result = await session.execute(
            select(BillingPeriod)
            .where(BillingPeriod.is_closed == False)
            .order_by(BillingPeriod.grace_deadline.asc())
        )
        return list(result.scalars().all())

    @classmethod
    async def update_billing_period_spent(
        cls,
        session: AsyncSession,
        period_id: int,
        total_spent: Decimal,
    ) -> Optional[BillingPeriod]:
        """Обновить сумму трат в периоде."""
        result = await session.execute(
            select(BillingPeriod).where(BillingPeriod.id == period_id)
        )
        period = result.scalar_one_or_none()
        if period:
            period.total_spent = total_spent
            await session.flush()
        return period

    @classmethod
    async def close_billing_period(
        cls,
        session: AsyncSession,
        period_id: int,
    ) -> Optional[BillingPeriod]:
        """Отметить период как закрытый (долг погашен)."""
        result = await session.execute(
            select(BillingPeriod).where(BillingPeriod.id == period_id)
        )
        period = result.scalar_one_or_none()
        if period:
            period.is_closed = True
            await session.flush()
        return period

    @classmethod
    async def get_total_unpaid_expenses(
        cls, session: AsyncSession
    ) -> Decimal:
        """Сумма всех расходов по незакрытым периодам."""
        result = await session.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
            .join(BillingPeriod, Transaction.billing_period_id == BillingPeriod.id)
            .where(Transaction.is_expense == True)
            .where(Transaction.is_parsed == True)
            .where(BillingPeriod.is_closed == False)
        )
        return result.scalar()

    @classmethod
    async def get_month_expenses_total(
        cls,
        session: AsyncSession,
        month: date,
    ) -> Decimal:
        """Сумма расходов за конкретный месяц (по billing_period)."""
        result = await session.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
            .join(BillingPeriod, Transaction.billing_period_id == BillingPeriod.id)
            .where(BillingPeriod.month == month)
            .where(Transaction.is_expense == True)
            .where(Transaction.is_parsed == True)
        )
        return result.scalar()

    # ─── Лимиты и бюджеты ────────────────────────────────────────

    @classmethod
    async def get_budget_limit(
        cls, session: AsyncSession, category: str
    ) -> Optional[BudgetLimit]:
        """Получить лимит по категории расходов."""
        result = await session.execute(
            select(BudgetLimit)
            .where(BudgetLimit.category == category)
            .where(BudgetLimit.is_active == True)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_month_category_expenses(
        cls,
        session: AsyncSession,
        category: str,
        month: date,
    ) -> Decimal:
        """Сумма расходов по категории за месяц."""
        result = await session.execute(
            select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0))
            .where(Transaction.category == category)
            .where(Transaction.is_expense == True)
            .where(Transaction.is_parsed == True)
            .where(func.date_trunc('month', Transaction.created_at) == month)
        )
        return result.scalar()

    @classmethod
    async def set_budget_limit(
        cls,
        session: AsyncSession,
        category: str,
        monthly_limit: Decimal,
    ) -> BudgetLimit:
        """Установить или обновить лимит по категории."""
        result = await session.execute(
            select(BudgetLimit).where(BudgetLimit.category == category)
        )
        budget = result.scalar_one_or_none()

        if budget:
            budget.monthly_limit = monthly_limit
            budget.is_active = True
        else:
            budget = BudgetLimit(category=category, monthly_limit=monthly_limit, is_active=True)
            session.add(budget)

        await session.flush()
        return budget

    @classmethod
    async def get_period_total_payments(
        cls,
        session: AsyncSession,
        period_id: int,
    ) -> Decimal:
        """Сумма всех платежей по периоду."""
        result = await session.execute(
            select(func.coalesce(func.sum(CreditPayment.amount), 0))
            .where(CreditPayment.billing_period_id == period_id)
        )
        return result.scalar()

    # ─── Investment Lots ─────────────────────────────────────────

    @classmethod
    async def create_investment_lot(
        cls,
        session: AsyncSession,
        *,
        ticker: str,
        isin: Optional[str],
        fund_name: str,
        fund_type: FundType,
        quantity: Decimal,
        purchase_price: Decimal,
        purchase_date: date,
        ldv_date: date,
    ) -> InvestmentLot:
        """Добавить новый лот покупки инвестиционного инструмента."""
        lot = InvestmentLot(
            ticker=ticker,
            isin=isin,
            fund_name=fund_name,
            fund_type=fund_type,
            quantity=quantity,
            purchase_price=purchase_price,
            purchase_date=purchase_date,
            ldv_date=ldv_date,
            is_active=True,
        )
        session.add(lot)
        await session.flush()
        return lot

    @classmethod
    async def get_active_lots(cls, session: AsyncSession) -> list[InvestmentLot]:
        """Все активные лоты (не проданные)."""
        result = await session.execute(
            select(InvestmentLot)
            .where(InvestmentLot.is_active == True)
            .order_by(InvestmentLot.ticker, InvestmentLot.purchase_date)
        )
        return list(result.scalars().all())

    @classmethod
    async def get_lots_by_ticker(
        cls, session: AsyncSession, ticker: str
    ) -> list[InvestmentLot]:
        """Активные лоты по тикеру."""
        result = await session.execute(
            select(InvestmentLot)
            .where(InvestmentLot.ticker == ticker)
            .where(InvestmentLot.is_active == True)
            .order_by(InvestmentLot.purchase_date)
        )
        return list(result.scalars().all())

    @classmethod
    async def lot_exists(
        cls, session: AsyncSession, ticker: str, purchase_date: date, quantity: Decimal
    ) -> bool:
        """Проверить, существует ли лот (для идемпотентного seed)."""
        result = await session.execute(
            select(InvestmentLot)
            .where(InvestmentLot.ticker == ticker)
            .where(InvestmentLot.purchase_date == purchase_date)
            .where(InvestmentLot.quantity == quantity)
        )
        return result.scalar_one_or_none() is not None

    # ─── Investment Prices ───────────────────────────────────────

    @classmethod
    async def upsert_investment_price(
        cls,
        session: AsyncSession,
        ticker: str,
        price_date: date,
        price: Decimal,
        source: str = "moex",
    ) -> InvestmentPrice:
        """Сохранить или обновить цену инструмента на дату."""
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        stmt = (
            pg_insert(InvestmentPrice)
            .values(
                ticker=ticker,
                price_date=price_date,
                price=price,
                source=source,
            )
            .on_conflict_do_update(
                constraint="uq_investment_price_ticker_date",
                set_={"price": price, "source": source},
            )
            .returning(InvestmentPrice)
        )
        result = await session.execute(stmt)
        await session.flush()
        return result.scalar_one()

    @classmethod
    async def get_latest_price(
        cls, session: AsyncSession, ticker: str
    ) -> Optional[InvestmentPrice]:
        """Последняя известная цена инструмента."""
        result = await session.execute(
            select(InvestmentPrice)
            .where(InvestmentPrice.ticker == ticker)
            .order_by(InvestmentPrice.price_date.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @classmethod
    async def get_latest_prices_all(
        cls, session: AsyncSession
    ) -> dict[str, InvestmentPrice]:
        """Словарь последних цен для всех тикеров {ticker: InvestmentPrice}."""
        from sqlalchemy import distinct

        # Для каждого тикера — максимальная дата
        subq = (
            select(
                InvestmentPrice.ticker,
                func.max(InvestmentPrice.price_date).label("max_date"),
            )
            .group_by(InvestmentPrice.ticker)
            .subquery()
        )
        result = await session.execute(
            select(InvestmentPrice)
            .join(
                subq,
                (InvestmentPrice.ticker == subq.c.ticker)
                & (InvestmentPrice.price_date == subq.c.max_date),
            )
        )
        rows = result.scalars().all()
        return {r.ticker: r for r in rows}
