import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import SmsWebhookRequest, SmsWebhookResponse
from app.services.idempotency import build_webhook_idempotency_key

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhook"])


@router.post("/sber-webhook", response_model=SmsWebhookResponse)
async def receive_sms(
    data: SmsWebhookRequest,
    db: AsyncSession = Depends(AsyncORM.get_db),
    idempotency_header: Optional[str] = Header(
        None,
        alias="Idempotency-Key",
        max_length=256,
    ),
):
    """
    Приём СМС от iOS Shortcuts.

    Создаёт запись в БД и ставит задачу на парсинг в Celery.
    Возвращает сразу же с status="queued".
    """
    from app.tasks.celery_worker import process_sms_task

    event_key = build_webhook_idempotency_key(
        data.sms_text,
        provided_key=idempotency_header or data.idempotency_key,
    )
    event, created = await AsyncORM.get_or_create_webhook_event(db, event_key)

    if created:
        transaction = await AsyncORM.create_transaction(db, sms_text=data.sms_text)
        event.transaction_id = transaction.id
        status = "queued"
    else:
        if event.transaction_id is None:
            raise HTTPException(
                status_code=409,
                detail="Webhook event exists without a linked transaction",
            )
        transaction = await AsyncORM.get_transaction_by_id(db, event.transaction_id)
        if transaction is None:
            raise HTTPException(
                status_code=409,
                detail="Webhook event points to a missing transaction",
            )
        status = "duplicate"

    # Транзакция должна стать видимой worker-у до публикации задачи в Redis.
    await db.commit()

    try:
        # Повторная постановка безопасна: worker блокирует запись и проверяет
        # is_parsed перед финансовой обработкой.
        process_sms_task.delay(transaction.id)
    except Exception as exc:
        logger.exception(
            "Не удалось поставить webhook %s в очередь",
            event_key[:12],
        )
        raise HTTPException(
            status_code=503,
            detail="Transaction saved, but background processing is unavailable",
        ) from exc

    logger.info(
        "Webhook %s: status=%s transaction_id=%s",
        event_key[:12],
        status,
        transaction.id,
    )
    return SmsWebhookResponse(status=status, transaction_id=transaction.id)
