"""Read-only transaction processing status endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import TransactionStatusResponse


router = APIRouter(prefix="/api/transactions", tags=["transactions"])


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@router.get("/{transaction_id}", response_model=TransactionStatusResponse)
async def get_transaction_status(
    transaction_id: int,
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Вернуть статус обработки и распарсенные поля одной транзакции."""
    transaction = await AsyncORM.get_transaction_by_id(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return TransactionStatusResponse(
        id=transaction.id,
        status=transaction.processing_status,
        created_at=transaction.created_at,
        processed_at=transaction.processed_at,
        account_type=_enum_value(transaction.account_type),
        amount=(
            float(transaction.amount)
            if transaction.amount is not None
            else None
        ),
        transaction_type=_enum_value(transaction.transaction_type),
        merchant=transaction.merchant,
        category=transaction.category,
        is_expense=transaction.is_expense,
        is_grace_safe=transaction.is_grace_safe,
        billing_period_id=transaction.billing_period_id,
        grace_deadline=transaction.grace_deadline,
        error=transaction.processing_error,
    )
