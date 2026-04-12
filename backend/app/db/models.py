import enum
from datetime import datetime, date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Date, Float,
    Boolean, Enum, Text, ForeignKey, UniqueConstraint, func,
)
from sqlalchemy.orm import DeclarativeBase, relationship

class Base(DeclarativeBase):
    """Базовый класс для всех моделей."""
    pass

class TransactionType(str, enum.Enum):
    """Тип транзакции."""
    PURCHASE = "purchase"
    PAYMENT = "payment"
    TRANSFER = "transfer"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    UNKNOWN = "unknown"

class AccountType(str, enum.Enum):
    """Тип банковского счёта/карты."""
    CREDIT = "credit"
    DEBIT = "debit"
    SAVINGS = "savings"

class BillingPeriod(Base):
    """Отчётный период кредитной карты."""
    __tablename__ = "billing_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    month = Column(Date, nullable=False, unique=True, comment="Первое число отчётного месяца")
    total_spent = Column(Numeric(12, 2), default=0, server_default="0", comment="Суммарные траты за период")
    grace_deadline = Column(Date, nullable=False, comment="Дедлайн погашения")
    is_closed = Column(Boolean, default=False, server_default="false", comment="Погашен ли долг")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    transactions = relationship("Transaction", back_populates="billing_period", lazy="selectin")

    def __repr__(self) -> str:
        return f"<BillingPeriod(id={self.id}, month={self.month})>"

class Transaction(Base):
    """Транзакция, извлечённая из СМС."""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    billing_period_id = Column(Integer, ForeignKey("billing_periods.id"), nullable=True)
    sms_text = Column(Text, nullable=False, comment="Оригинальный текст СМС")

    # ─── Маршрутизация по счетам (Stage 2) ────────────────────────
    card_tail = Column(String(4), nullable=True, comment="Последние 4 цифры карты/счёта")
    account_type = Column(Enum(AccountType, name="account_type_enum"), nullable=True, comment="Тип счёта")

    # Результат парсинга
    amount = Column(Numeric(12, 2), nullable=True, comment="Сумма транзакции")
    transaction_type = Column(Enum(TransactionType, name="transaction_type_enum"), nullable=True, default=TransactionType.UNKNOWN)
    merchant = Column(String(255), nullable=True, comment="Название мерчанта/получателя")
    category = Column(String(100), nullable=True, comment="Категория операции")
    is_grace_safe = Column(Boolean, nullable=True, comment="Попадает ли транзакция под грейс-период")
    is_expense = Column(Boolean, nullable=True, comment="True = расход, False = доход")
    balance_after = Column(Numeric(12, 2), nullable=True, comment="Баланс после операции")
    card = Column(String(50), nullable=True, comment="Маска карты/счёта")

    grace_deadline = Column(Date, nullable=True, comment="Дедлайн погашения по грейсу")
    raw_llm_response = Column(Text, nullable=True, comment="Сырой JSON ответ от Ollama")
    is_parsed = Column(Boolean, default=False, comment="Успешно ли распарсена СМС")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    billing_period = relationship("BillingPeriod", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction(id={self.id}, tail={self.card_tail}, amount={self.amount})>"

class DailyYield(Base):
    """Ежедневная расчётная прибыль по накопительному счёту *1837."""
    __tablename__ = "daily_yields"

    date = Column(Date, primary_key=True, comment="Дата расчёта")
    account_tail = Column(String(4), default="1837", nullable=False)
    end_of_day_balance = Column(Numeric(12, 2), nullable=False, comment="Баланс на конец дня")
    applied_rate = Column(Float, nullable=False, comment="Примененная ставка")
    earned_amount = Column(Numeric(12, 2), nullable=False, comment="Заработанная сумма")

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    @staticmethod
    def calculate_daily_yield(current_balance: Decimal) -> Decimal:
        """
        Рассчитать ежедневную прибыль по накопительному счёту.
        Формула: баланс * (ставка / 100) / 365.
        Округление до 2 знаков после запятой.
        """
        from app.config import get_current_rate
        rate = Decimal(str(get_current_rate()))
        daily = (current_balance * (rate / Decimal("100")) / Decimal("365"))
        return daily.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def __repr__(self) -> str:
        return (
            f"<DailyYield date={self.date} tail={self.account_tail} "
            f"balance={self.end_of_day_balance} rate={self.applied_rate} earned={self.earned_amount}>"
        )

class BudgetLimit(Base):
    """Лимит расходов по категориям дебетовой карты."""
    __tablename__ = "budget_limits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(100), nullable=False, unique=True, comment="Категория расходов")
    monthly_limit = Column(Numeric(12, 2), nullable=False, comment="Месячный лимит")
    is_active = Column(Boolean, default=True, comment="Активен ли лимит")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<BudgetLimit(category={self.category}, limit={self.monthly_limit})>"

class CreditPayment(Base):
    """История платежей по кредитной карте для отслеживания погашения."""
    __tablename__ = "credit_payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    billing_period_id = Column(Integer, ForeignKey("billing_periods.id"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=True, comment="Связанная транзакция (если распарсена из СМС)")
    amount = Column(Numeric(12, 2), nullable=False, comment="Сумма платежа")
    payment_date = Column(Date, nullable=False, comment="Дата платежа")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    billing_period = relationship("BillingPeriod", backref="payments")

    def __repr__(self) -> str:
        return f"<CreditPayment(period={self.billing_period_id}, amount={self.amount})>"


# ─── Инвестиционный модуль ──────────────────────────────────────────────────


class FundType(str, enum.Enum):
    """Тип инвестиционного инструмента."""
    ETF_EXCHANGE = "etf_exchange"  # Биржевой БПИФ — расчёты T+1 (SBGB, SBGD, SBFR, SBMM)
    PIF_OPEN     = "pif_open"      # Открытый ПИФ — погашение через УК, T+3..5


class InvestmentLot(Base):
    """
    Лот покупки инвестиционного инструмента.

    Каждая покупка — отдельный лот (для точного расчёта ЛДВ и P&L).
    ЛДВ (льгота долгосрочного владения) — 3 года с даты покупки.
    """
    __tablename__ = "investment_lots"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    ticker        = Column(String(20),  nullable=False, comment="Тикер инструмента: SBMM, SBGB, PIF_NAK, ...")
    isin          = Column(String(12),  nullable=True,  comment="ISIN ценной бумаги")
    fund_name     = Column(String(150), nullable=True,  comment="Полное название фонда")
    fund_type     = Column(Enum(FundType, name="fund_type_enum"), nullable=False, comment="Тип фонда (биржевой/открытый ПИФ)")

    quantity      = Column(Numeric(18, 5), nullable=False, comment="Количество паёв/единиц (дробные — для ОПИФ)")
    purchase_price = Column(Numeric(14, 4), nullable=False, comment="Цена покупки за 1 пай")
    purchase_date  = Column(Date, nullable=False, comment="Дата покупки")
    ldv_date       = Column(Date, nullable=False, comment="Дата начала ЛДВ (purchase_date + 3 года)")

    is_active     = Column(Boolean, default=True, server_default="true", comment="False если лот полностью продан")
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    @property
    def invested_amount(self) -> Decimal:
        """Сумма вложений по лоту."""
        return Decimal(str(self.quantity)) * Decimal(str(self.purchase_price))

    def __repr__(self) -> str:
        return (
            f"<InvestmentLot(ticker={self.ticker}, qty={self.quantity}, "
            f"price={self.purchase_price}, date={self.purchase_date})>"
        )


class InvestmentPrice(Base):
    """
    Дневной снимок цены инвестиционного инструмента.

    Источники: MOEX ISS API (биржевые БПИФ) или ручной ввод (открытые ПИФ).
    Хранится одна запись на тикер в день (upsert по unique constraint).
    """
    __tablename__ = "investment_prices"
    __table_args__ = (
        UniqueConstraint("ticker", "price_date", name="uq_investment_price_ticker_date"),
    )

    id         = Column(Integer, primary_key=True, autoincrement=True)
    ticker     = Column(String(20), nullable=False, comment="Тикер инструмента")
    price_date = Column(Date, nullable=False, comment="Дата цены")
    price      = Column(Numeric(14, 4), nullable=False, comment="Цена за 1 пай")
    source     = Column(String(20), default="moex", comment="Источник: moex | manual")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<InvestmentPrice(ticker={self.ticker}, date={self.price_date}, price={self.price})>"
