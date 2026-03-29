import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM, TransactionType
from app.schemas import SmsWebhookRequest, SmsWebhookResponse
from app.services.ollama_client import ollama_client
from app.services.credit_logic import CreditCardService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["webhook"])

# Маппинг строки type → enum
TYPE_MAPPING = {
    "purchase": TransactionType.PURCHASE,
    "payment": TransactionType.PAYMENT,
    "transfer": TransactionType.TRANSFER,
    "deposit": TransactionType.DEPOSIT,
    "withdrawal": TransactionType.WITHDRAWAL,
    "fee": TransactionType.FEE,
}


@router.post("/sber-webhook", response_model=SmsWebhookResponse)
async def receive_sms(data: SmsWebhookRequest, db: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Приём СМС от iOS Shortcuts.

    1. Сохраняет сырой текст в БД
    2. Отправляет в Ollama для парсинга
    3. Обновляет запись результатами парсинга
    4. Обрабатывает финансовую логику (BillingPeriod, grace, alarm)
    """
    logger.info(f"--- НОВОЕ СООБЩЕНИЕ ОТ APPLE SHORTCUTS ---")
    logger.info(f"Текст: {data.sms_text}")

    # 1. Создаём запись в БД с сырым текстом
    transaction = await AsyncORM.create_transaction(db, sms_text=data.sms_text)

    # 2. Отправляем в Ollama
    parsed_result, raw_response = await ollama_client.parse_sms(data.sms_text)

    grace_deadline_str = None
    billing_month_str = None

    # 3. Обновляем запись результатами парсинга
    if parsed_result:
        await AsyncORM.update_transaction_parsed(
            db,
            transaction,
            amount=parsed_result.amount,
            transaction_type=TYPE_MAPPING.get(parsed_result.type, TransactionType.UNKNOWN),
            merchant=parsed_result.merchant,
            is_grace_safe=parsed_result.is_grace_safe,
            is_expense=parsed_result.is_expense,
            balance_after=parsed_result.balance_after,
            card=parsed_result.card,
            raw_llm_response=raw_response,
            is_parsed=True,
        )

        expense_label = "EXPENSE" if parsed_result.is_expense else "INCOME"
        logger.info(
            f"[{expense_label}] Парсинг OK: {parsed_result.amount}₽ | "
            f"тип={parsed_result.type} | мерчант={parsed_result.merchant} | "
            f"расход={parsed_result.is_expense} | грейс={parsed_result.is_grace_safe} | "
            f"баланс={parsed_result.balance_after} | карта={parsed_result.card}"
        )

        # 4. Финансовая логика — привязка к BillingPeriod
        period = await CreditCardService.process_transaction(
            db,
            transaction_id=transaction.id,
            amount=parsed_result.amount,
            is_expense=parsed_result.is_expense,
            is_grace_safe=parsed_result.is_grace_safe,
            merchant=parsed_result.merchant,
            transaction_type=parsed_result.type or "unknown",
        )

        if period:
            grace_deadline_str = period.grace_deadline.isoformat()
            billing_month_str = period.month.strftime("%Y-%m")

    else:
        await AsyncORM.update_transaction_parsed(
            db,
            transaction,
            raw_llm_response=raw_response,
            is_parsed=False,
        )
        logger.warning("Парсинг FAILED — не удалось извлечь данные из ответа LLM")

    return SmsWebhookResponse(
        status="received" if parsed_result else "parse_error",
        transaction_id=transaction.id,
        parsed_data=parsed_result,
        grace_deadline=grace_deadline_str,
        billing_month=billing_month_str,
    )
