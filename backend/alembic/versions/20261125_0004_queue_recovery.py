"""Recover transactions lost in the background queue.

Revision ID: 20261125_0004
Revises: 20261119_0003
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261125_0004"
down_revision: Union[str, None] = "20261119_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }

    if "queued_at" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "queued_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Время последней постановки фоновой задачи в очередь",
            ),
        )
    if "queue_recovery_attempts" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "queue_recovery_attempts",
                sa.Integer(),
                nullable=True,
                comment="Число автоматических повторных постановок в очередь",
            ),
        )

    op.execute(
        """
        UPDATE transactions
        SET queued_at = COALESCE(queued_at, created_at, CURRENT_TIMESTAMP),
            queue_recovery_attempts = COALESCE(queue_recovery_attempts, 0)
        WHERE queued_at IS NULL OR queue_recovery_attempts IS NULL
        """
    )
    op.alter_column(
        "transactions",
        "queued_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.alter_column(
        "transactions",
        "queue_recovery_attempts",
        existing_type=sa.Integer(),
        nullable=False,
        server_default=sa.text("0"),
    )

    inspector = sa.inspect(op.get_bind())
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("transactions")
    }
    if "ix_transactions_queue_recovery" not in existing_indexes:
        op.create_index(
            "ix_transactions_queue_recovery",
            "transactions",
            ["processing_status", "queued_at"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("transactions")
    }
    if "ix_transactions_queue_recovery" in existing_indexes:
        op.drop_index(
            "ix_transactions_queue_recovery",
            table_name="transactions",
        )

    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }
    if "queue_recovery_attempts" in existing_columns:
        op.drop_column("transactions", "queue_recovery_attempts")
    if "queued_at" in existing_columns:
        op.drop_column("transactions", "queued_at")
