"""Transaction status, history, and recovery endpoints."""

import logging
from datetime import date
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import (
    AccountType,
    BulkTransactionRetryResponse,
    TransactionHistoryResponse,
    TransactionProcessingSummaryResponse,
    TransactionRetryResponse,
    TransactionStatusResponse,
)
from app.services.processing_health import (
    determine_processing_health,
    get_queue_stale_minutes,
)
from app.services.transaction_retry import (
    mark_retry_queue_failure,
    prepare_transaction_for_retry,
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
        failure_notified_at=transaction.failure_notified_at,
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
    "/transactions/summary",
    response_model=TransactionProcessingSummaryResponse,
)
async def get_transaction_processing_summary(
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Вернуть одно агрегированное состояние очереди и ошибок обработки."""
    generated_at = datetime.now(timezone.utc)
    stale_after_minutes = get_queue_stale_minutes()
    summary = await AsyncORM.get_transaction_processing_summary(
        db,
        stale_before=generated_at - timedelta(minutes=stale_after_minutes),
    )
    health = determine_processing_health(
        queued=summary["queued"],
        stale_queued=summary["stale_queued"],
        failed=summary["failed"],
        unnotified_failures=summary["unnotified_failures"],
    )
    return TransactionProcessingSummaryResponse(
        health=health,
        generated_at=generated_at,
        stale_after_minutes=stale_after_minutes,
        **summary,
    )


@router.post(
    "/transactions/retry-failed",
    response_model=BulkTransactionRetryResponse,
    status_code=202,
)
async def retry_failed_transactions(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(AsyncORM.get_db),
):
    """Повторно поставить ограниченную пачку failed-транзакций в очередь."""
    from app.tasks.celery_worker import process_sms_task

    transactions = await AsyncORM.claim_failed_transactions_for_retry(
        db,
        limit=limit,
    )
    if not transactions:
        return BulkTransactionRetryResponse(
            status="empty",
            selected_count=0,
            queued_count=0,
            failed_count=0,
            queued_ids=[],
            failed_ids=[],
        )

    queued_at = datetime.now(timezone.utc)
    transaction_ids = [transaction.id for transaction in transactions]
    for transaction in transactions:
        prepare_transaction_for_retry(transaction, queued_at=queued_at)

    # Watchdog восстановит строки, если процесс завершится между commit и delay.
    await db.commit()

    queued_ids: list[int] = []
    failed: dict[int, str] = {}
    for transaction_id in transaction_ids:
        try:
            process_sms_task.delay(transaction_id)
            queued_ids.append(transaction_id)
        except Exception as exc:
            logger.exception(
                "Could not requeue transaction %s in bulk retry",
                transaction_id,
            )
            failed[transaction_id] = type(exc).__name__

    failed_ids = list(failed)
    if failed_ids:
        failed_at = datetime.now(timezone.utc)
        failed_transactions = await AsyncORM.get_transactions_by_ids_for_update(
            db,
            failed_ids,
        )
        for transaction in failed_transactions:
            # Старое Celery-сообщение могло успеть обработать строку, пока
            # новая публикация падала. Не перезаписываем такой результат.
            if transaction.processing_status != "queued" or transaction.is_parsed:
                continue
            mark_retry_queue_failure(
                transaction,
                failed_at=failed_at,
                error_type=failed[transaction.id],
            )
        await db.commit()

    return BulkTransactionRetryResponse(
        status="partial" if failed_ids else "queued",
        selected_count=len(transaction_ids),
        queued_count=len(queued_ids),
        failed_count=len(failed_ids),
        queued_ids=queued_ids,
        failed_ids=failed_ids,
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

    prepare_transaction_for_retry(
        transaction,
        queued_at=datetime.now(timezone.utc),
    )

    # Worker должен увидеть статус queued до публикации задачи в Redis.
    await db.commit()

    try:
        process_sms_task.delay(transaction.id)
    except Exception as exc:
        logger.exception(
            "Could not requeue transaction %s",
            transaction.id,
        )
        mark_retry_queue_failure(
            transaction,
            failed_at=datetime.now(timezone.utc),
            error_type=type(exc).__name__,
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="Transaction remains failed because background processing is unavailable",
        ) from exc

    return TransactionRetryResponse(transaction_id=transaction.id)
