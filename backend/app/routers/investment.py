"""
Investment router — API для инвестиционного портфеля.

Эндпоинты:
    GET  /api/investment/portfolio    — P&L по всем лотам
    GET  /api/investment/ldv          — ЛДВ-календарь
    GET  /api/investment/liquidation  — ликвидность для grace period
    GET  /api/investment/compare      — SBMM vs накопительный счёт
    GET  /api/investment/cbr          — ключевая ставка и расписание ЦБ
    POST /api/investment/lots         — добавить новый лот
    POST /api/investment/price        — обновить цену вручную (для ПИФ)
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import AsyncORM
from app.db.models import FundType
from app.services.investment_service import InvestmentService
from app.services.cbr_client import CBRClient
from app.config import INVESTMENT_FUNDS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/investment", tags=["Investment"])


# ─── Схемы запросов ─────────────────────────────────────────────────────────

class AddLotRequest(BaseModel):
    ticker:         str             = Field(..., description="Тикер: SBGB, SBGD, SBFR, SBMM, PIF_NAK")
    quantity:       float           = Field(..., gt=0, description="Количество паёв")
    purchase_price: float           = Field(..., gt=0, description="Цена покупки за 1 пай")
    purchase_date:  date            = Field(..., description="Дата покупки")
    isin:           Optional[str]   = Field(None, description="ISIN (опционально)")
    fund_name:      Optional[str]   = Field(None, description="Название фонда (опционально)")


class ManualPriceRequest(BaseModel):
    ticker:     str   = Field(..., description="Тикер инструмента")
    price:      float = Field(..., gt=0, description="Текущая цена за 1 пай")
    price_date: date  = Field(default_factory=date.today, description="Дата цены")


# ─── Эндпоинты ──────────────────────────────────────────────────────────────

@router.get("/portfolio", summary="P&L по всем лотам портфеля")
async def get_portfolio(session: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Текущая стоимость портфеля, P&L и потенциальный налог по каждому лоту.

    Цены берутся из последних сохранённых значений (обновляются ежедневно Celery).
    Если цена недоступна (нет данных), поля `current_value` и `pnl` будут null.
    """
    return await InvestmentService.get_portfolio_summary(session)


@router.get("/ldv", summary="ЛДВ-календарь (льгота долгосрочного владения)")
async def get_ldv_calendar(session: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Отсортированный список лотов с датами наступления ЛДВ (3 года с покупки).

    alert_level:
    - `ok`         — более 90 дней
    - `warn_90d`   — менее 90 дней
    - `warn_30d`   — менее 30 дней (не рекомендуется продавать)
    - `available`  — льгота уже доступна
    """
    return await InvestmentService.get_ldv_calendar(session)


@router.get("/liquidation", summary="Ликвидность портфеля для погашения grace period")
async def get_liquidation(session: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Анализ: хватит ли ликвидных активов для погашения ближайшего grace period.

    - T+1: биржевые БПИФ (SBGB, SBGD, SBFR, SBMM) — деньги на след. день
    - T+4: открытый ПИФ (PIF_NAK) — деньги через 3-5 рабочих дней
    """
    return await InvestmentService.get_liquidity_for_grace(session)


@router.get("/compare", summary="SBMM vs накопительный счёт")
async def compare_instruments(session: AsyncSession = Depends(AsyncORM.get_db)):
    """
    Сравнение доходности SBMM и накопительного счёта *1837.

    SBMM отслеживает RUONIA (ключевая ставка ЦБ) минус TER фонда (0.30%).
    Накопительный счёт — фиксированная ставка из конфига SAVINGS_RATES.
    """
    return await InvestmentService.compare_sbmm_vs_savings(session)


@router.get("/cbr", summary="Ключевая ставка ЦБ и расписание заседаний")
async def get_cbr_info():
    """
    Текущая ключевая ставка ЦБ, RUONIA и ближайшие даты заседаний Совета директоров.
    Критично для прогнозирования доходности SBMM, SBFR, SBGB.
    """
    return await CBRClient.get_summary()


@router.post("/lots", summary="Добавить новый лот покупки", status_code=201)
async def add_lot(
    req: AddLotRequest,
    session: AsyncSession = Depends(AsyncORM.get_db),
):
    """
    Добавить новый лот покупки вручную.

    Используется при новых покупках. ЛДВ рассчитывается автоматически.
    """
    if req.ticker not in INVESTMENT_FUNDS:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный тикер: {req.ticker}. Доступны: {list(INVESTMENT_FUNDS.keys())}",
        )

    fund_meta = INVESTMENT_FUNDS[req.ticker]
    ldv_date  = req.purchase_date.replace(year=req.purchase_date.year + 3)

    lot = await AsyncORM.create_investment_lot(
        session,
        ticker=req.ticker,
        isin=req.isin or fund_meta.get("isin"),
        fund_name=req.fund_name or fund_meta["name"],
        fund_type=FundType(fund_meta["fund_type"]),
        quantity=Decimal(str(req.quantity)),
        purchase_price=Decimal(str(req.purchase_price)),
        purchase_date=req.purchase_date,
        ldv_date=ldv_date,
    )

    logger.info(
        f"Новый лот: {req.ticker} × {req.quantity} @ {req.purchase_price} "
        f"({req.purchase_date}) | ЛДВ: {ldv_date}"
    )

    return {
        "id":             lot.id,
        "ticker":         lot.ticker,
        "quantity":       float(lot.quantity),
        "purchase_price": float(lot.purchase_price),
        "purchase_date":  lot.purchase_date.isoformat(),
        "ldv_date":       lot.ldv_date.isoformat(),
        "invested":       float(lot.quantity * lot.purchase_price),
    }


@router.post("/price", summary="Обновить цену вручную (для ПИФ Накопительного)")
async def set_manual_price(
    req: ManualPriceRequest,
    session: AsyncSession = Depends(AsyncORM.get_db),
):
    """
    Установить цену вручную.

    Необходимо для ПИФ_НАК (не торгуется на бирже).
    Для биржевых БПИФ цены обновляются автоматически через Celery.
    """
    price_record = await AsyncORM.upsert_investment_price(
        session,
        ticker=req.ticker,
        price_date=req.price_date,
        price=Decimal(str(req.price)),
        source="manual",
    )
    return {
        "ticker":     price_record.ticker,
        "price":      float(price_record.price),
        "price_date": price_record.price_date.isoformat(),
        "source":     price_record.source,
    }
