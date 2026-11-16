"""Baseline the current application schema.

This migration is safe for both a clean database and an existing installation
previously initialized through SQLAlchemy create_all. Existing tables are
validated before the revision is recorded.

Revision ID: 20261116_0001
Revises:
Create Date: 2026-11-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20261116_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


account_type_enum = postgresql.ENUM(
    "CREDIT",
    "DEBIT",
    "SAVINGS",
    name="account_type_enum",
    create_type=False,
)
transaction_type_enum = postgresql.ENUM(
    "PURCHASE",
    "PAYMENT",
    "TRANSFER",
    "DEPOSIT",
    "WITHDRAWAL",
    "FEE",
    "UNKNOWN",
    name="transaction_type_enum",
    create_type=False,
)
fund_type_enum = postgresql.ENUM(
    "ETF_EXCHANGE",
    "PIF_OPEN",
    name="fund_type_enum",
    create_type=False,
)


EXPECTED_COLUMNS = {
    "billing_periods": {
        "id", "month", "total_spent", "grace_deadline", "is_closed", "created_at",
    },
    "transactions": {
        "id", "billing_period_id", "sms_text", "card_tail", "account_type",
        "amount", "transaction_type", "merchant", "category", "is_grace_safe",
        "is_expense", "balance_after", "card", "grace_deadline",
        "raw_llm_response", "is_parsed", "created_at",
    },
    "webhook_events": {"key", "transaction_id", "created_at"},
    "daily_yields": {
        "date", "account_tail", "end_of_day_balance", "applied_rate",
        "earned_amount", "created_at",
    },
    "budget_limits": {
        "id", "category", "monthly_limit", "is_active", "created_at", "updated_at",
    },
    "credit_payments": {
        "id", "billing_period_id", "transaction_id", "amount", "payment_date",
        "created_at",
    },
    "investment_lots": {
        "id", "ticker", "isin", "fund_name", "fund_type", "quantity",
        "purchase_price", "purchase_date", "ldv_date", "is_active", "created_at",
    },
    "investment_prices": {
        "id", "ticker", "price_date", "price", "source", "created_at",
    },
}


def _missing(table_name: str, existing_tables: set[str]) -> bool:
    return table_name not in existing_tables


def _validate_schema() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    for table_name, expected in EXPECTED_COLUMNS.items():
        if table_name not in tables:
            raise RuntimeError(f"Migration did not create required table: {table_name}")
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        missing = expected - actual
        if missing:
            names = ", ".join(sorted(missing))
            raise RuntimeError(
                f"Existing table {table_name} is incompatible; missing columns: {names}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    if bind.dialect.name == "postgresql":
        account_type_enum.create(bind, checkfirst=True)
        transaction_type_enum.create(bind, checkfirst=True)
        fund_type_enum.create(bind, checkfirst=True)

    if _missing("billing_periods", existing):
        op.create_table(
            "billing_periods",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("month", sa.Date(), nullable=False, comment="Первое число отчётного месяца"),
            sa.Column("total_spent", sa.Numeric(12, 2), server_default=sa.text("0"), comment="Суммарные траты за период"),
            sa.Column("grace_deadline", sa.Date(), nullable=False, comment="Дедлайн погашения"),
            sa.Column("is_closed", sa.Boolean(), server_default=sa.text("false"), comment="Погашен ли долг"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("month"),
        )

    if _missing("transactions", existing):
        op.create_table(
            "transactions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("billing_period_id", sa.Integer()),
            sa.Column("sms_text", sa.Text(), nullable=False, comment="Оригинальный текст СМС"),
            sa.Column("card_tail", sa.String(4), comment="Последние 4 цифры карты/счёта"),
            sa.Column("account_type", account_type_enum, comment="Тип счёта"),
            sa.Column("amount", sa.Numeric(12, 2), comment="Сумма транзакции"),
            sa.Column("transaction_type", transaction_type_enum),
            sa.Column("merchant", sa.String(255), comment="Название мерчанта/получателя"),
            sa.Column("category", sa.String(100), comment="Категория операции"),
            sa.Column("is_grace_safe", sa.Boolean(), comment="Попадает ли транзакция под грейс-период"),
            sa.Column("is_expense", sa.Boolean(), comment="True = расход, False = доход"),
            sa.Column("balance_after", sa.Numeric(12, 2), comment="Баланс после операции"),
            sa.Column("card", sa.String(50), comment="Маска карты/счёта"),
            sa.Column("grace_deadline", sa.Date(), comment="Дедлайн погашения по грейсу"),
            sa.Column("raw_llm_response", sa.Text(), comment="Сырой JSON ответ от Ollama"),
            sa.Column("is_parsed", sa.Boolean(), comment="Успешно ли распарсена СМС"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["billing_period_id"],
                ["billing_periods.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if _missing("webhook_events", existing):
        op.create_table(
            "webhook_events",
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("transaction_id", sa.Integer()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
            sa.PrimaryKeyConstraint("key"),
            sa.UniqueConstraint("transaction_id"),
        )

    if _missing("daily_yields", existing):
        op.create_table(
            "daily_yields",
            sa.Column("date", sa.Date(), nullable=False, comment="Дата расчёта"),
            sa.Column("account_tail", sa.String(4), nullable=False),
            sa.Column("end_of_day_balance", sa.Numeric(12, 2), nullable=False, comment="Баланс на конец дня"),
            sa.Column("applied_rate", sa.Float(), nullable=False, comment="Примененная ставка"),
            sa.Column("earned_amount", sa.Numeric(12, 2), nullable=False, comment="Заработанная сумма"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("date"),
        )

    if _missing("budget_limits", existing):
        op.create_table(
            "budget_limits",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("category", sa.String(100), nullable=False, comment="Категория расходов"),
            sa.Column("monthly_limit", sa.Numeric(12, 2), nullable=False, comment="Месячный лимит"),
            sa.Column("is_active", sa.Boolean(), comment="Активен ли лимит"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("category"),
        )

    if _missing("credit_payments", existing):
        op.create_table(
            "credit_payments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("billing_period_id", sa.Integer(), nullable=False),
            sa.Column("transaction_id", sa.Integer(), comment="Связанная транзакция (если распарсена из СМС)"),
            sa.Column("amount", sa.Numeric(12, 2), nullable=False, comment="Сумма платежа"),
            sa.Column("payment_date", sa.Date(), nullable=False, comment="Дата платежа"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["billing_period_id"],
                ["billing_periods.id"],
            ),
            sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if _missing("investment_lots", existing):
        op.create_table(
            "investment_lots",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ticker", sa.String(20), nullable=False, comment="Тикер инструмента: SBMM, SBGB, PIF_NAK, ..."),
            sa.Column("isin", sa.String(12), comment="ISIN ценной бумаги"),
            sa.Column("fund_name", sa.String(150), comment="Полное название фонда"),
            sa.Column("fund_type", fund_type_enum, nullable=False, comment="Тип фонда (биржевой/открытый ПИФ)"),
            sa.Column("quantity", sa.Numeric(18, 5), nullable=False, comment="Количество паёв/единиц (дробные — для ОПИФ)"),
            sa.Column("purchase_price", sa.Numeric(14, 4), nullable=False, comment="Цена покупки за 1 пай"),
            sa.Column("purchase_date", sa.Date(), nullable=False, comment="Дата покупки"),
            sa.Column("ldv_date", sa.Date(), nullable=False, comment="Дата начала ЛДВ (purchase_date + 3 года)"),
            sa.Column(
                "is_active",
                sa.Boolean(),
                server_default=sa.text("true"),
                comment="False если лот полностью продан",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
        )

    if _missing("investment_prices", existing):
        op.create_table(
            "investment_prices",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ticker", sa.String(20), nullable=False, comment="Тикер инструмента"),
            sa.Column("price_date", sa.Date(), nullable=False, comment="Дата цены"),
            sa.Column("price", sa.Numeric(14, 4), nullable=False, comment="Цена за 1 пай"),
            sa.Column("source", sa.String(20), comment="Источник: moex | manual"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "ticker",
                "price_date",
                name="uq_investment_price_ticker_date",
            ),
        )

    _validate_schema()


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    for table_name in (
        "investment_prices",
        "investment_lots",
        "credit_payments",
        "budget_limits",
        "daily_yields",
        "webhook_events",
        "transactions",
        "billing_periods",
    ):
        if table_name in existing:
            op.drop_table(table_name)

    if bind.dialect.name == "postgresql":
        fund_type_enum.drop(bind, checkfirst=True)
        transaction_type_enum.drop(bind, checkfirst=True)
        account_type_enum.drop(bind, checkfirst=True)
