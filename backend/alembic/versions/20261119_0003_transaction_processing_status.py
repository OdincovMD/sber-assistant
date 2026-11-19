"""Track asynchronous transaction processing results.

Revision ID: 20261119_0003
Revises: 20261118_0002
Create Date: 2026-11-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20261119_0003"
down_revision: Union[str, None] = "20261118_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }

    if "processing_status" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "processing_status",
                sa.String(20),
                nullable=True,
                comment="Статус фоновой обработки транзакции",
            ),
        )
    if "processing_error" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "processing_error",
                sa.Text(),
                nullable=True,
                comment="Причина ошибки или отклонения транзакции",
            ),
        )
    if "processed_at" not in existing_columns:
        op.add_column(
            "transactions",
            sa.Column(
                "processed_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="Время завершения фоновой обработки",
            ),
        )

    op.execute(
        """
        UPDATE transactions
        SET processing_status = CASE
            WHEN is_parsed IS TRUE THEN 'completed'
            ELSE 'queued'
        END,
        processed_at = CASE
            WHEN is_parsed IS TRUE THEN COALESCE(processed_at, created_at)
            ELSE processed_at
        END
        WHERE processing_status IS NULL
        """
    )
    op.alter_column(
        "transactions",
        "processing_status",
        existing_type=sa.String(20),
        nullable=False,
        server_default=sa.text("'queued'"),
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {
        column["name"] for column in inspector.get_columns("transactions")
    }
    if "processed_at" in existing_columns:
        op.drop_column("transactions", "processed_at")
    if "processing_error" in existing_columns:
        op.drop_column("transactions", "processing_error")
    if "processing_status" in existing_columns:
        op.drop_column("transactions", "processing_status")
