import os
import asyncio
from datetime import date
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app.db.models import Transaction, DailyYield, BudgetLimit
from app.db.async_orm import AsyncORM
from app.config import get_current_rate, get_settings
from datetime import datetime

settings = get_settings()

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
    }
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
                current_month = today.strftime("%Y-%m")

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
                    from app.services.vk_client import VkBotClient
                    import logging

                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(msg)
                    except Exception as e:
                        logging.getLogger(__name__).error(f"VK Budget Report Error: {e}")
                    finally:
                        await vk.close()
        finally:
            await AsyncORM.close()

    asyncio.run(_inner())
