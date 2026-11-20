"""Read-only transaction status and history endpoints."""

from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import (
    AccountType,
    TransactionHistoryResponse,
    TransactionStatusResponse,
)


router = APIRouter(prefix="/api", tags=["transactions"])


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _to_response(transaction) -> TransactionStatusResponse:
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


@router.get("/transactions", response_model=TransactionHistoryResponse)
async def get_transaction_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[
        Literal["queued", "completed", "rejected", "failed"]
    ] = None,
    account_type: Optional[AccountType] = None,
    transaction_type: Optional[str] = Query(
        None,
        pattern="^(purchase|payment|transfer|deposit|withdrawal|fee|unknown)$",
    ),
    is_expense: Optional[bool] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Вернуть историю операций с фильтрами и offset-пагинацией."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must not be later than date_to",
        )

    transactions, total = await AsyncORM.get_transaction_history(
        db,
        limit=limit,
        offset=offset,
        processing_status=status,
        account_type=account_type.value if account_type is not None else None,
        transaction_type=transaction_type,
        is_expense=is_expense,
        date_from=date_from,
        date_to=date_to,
    )
    return TransactionHistoryResponse(
        items=[_to_response(transaction) for transaction in transactions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionStatusResponse,
)
async def get_transaction_status(
    transaction_id: int,
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Вернуть статус обработки и распарсенные поля одной транзакции."""
    transaction = await AsyncORM.get_transaction_by_id(db, transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")

    return _to_response(transaction)
