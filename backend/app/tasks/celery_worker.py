import os
import asyncio
from datetime import date
from celery import Celery
from celery.schedules import crontab
from sqlalchemy import select

from app.db.models import Transaction, DailyYield
from app.db.async_orm import AsyncORM
from app.services.yield_calculator import calculate_daily_yield
from app.config import get_current_rate, get_settings

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
    }
}
celery_app.conf.timezone = "Europe/Moscow"

@celery_app.task(name="app.tasks.celery_worker.calculate_and_store_yield")
def calculate_and_store_yield():
    async def _inner():
        async with AsyncORM.get_session() as session:
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
            earned = calculate_daily_yield(balance)

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

    asyncio.run(_inner())
