import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.schemas import SmsWebhookRequest, SmsWebhookResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhook"])


@router.post("/sber-webhook", response_model=SmsWebhookResponse)
async def receive_sms(data: SmsWebhookRequest, db: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Приём СМС от iOS Shortcuts.

    Создаёт запись в БД и ставит задачу на парсинг в Celery.
    Возвращает сразу же с status="queued".
    """
    from app.tasks.celery_worker import process_sms_task

    logger.info(f"--- НОВОЕ СООБЩЕНИЕ ОТ APPLE SHORTCUTS ---")
    logger.info(f"Текст: {data.sms_text}")

    # 1. Создаём запись в БД с сырым текстом
    transaction = await AsyncORM.create_transaction(db, sms_text=data.sms_text)

    # 2. Ставим задачу на фоновый парсинг
    process_sms_task.delay(transaction.id)

    return SmsWebhookResponse(status="queued", transaction_id=transaction.id)
