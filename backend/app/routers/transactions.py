"""Transaction status, history, and recovery endpoints."""

import logging
from datetime import date
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import (
    AccountType,
    TransactionHistoryResponse,
    TransactionRetryResponse,
    TransactionStatusResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["transactions"])


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def _to_response(transaction) -> TransactionStatusResponse:
    return TransactionStatusResponse(
        id=transaction.id,
        status=transaction.processing_status,
        created_at=transaction.created_at,
        queued_at=transaction.queued_at,
        queue_recovery_attempts=transaction.queue_recovery_attempts,
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


@router.post(
    "/transactions/{transaction_id}/retry",
    response_model=TransactionRetryResponse,
    status_code=202,
)
async def retry_failed_transaction(
    transaction_id: int,
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Повторно поставить упавшую транзакцию в очередь обработки."""
    from app.tasks.celery_worker import process_sms_task

    transaction = await AsyncORM.get_transaction_by_id_for_update(
        db,
        transaction_id,
    )
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if transaction.processing_status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Only failed transactions can be retried",
        )

    transaction.processing_status = "queued"
    transaction.processing_error = None
    transaction.processed_at = None
    transaction.queued_at = datetime.now(timezone.utc)
    transaction.queue_recovery_attempts = 0

    # Worker должен увидеть статус queued до публикации задачи в Redis.
    await db.commit()

    try:
        process_sms_task.delay(transaction.id)
    except Exception as exc:
        logger.exception(
            "Could not requeue transaction %s",
            transaction.id,
        )
        transaction.processing_status = "failed"
        transaction.processing_error = (
            "Background queue unavailable: "
            f"{type(exc).__name__}"
        )
        transaction.processed_at = datetime.now(timezone.utc)
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="Transaction remains failed because background processing is unavailable",
        ) from exc

    return TransactionRetryResponse(transaction_id=transaction.id)
