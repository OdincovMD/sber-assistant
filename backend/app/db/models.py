import enum
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, String, Numeric, DateTime, Date,
    Boolean, Enum, Text, ForeignKey, func,
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


class BillingPeriod(Base):
    """
    Отчётный период кредитной карты.

    Каждый календарный месяц — один BillingPeriod.
    Грейс-период: месяц покупки + 3 месяца (правило 120 дней Сбера).
    """

    __tablename__ = "billing_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Период — первое число месяца (2026-04-01 = апрель 2026)
    month = Column(Date, nullable=False, unique=True, comment="Первое число отчётного месяца")

    # Финансы
    total_spent = Column(
        Numeric(12, 2), default=0, server_default="0",
        comment="Суммарные траты за период (is_expense=True)",
    )

    # Грейс
    grace_deadline = Column(
        Date, nullable=False,
        comment="Дедлайн погашения (последний день месяца+3)",
    )

    # Статус
    is_closed = Column(
        Boolean, default=False, server_default="false",
        comment="Погашен ли долг за этот месяц",
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Дата создания записи",
    )

    # Связь с транзакциями
    transactions = relationship(
        "Transaction", back_populates="billing_period", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"<BillingPeriod(id={self.id}, month={self.month}, "
            f"spent={self.total_spent}, deadline={self.grace_deadline}, "
            f"closed={self.is_closed})>"
        )


class Transaction(Base):
    """Транзакция, извлечённая из СМС."""

    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Привязка к отчётному периоду
    billing_period_id = Column(
        Integer, ForeignKey("billing_periods.id"), nullable=True,
        comment="FK на отчётный период",
    )

    # Исходные данные
    sms_text = Column(Text, nullable=False, comment="Оригинальный текст СМС")

    # Результат парсинга LLM
    amount = Column(Numeric(12, 2), nullable=True, comment="Сумма транзакции")
    transaction_type = Column(
        Enum(TransactionType, name="transaction_type_enum"),
        nullable=True,
        default=TransactionType.UNKNOWN,
        comment="Тип транзакции",
    )
    merchant = Column(String(255), nullable=True, comment="Название мерчанта/получателя")
    is_grace_safe = Column(
        Boolean, nullable=True,
        comment="Попадает ли транзакция под грейс-период",
    )
    is_expense = Column(
        Boolean, nullable=True,
        comment="True = расход (деньги ушли), False = доход (деньги пришли)",
    )
    balance_after = Column(
        Numeric(12, 2), nullable=True,
        comment="Баланс после операции",
    )
    card = Column(String(50), nullable=True, comment="Маска карты/счёта (ECMC6517, СЧЁТ9103)")

    # Грейс-период (дедлайн конкретной транзакции, дублирует billing_period.grace_deadline)
    grace_deadline = Column(Date, nullable=True, comment="Дедлайн погашения по грейс-периоду")

    # Технические поля
    raw_llm_response = Column(Text, nullable=True, comment="Сырой JSON ответ от Ollama")
    is_parsed = Column(Boolean, default=False, comment="Успешно ли распарсена СМС")

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        comment="Дата создания записи",
    )

    # Связь с периодом
    billing_period = relationship("BillingPeriod", back_populates="transactions")

    def __repr__(self) -> str:
        return f"<Transaction(id={self.id}, amount={self.amount}, type={self.transaction_type})>"
