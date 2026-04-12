import os
import asyncio
import logging
from datetime import date
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app.db.models import Transaction, DailyYield, BudgetLimit, TransactionType
from app.db.async_orm import AsyncORM
from app.config import get_current_rate, get_settings
from app.services.ollama_client import ollama_client
from app.services.credit_logic import CreditCardService
from app.services.vk_client import VkBotClient
from app.services.analyst_service import FinancialAnalyst
from datetime import datetime

logger = logging.getLogger(__name__)

settings = get_settings()

# Маппинг строки type → enum
TYPE_MAPPING = {
    "purchase": TransactionType.PURCHASE,
    "payment": TransactionType.PAYMENT,
    "transfer": TransactionType.TRANSFER,
    "deposit": TransactionType.DEPOSIT,
    "withdrawal": TransactionType.WITHDRAWAL,
    "fee": TransactionType.FEE,
}

celery_app = Celery(
    "sber_assistant",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.beat_schedule = {
    "daily-yield-task": {
        "task": "app.tasks.celery_worker.calculate_and_store_yield",
        "schedule": crontab(minute=55, hour=23),  # 23:55 Moscow time
    },
    "weekly-budget-report": {
        "task": "app.tasks.celery_worker.send_weekly_budget_report",
        "schedule": crontab(minute=0, hour=20, day_of_week=6),  # Sunday 20:00 Moscow time
    },
    "ai-advice-task": {
        "task": "app.tasks.celery_worker.send_ai_advice",
        "schedule": crontab(minute=0, hour=19, day_of_week=5),  # Friday 19:00 Moscow time
    },
    # ─── Инвестиционные задачи ───────────────────────────────────
    "fetch-moex-prices": {
        "task": "app.tasks.celery_worker.fetch_investment_prices",
        "schedule": crontab(minute=5, hour=19),   # 19:05 после закрытия MOEX (18:50)
    },
    "check-ldv-alerts": {
        "task": "app.tasks.celery_worker.check_ldv_alerts",
        "schedule": crontab(minute=0, hour=8),    # 08:00 утром
    },
}
celery_app.conf.timezone = "Europe/Moscow"

@celery_app.task(name="app.tasks.celery_worker.calculate_and_store_yield")
def calculate_and_store_yield():
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                stmt = (
                    select(Transaction)
                    .where(Transaction.account_type == "savings")
                    .order_by(Transaction.created_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                tx = result.scalar_one_or_none()
                if not tx or tx.balance_after is None:
                    return

                balance = tx.balance_after
                rate = get_current_rate()
                earned = DailyYield.calculate_daily_yield(balance)

                today = date.today()
                existing = await session.get(DailyYield, today)
                if existing:
                    existing.end_of_day_balance = balance
                    existing.applied_rate = rate
                    existing.earned_amount = earned
                else:
                    session.add(
                        DailyYield(
                            date=today,
                            account_tail="1837",
                            end_of_day_balance=balance,
                            applied_rate=rate,
                            earned_amount=earned,
                        )
                    )
                await session.commit()

                if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                    from app.services.vk_client import VkBotClient
                    import logging
                    msg = (
                        f"ФИНАНСОВЫЙ ДАЙДЖЕСТ: НАКОПИТЕЛЬНЫЙ СЧЕТ\n"
                        f"Счет: *1837\n"
                        f"Примененная ставка: {rate}% годовых\n\n"
                        f"Доход за сегодня: +{earned} руб.\n"
                        f"Текущий баланс: {balance} руб."
                    )
                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(msg)
                    except Exception as e:
                        logging.getLogger(__name__).error(f"VK Celery Error: {e}")
                    finally:
                        await vk.close()
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())


@celery_app.task(name="app.tasks.celery_worker.send_weekly_budget_report")
def send_weekly_budget_report():
    """Weekly digest of budget limits and spending by category."""
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                # Get all active budgets
                from sqlalchemy import select

                stmt = select(BudgetLimit).where(BudgetLimit.is_active == True)
                result = await session.execute(stmt)
                budgets = result.scalars().all()

                if not budgets:
                    return

                # Build report
                today = date.today()
                current_month = today.replace(day=1)

                lines = [
                    "BUDGET REPORT (CURRENT MONTH)",
                    ""
                ]

                for budget in budgets:
                    spent = await AsyncORM.get_month_category_expenses(session, budget.category, current_month)
                    spent_float = float(spent) if spent else 0.0
                    remaining = float(budget.monthly_limit) - spent_float

                    lines.append(f"Category: {budget.category}")
                    lines.append(f"Limit: {int(budget.monthly_limit)} RUB")
                    lines.append(f"Spent: {int(spent_float)} RUB")
                    lines.append(f"Remaining: {int(remaining)} RUB")
                    lines.append("-----------------------------------")

                msg = "\n".join(lines)

                if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(msg)
                    except Exception as e:
                        logger.error(f"VK Budget Report Error: {e}")
                    finally:
                        await vk.close()
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())


@celery_app.task(name="app.tasks.celery_worker.process_sms_task")
def process_sms_task(transaction_id: int):
    """
    Background SMS processing task: parse SMS, update transaction, process financial logic, send VK notification.
    """
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                # 1. Fetch the raw transaction by ID
                transaction = await AsyncORM.get_transaction_by_id(session, transaction_id)
                if not transaction:
                    logger.warning(f"Transaction {transaction_id} not found")
                    return

                logger.info(f"Processing SMS for transaction {transaction_id}: {transaction.sms_text}")

                # 2. Parse via Ollama
                parsed_result, raw_response = await ollama_client.parse_sms(transaction.sms_text)

                # 3. Update DB with parse results
                if parsed_result:
                    # Determine grace safety
                    is_grace_safe = True
                    if parsed_result.account_type == "credit":
                        if parsed_result.category in ["Снятие наличных", "Перевод между счетами", "Комиссия"]:
                            is_grace_safe = False

                    await AsyncORM.update_transaction_parsed(
                        session,
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
                        f"расход={parsed_result.is_expense} | грейс={is_grace_safe} | "
                        f"баланс={parsed_result.balance_after}"
                    )

                    # 4. Financial logic
                    process_result = await CreditCardService.process_transaction(
                        session,
                        transaction_id=transaction.id,
                        amount=parsed_result.amount,
                        is_expense=parsed_result.is_expense,
                        is_grace_safe=is_grace_safe,
                        merchant=parsed_result.merchant,
                        transaction_type=parsed_result.type or "unknown",
                    )

                    if process_result.get("errors"):
                        for error in process_result["errors"]:
                            logger.error(f"Ошибка обработки: {error}")

                    if process_result.get("warnings"):
                        for warning in process_result["warnings"]:
                            logger.warning(f"Предупреждение: {warning}")

                    # Сохраняем все изменения в БД до отправки уведомлений
                    await session.commit()

                    # 5. VK notification (strict banking-style, no emojis)
                    try:
                        if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                            acc_type_str = str(parsed_result.account_type).split(".")[-1].lower() if parsed_result.account_type else "unknown"
                            acc_names = {"credit": "credit", "debit": "debit", "savings": "savings"}
                            acc_en = acc_names.get(acc_type_str, "account")

                            stats = await CreditCardService.get_spending_statistics(session=session, account_type=acc_type_str if acc_type_str != "unknown" else None)
                            bonus = await CreditCardService.get_bonus_target_status(session=session)

                            pct = float(bonus['progress_percent'])
                            remaining = bonus.get('remaining', 0.0)
                            available_limit = await CreditCardService.get_available_limit(session=session)

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

                                if process_result.get("warnings") or process_result.get("errors"):
                                    msg_lines.append("")
                                    msg_lines.append("[ SYSTEM NOTIFICATIONS ]")

                                    if process_result.get("warnings"):
                                        for warning in process_result["warnings"]:
                                            msg_lines.append(f"- {warning}")

                                    if process_result.get("errors"):
                                        for error in process_result["errors"]:
                                            msg_lines.append(f"- {error}")

                            vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                            try:
                                await vk.send_message("\n".join(msg_lines))
                            except Exception as e:
                                logger.error(f"VK Send Error: {e}")
                            finally:
                                await vk.close()
                    except Exception as e:
                        logger.error(f"Ошибка формирования уведомления для VK: {e}")

                else:
                    await AsyncORM.update_transaction_parsed(
                        session,
                        transaction,
                        raw_llm_response=raw_response,
                        is_parsed=False,
                    )
                    await session.commit()
                    logger.warning(f"Парсинг FAILED для транзакции {transaction_id} — не удалось извлечь данные из ответа LLM")

        finally:
            await AsyncORM.close()

    asyncio.run(_inner())


@celery_app.task(name="app.tasks.celery_worker.fetch_investment_prices")
def fetch_investment_prices():
    """
    Ежедневно в 19:05 — забирает цены биржевых БПИФ с MOEX ISS и сохраняет в БД.
    Запускается после закрытия основной торговой сессии MOEX (18:50 МСК).
    ПИФ_НАК — обновляется вручную через POST /api/investment/price.
    """
    async def _inner():
        from app.services.moex_client import MOEXClient, MOEX_TICKERS

        await AsyncORM.init()
        try:
            prices = await MOEXClient.get_prices_bulk(MOEX_TICKERS)
            today  = date.today()
            updated = []
            failed  = []

            async with AsyncORM.get_session()() as session:
                for ticker, price in prices.items():
                    if price is not None:
                        await AsyncORM.upsert_investment_price(
                            session, ticker=ticker, price_date=today,
                            price=price, source="moex",
                        )
                        updated.append(f"{ticker}={price}")
                    else:
                        failed.append(ticker)
                await session.commit()

            if updated:
                logger.info(f"MOEX prices updated: {', '.join(updated)}")
            if failed:
                logger.warning(f"MOEX prices failed: {', '.join(failed)}")

        except Exception as e:
            logger.error(f"fetch_investment_prices error: {e}")
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())


@celery_app.task(name="app.tasks.celery_worker.check_ldv_alerts")
def check_ldv_alerts():
    """
    Ежедневно в 08:00 — проверяет приближающиеся даты ЛДВ и отправляет VK-алерт.

    Алерт отправляется при:
    - 90 дней до ЛДВ (первое предупреждение)
    - 30 дней до ЛДВ (срочное)
    - В день наступления ЛДВ (льгота доступна)
    """
    async def _inner():
        from app.services.investment_service import InvestmentService, LDV_WARN_DAYS, LDV_CRITICAL_DAYS

        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                calendar = await InvestmentService.get_ldv_calendar(session)

            # Фильтруем только лоты, требующие уведомления сегодня
            alerts = []
            for entry in calendar:
                days = entry["days_to_ldv"]
                if days in (0, LDV_CRITICAL_DAYS, LDV_WARN_DAYS):
                    alerts.append(entry)

            if not alerts:
                return

            lines = ["[ ЛДВ УВЕДОМЛЕНИЕ ]", ""]
            for a in alerts:
                lines.append(a["message"])

            msg = "\n".join(lines)
            logger.info(f"ЛДВ алерт: {len(alerts)} лотов")

            if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                try:
                    await vk.send_message(msg)
                except Exception as e:
                    logger.error(f"VK LDV alert error: {e}")
                finally:
                    await vk.close()

        except Exception as e:
            logger.error(f"check_ldv_alerts error: {e}")
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())


@celery_app.task(name="app.tasks.celery_worker.send_ai_advice")
def send_ai_advice():
    """
    Генерирует AI-совет на основе текущей финансовой сводки и отправляет через VK.
    Запускается по расписанию: каждую пятницу в 19:00 Moscow time.
    """
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                # 1. Get financial summary
                summary = await CreditCardService.get_financial_summary(session)

                # 2. Generate advice using AI analyst
                analyst = FinancialAnalyst()
                advice_text = await analyst.generate_advice(summary)

                # 3. Prepend header
                full_message = "[ СОВЕТ ОТ ИИ-АНАЛИТИКА ]\n\n" + advice_text

                # 4. Send via VK if configured
                if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(full_message)
                        logger.info("AI advice sent successfully to VK")
                    except Exception as e:
                        logger.error(f"VK Send Error (AI Advice): {e}")
                    finally:
                        await vk.close()
                else:
                    logger.info("VK not configured, AI advice not sent")
        except Exception as e:
            logger.error(f"Error in send_ai_advice task: {e}")
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())
