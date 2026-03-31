import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM, TransactionType
from app.schemas import SmsWebhookRequest, SmsWebhookResponse
import asyncio
from app.services.ollama_client import ollama_client
from app.config import get_settings
from app.db.models import DailyYield
from app.services.credit_logic import CreditCardService
from app.services.vk_client import VkBotClient

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
        # Определяем is_grace_safe
        is_grace_safe = True
        if parsed_result.account_type == "credit":
            if parsed_result.category in ["Снятие наличных", "Перевод между счетами", "Комиссия"]:
                is_grace_safe = False

        await AsyncORM.update_transaction_parsed(
            db,
            transaction,
            card_tail=parsed_result.card_tail,
            account_type=parsed_result.account_type,
            amount=parsed_result.amount,
            transaction_type=TYPE_MAPPING.get(parsed_result.type, TransactionType.UNKNOWN),
            merchant=parsed_result.merchant,
            category=parsed_result.category,
            is_grace_safe=is_grace_safe,
            is_expense=parsed_result.is_expense,
            balance_after=parsed_result.balance_after,
            card=parsed_result.card,
            raw_llm_response=raw_response,
            is_parsed=True,
        )

        expense_label = "EXPENSE" if parsed_result.is_expense else "INCOME"
        logger.info(
            f"[{expense_label}] Парсинг OK: {parsed_result.amount}₽ | "
            f"карта={parsed_result.card_tail} ({parsed_result.account_type}) | "
            f"тип={parsed_result.type} | мерчант={parsed_result.merchant} | "
            f"категория={parsed_result.category} | "
            f"расход={parsed_result.is_expense} | грейс={parsed_result.is_grace_safe} | "
            f"баланс={parsed_result.balance_after}"
        )

        # 4. Финансовая логика — привязка к BillingPeriod
        period = await CreditCardService.process_transaction(
            db,
            transaction_id=transaction.id,
            amount=parsed_result.amount,
            is_expense=parsed_result.is_expense,
            is_grace_safe=is_grace_safe,
            merchant=parsed_result.merchant,
            transaction_type=parsed_result.type or "unknown",
        )

        if period:
            grace_deadline_str = period.grace_deadline.isoformat()
            billing_month_str = period.month.strftime("%Y-%m")

        # 5. Интеграция с ВКонтакте
        try:
            settings = get_settings()
            if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                acc_type_str = str(parsed_result.account_type).split(".")[-1].lower() if parsed_result.account_type else "unknown"
                acc_names = {"credit": "credit", "debit": "debit", "savings": "savings"}
                acc_en = acc_names.get(acc_type_str, "account")

                stats = await CreditCardService.get_spending_statistics(db, account_type=acc_type_str if acc_type_str != "unknown" else None)
                bonus = await CreditCardService.get_bonus_target_status(db)
                
                pct = float(bonus['progress_percent'])
                remaining = bonus.get('remaining', 0.0)
                available_limit = await CreditCardService.get_available_limit(db)

                def fmt_amt(val):
                    val = abs(float(val)) if val is not None else 0.0
                    return f"{int(val)}" if val % 1 == 0 else f"{val:.2f}"

                amt_str = fmt_amt(parsed_result.amount)
                merch_str = parsed_result.merchant if parsed_result.merchant else "Не указан"

                if is_grace_safe is False and parsed_result.is_expense:
                    msg_lines = [
                        "[ КРИТИЧЕСКОЕ ПРЕДУПРЕЖДЕНИЕ ]",
                        f"Счет: {parsed_result.card_tail} ({acc_en})",
                        f"Сумма: {amt_str} руб.",
                        f"Детали: {merch_str} ({parsed_result.category})",
                        "",
                        "ВНИМАНИЕ: Зафиксирована операция (снятие/перевод), нарушающая льготный период! Проценты по карте могут быть начислены."
                    ]
                else:
                    msg_lines = [
                        "ОПЕРАЦИЯ ПО СЧЕТУ",
                        f"Счет: {parsed_result.card_tail} ({acc_en})",
                        f"Сумма: {amt_str} руб.",
                        f"Детали: {merch_str} ({parsed_result.category})",
                        "-----------------------------------",
                        f"Доступный лимит: {fmt_amt(available_limit)} руб.",
                        f"ЦЕЛЬ 100К: {pct}% (остаток: {fmt_amt(remaining)} руб.)",
                        "",
                        "РАСХОДЫ (без переводов):",
                        f"День: {fmt_amt(stats['daily'])} руб.",
                        f"Неделя: {fmt_amt(stats['weekly'])} руб.",
                        f"Месяц: {fmt_amt(stats['monthly'])} руб."
                    ]

                vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)

                async def send_and_close():
                    try:
                        await vk.send_message("\n".join(msg_lines))
                    except Exception as e:
                        logger.error(f"VK Send Error: {e}")
                    finally:
                        await vk.close()

                asyncio.create_task(send_and_close())
        except Exception as e:
            logger.error(f"Ошибка формирования уведомления для VK: {e}")

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
