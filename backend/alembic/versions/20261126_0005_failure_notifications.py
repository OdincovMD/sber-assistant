"""Track notifications for terminal transaction failures.

Revision ID: 20261126_0005
Revises: 20261125_0004
Create Date: 2026-11-26
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261126_0005"
down_revision: Union[str, None] = "20261125_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }
    if "failure_notified_at" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "failure_notified_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Время успешного уведомления об окончательной ошибке",
            ),
        )

    inspector = sa.inspect(op.get_bind())
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("transactions")
    }
    if "ix_transactions_failure_notifications" not in existing_indexes:
        op.create_index(
            "ix_transactions_failure_notifications",
            "transactions",
            ["processing_status", "failure_notified_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("transactions")
    }
    if "ix_transactions_failure_notifications" in existing_indexes:
        op.drop_index(
            "ix_transactions_failure_notifications",
            table_name="transactions",
        )

    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }
    if "failure_notified_at" in existing_columns:
        op.drop_column("transactions", "failure_notified_at")
