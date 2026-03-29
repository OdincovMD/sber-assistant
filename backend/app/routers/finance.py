"""
Роутер для финансовых данных и статистики.

Эндпоинты:
- GET /api/finance/summary — полная финансовая сводка
- GET /api/finance/limit — доступный кредитный лимит
- GET /api/finance/bonus — статус бонусного порога
- GET /api/finance/periods — список открытых периодов
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.config import get_settings
from app.services.credit_logic import CreditCardService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/finance", tags=["finance"])

settings = get_settings()


@router.get("/summary")
async def get_summary(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Полная финансовая сводка."""
    return await CreditCardService.get_financial_summary(db)


@router.get("/limit")
async def get_limit(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Доступный кредитный лимит."""
    available = await CreditCardService.get_available_limit(db)
    return {
        "available_limit": float(available),
        "credit_limit": settings.credit_limit,
    }


@router.get("/bonus")
async def get_bonus(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Статус бонусного порога."""
    return await CreditCardService.get_bonus_target_status(db)


@router.get("/periods")
async def get_periods(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Все открытые (незакрытые) биллинг-периоды."""
    periods = await AsyncORM.get_open_billing_periods(db)
    from datetime import date as date_type

    return [
        {
            "id": p.id,
            "month": p.month.strftime("%Y-%m"),
            "total_spent": float(p.total_spent) if p.total_spent else 0,
            "grace_deadline": p.grace_deadline.isoformat(),
            "days_left": (p.grace_deadline - date_type.today()).days,
            "is_closed": p.is_closed,
        }
        for p in periods
    ]
