"""Persist payment amounts not yet allocated to billing periods.

Revision ID: 20261118_0002
Revises: 20261116_0001
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261118_0002"
down_revision: Union[str, None] = "20261116_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "unallocated_credits" not in inspector.get_table_names():
        op.create_table(
            "unallocated_credits",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column(
                "transaction_id",
                sa.Integer(),
                nullable=False,
                comment="Транзакция, создавшая авансовый остаток",
            ),
            sa.Column(
                "original_amount",
                sa.Numeric(12, 2),
                nullable=False,
                comment="Исходная нераспределённая сумма",
            ),
            sa.Column(
                "remaining_amount",
                sa.Numeric(12, 2),
                nullable=False,
                comment="Остаток для будущих покупок",
            ),
            sa.Column(
                "payment_date",
                sa.Date(),
                nullable=False,
                comment="Дата исходного платежа",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("transaction_id"),
        )
        return

    expected_columns = {
        "id",
        "transaction_id",
        "original_amount",
        "remaining_amount",
        "payment_date",
        "created_at",
    }
    actual_columns = {
        column["name"]
        for column in inspector.get_columns("unallocated_credits")
    }
    missing = expected_columns - actual_columns
    if missing:
        names = ", ".join(sorted(missing))
        raise RuntimeError(
            "Existing table unallocated_credits is incompatible; "
            f"missing columns: {names}"
        )


def downgrade() -> None:
    op.drop_table("unallocated_credits")
