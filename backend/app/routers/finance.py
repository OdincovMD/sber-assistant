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
from app.schemas import FinancialSummaryResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/finance", tags=["finance"])

settings = get_settings()


@router.get("/summary", response_model=FinancialSummaryResponse)
async def get_summary(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Полная финансовая сводка."""
    return await CreditCardService.get_financial_summary(db)


@router.get("/limit")
async def get_limit(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Доступный кредитный лимит и статус."""
    available = await CreditCardService.get_available_limit(db)
    total_unpaid = await AsyncORM.get_total_unpaid_expenses(db)
    return {
        "available_limit": float(available),
        "credit_limit": float(settings.credit_limit),
        "total_unpaid": float(total_unpaid),
        "usage_percent": round(float(total_unpaid) / settings.credit_limit * 100, 1) if settings.credit_limit > 0 else 0,
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


@router.get("/budgets")
async def get_budgets(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Получить все установленные бюджеты по категориям."""
    from sqlalchemy import select
    from app.db.models import BudgetLimit

    result = await db.execute(
        select(BudgetLimit).where(BudgetLimit.is_active == True)
    )
    budgets = result.scalars().all()

    return [
        {
            "id": b.id,
            "category": b.category,
            "monthly_limit": float(b.monthly_limit),
        }
        for b in budgets
    ] if budgets else []


@router.post("/budgets/{category}")
async def set_budget(category: str, limit: float, db: AsyncSession = Depends(AsyncORM.get_db)):
    """Установить месячный бюджет для категории расходов."""
    from decimal import Decimal
    budget = await AsyncORM.set_budget_limit(db, category, Decimal(str(limit)))
    await db.commit()
    return {
        "success": True,
        "category": budget.category,
        "monthly_limit": float(budget.monthly_limit),
    }


@router.get("/spending/by-category")
async def get_spending_by_category(db: AsyncSession = Depends(AsyncORM.get_db)):
    """Распределение расходов по категориям за текущий месяц."""
    from datetime import date as date_type
    from sqlalchemy import select, func
    from app.db.models import Transaction

    current_month = date_type.today().replace(day=1)

    result = await db.execute(
        select(
            Transaction.category,
            func.sum(func.abs(Transaction.amount)).label("total")
        )
        .where(Transaction.is_expense == True)
        .where(Transaction.is_parsed == True)
        .where(Transaction.account_type == "debit")
        .where(func.date_trunc('month', Transaction.created_at) == current_month)
        .group_by(Transaction.category)
        .order_by(func.sum(func.abs(Transaction.amount)).desc())
    )

    rows = result.all()
    return [
        {
            "category": row[0] or "Неизвестно",
            "spent": float(row[1]) if row[1] else 0,
        }
        for row in rows
    ]
