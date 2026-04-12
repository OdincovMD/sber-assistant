"""
InvestmentService — бизнес-логика инвестиционного портфеля.

Реализует:
- Расчёт текущей стоимости портфеля и P&L по каждому лоту
- ЛДВ-календарь (льгота долгосрочного владения, 3 года)
- Анализ ликвидности для погашения grace period
- Сравнение SBMM vs накопительный счёт
"""

import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, INVESTMENT_FUNDS, get_current_rate
from app.db import AsyncORM
from app.db.models import InvestmentLot, FundType

logger = logging.getLogger(__name__)
settings = get_settings()

# Ставка НДФЛ на инвестиционный доход
NDFL_RATE = Decimal("0.13")

# Порог алерта ЛДВ (дней до наступления льготы)
LDV_WARN_DAYS = 90
LDV_CRITICAL_DAYS = 30


class InvestmentService:

    # ─── Портфель: текущая стоимость и P&L ──────────────────────

    @classmethod
    async def get_portfolio_summary(cls, session: AsyncSession) -> dict:
        """
        Полная сводка по инвестиционному портфелю.

        Возвращает:
        - Стоимость каждого лота по последней известной цене
        - P&L и P&L% на уровне лота и тикера
        - Общий итог по портфелю
        - Потенциальный налог при продаже сегодня (до ЛДВ)
        """
        lots = await AsyncORM.get_active_lots(session)
        prices = await AsyncORM.get_latest_prices_all(session)

        by_ticker: dict[str, dict] = {}
        total_invested = Decimal("0")
        total_current  = Decimal("0")
        total_tax_if_sold = Decimal("0")

        for lot in lots:
            ticker = lot.ticker
            price_record = prices.get(ticker)
            current_price = Decimal(str(price_record.price)) if price_record else None
            price_date    = price_record.price_date if price_record else None

            invested = Decimal(str(lot.quantity)) * Decimal(str(lot.purchase_price))
            current  = (Decimal(str(lot.quantity)) * current_price) if current_price else None

            pnl     = (current - invested) if current is not None else None
            pnl_pct = (pnl / invested * 100) if (pnl is not None and invested > 0) else None

            # Налог при продаже до ЛДВ: 13% с прибыли (только если прибыль > 0)
            tax_if_sold = Decimal("0")
            if pnl is not None and pnl > 0 and lot.ldv_date > date.today():
                tax_if_sold = (pnl * NDFL_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            lot_data = {
                "lot_id":        lot.id,
                "purchase_date": lot.purchase_date.isoformat(),
                "quantity":      float(lot.quantity),
                "purchase_price": float(lot.purchase_price),
                "invested":      float(invested),
                "current_price": float(current_price) if current_price else None,
                "price_date":    price_date.isoformat() if price_date else None,
                "current_value": float(current) if current else None,
                "pnl":           float(pnl) if pnl is not None else None,
                "pnl_pct":       round(float(pnl_pct), 2) if pnl_pct is not None else None,
                "ldv_date":      lot.ldv_date.isoformat(),
                "tax_if_sold":   float(tax_if_sold),
            }

            if ticker not in by_ticker:
                fund_meta = INVESTMENT_FUNDS.get(ticker, {})
                by_ticker[ticker] = {
                    "ticker":      ticker,
                    "fund_name":   lot.fund_name or fund_meta.get("name", ticker),
                    "fund_type":   lot.fund_type.value,
                    "settlement_days": fund_meta.get("settlement", 1),
                    "lots":        [],
                    "total_invested":  Decimal("0"),
                    "total_current":   Decimal("0"),
                    "total_tax":       Decimal("0"),
                }

            by_ticker[ticker]["lots"].append(lot_data)
            by_ticker[ticker]["total_invested"] += invested
            if current:
                by_ticker[ticker]["total_current"] += current
            by_ticker[ticker]["total_tax"] += tax_if_sold

            total_invested += invested
            if current:
                total_current += current
            total_tax_if_sold += tax_if_sold

        # Финализируем по тикерам
        tickers_list = []
        for ticker, tdata in by_ticker.items():
            inv  = tdata["total_invested"]
            cur  = tdata["total_current"]
            pnl  = cur - inv if cur else None
            pnl_pct = (pnl / inv * 100) if (pnl is not None and inv > 0) else None
            share = (cur / total_current * 100) if (cur and total_current > 0) else None

            tickers_list.append({
                "ticker":         ticker,
                "fund_name":      tdata["fund_name"],
                "fund_type":      tdata["fund_type"],
                "settlement_days": tdata["settlement_days"],
                "lots":           tdata["lots"],
                "total_invested": float(inv),
                "total_current":  float(cur) if cur else None,
                "pnl":            float(pnl) if pnl is not None else None,
                "pnl_pct":        round(float(pnl_pct), 2) if pnl_pct is not None else None,
                "portfolio_share_pct": round(float(share), 1) if share is not None else None,
                "tax_if_sold":    float(tdata["total_tax"]),
            })

        total_pnl = total_current - total_invested if total_current else None
        total_pnl_pct = (
            total_pnl / total_invested * 100
            if (total_pnl is not None and total_invested > 0)
            else None
        )

        return {
            "total_invested":    float(total_invested),
            "total_current":     float(total_current) if total_current else None,
            "total_pnl":         float(total_pnl) if total_pnl is not None else None,
            "total_pnl_pct":     round(float(total_pnl_pct), 2) if total_pnl_pct is not None else None,
            "tax_if_sold_today": float(total_tax_if_sold),
            "prices_stale":      not bool(prices),
            "by_ticker":         tickers_list,
        }

    # ─── ЛДВ-календарь ──────────────────────────────────────────

    @classmethod
    async def get_ldv_calendar(cls, session: AsyncSession) -> list[dict]:
        """
        ЛДВ-календарь: сколько дней до налоговой льготы по каждому лоту.

        alert_level:
            "ok"          — более 90 дней
            "warn_90d"    — до 90 дней
            "warn_30d"    — до 30 дней (срочно, не продавать!)
            "available"   — ЛДВ уже наступила
        """
        lots   = await AsyncORM.get_active_lots(session)
        prices = await AsyncORM.get_latest_prices_all(session)
        today  = date.today()
        result = []

        for lot in lots:
            days_to_ldv = (lot.ldv_date - today).days

            if days_to_ldv <= 0:
                alert_level = "available"
            elif days_to_ldv <= LDV_CRITICAL_DAYS:
                alert_level = "warn_30d"
            elif days_to_ldv <= LDV_WARN_DAYS:
                alert_level = "warn_90d"
            else:
                alert_level = "ok"

            # Расчёт потенциального налога
            invested = Decimal(str(lot.quantity)) * Decimal(str(lot.purchase_price))
            price_record = prices.get(lot.ticker)
            tax_at_risk = Decimal("0")
            if price_record and days_to_ldv > 0:
                current = Decimal(str(lot.quantity)) * Decimal(str(price_record.price))
                pnl = current - invested
                if pnl > 0:
                    tax_at_risk = (pnl * NDFL_RATE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            result.append({
                "ticker":       lot.ticker,
                "lot_id":       lot.id,
                "purchase_date": lot.purchase_date.isoformat(),
                "quantity":     float(lot.quantity),
                "ldv_date":     lot.ldv_date.isoformat(),
                "days_to_ldv":  days_to_ldv,
                "alert_level":  alert_level,
                "tax_at_risk":  float(tax_at_risk),
                "message":      cls._ldv_message(lot.ticker, days_to_ldv, float(tax_at_risk)),
            })

        return sorted(result, key=lambda x: x["days_to_ldv"])

    @staticmethod
    def _ldv_message(ticker: str, days: int, tax: float) -> str:
        if days <= 0:
            return f"{ticker}: ЛДВ доступна — можно продавать без налога"
        if days <= LDV_CRITICAL_DAYS:
            return (
                f"{ticker}: {days} дн. до ЛДВ — НЕ продавать! "
                f"Потенциальный налог при продаже: {tax:,.0f} ₽"
            )
        if days <= LDV_WARN_DAYS:
            return f"{ticker}: {days} дн. до ЛДВ (менее 3 месяцев)"
        return f"{ticker}: {days} дн. до ЛДВ"

    # ─── Ликвидность для grace period ────────────────────────────

    @classmethod
    async def get_liquidity_for_grace(cls, session: AsyncSession) -> dict:
        """
        Анализ ликвидности портфеля для погашения кредитной задолженности.

        Делит портфель на:
        - T+1 (биржевые БПИФ): деньги доступны через 1 рабочий день
        - T+4 (открытые ПИФ): деньги доступны через 3-5 рабочих дней

        Сопоставляет с ближайшим дедлайном grace period.
        """
        lots           = await AsyncORM.get_active_lots(session)
        prices         = await AsyncORM.get_latest_prices_all(session)
        open_periods   = await AsyncORM.get_open_billing_periods(session)

        # Считаем ликвидность по тирам
        t1_value  = Decimal("0")  # БПИФ (T+1)
        t4_value  = Decimal("0")  # ОПИФ (T+4)

        for lot in lots:
            price_record = prices.get(lot.ticker)
            if not price_record:
                continue
            value = Decimal(str(lot.quantity)) * Decimal(str(price_record.price))
            fund_meta = INVESTMENT_FUNDS.get(lot.ticker, {})
            if fund_meta.get("settlement", 1) <= 1:
                t1_value += value
            else:
                t4_value += value

        # Ближайший grace period
        nearest_period   = open_periods[0] if open_periods else None
        grace_needed     = Decimal("0")
        grace_deadline   = None
        days_to_deadline = None

        if nearest_period:
            grace_needed     = nearest_period.total_spent or Decimal("0")
            grace_deadline   = nearest_period.grace_deadline
            days_to_deadline = (grace_deadline - date.today()).days

        # Рекомендация
        recommendation = cls._liquidity_recommendation(
            t1_value, t4_value, grace_needed, days_to_deadline
        )

        return {
            "t1_available":      float(t1_value),
            "t4_available":      float(t4_value),
            "total_liquid":      float(t1_value + t4_value),
            "grace_needed":      float(grace_needed),
            "grace_deadline":    grace_deadline.isoformat() if grace_deadline else None,
            "days_to_deadline":  days_to_deadline,
            "can_cover_t1":      t1_value >= grace_needed,
            "can_cover_total":   (t1_value + t4_value) >= grace_needed,
            "recommendation":    recommendation,
        }

    @staticmethod
    def _liquidity_recommendation(
        t1: Decimal, t4: Decimal, needed: Decimal, days: Optional[int]
    ) -> str:
        if needed == 0:
            return "Открытых кредитных периодов нет — ликвидность не требуется"
        if days is not None and days <= 5 and t1 < needed:
            return (
                f"СРОЧНО: до дедлайна {days} дн., T+1 ресурсов ({float(t1):,.0f} ₽) "
                f"недостаточно для покрытия долга ({float(needed):,.0f} ₽)"
            )
        if t1 >= needed:
            return (
                f"SBMM и БПИФ покрывают долг: {float(t1):,.0f} ₽ доступно T+1 "
                f"(нужно {float(needed):,.0f} ₽)"
            )
        if (t1 + t4) >= needed:
            return (
                f"Только с учётом ПИФ ({float(t1+t4):,.0f} ₽) хватит на долг — "
                f"учитывайте T+4 при планировании"
            )
        return f"Портфеля недостаточно для покрытия долга {float(needed):,.0f} ₽"

    # ─── SBMM vs накопительный счёт ─────────────────────────────

    @classmethod
    async def compare_sbmm_vs_savings(cls, session: AsyncSession) -> dict:
        """
        Сравнение SBMM (денежный рынок) и накопительного счёта *1837.

        SBMM доходность ≈ RUONIA - TER_SBMM
        Накопительный = ставка из SAVINGS_RATES[текущий_месяц]

        Оба инструмента — «парковка кэша». Показывает, где выгоднее
        держать резерв с учётом текущих ставок.
        """
        from app.services.cbr_client import CBRClient

        # Ставка SBMM: RUONIA - TER
        ruonia = await CBRClient.get_ruonia()
        sbmm_ter = INVESTMENT_FUNDS["SBMM"]["ter_pct"]

        sbmm_yield: Optional[float] = None
        if ruonia:
            sbmm_yield = round(float(ruonia) - sbmm_ter, 2)

        # Ставка накопительного счёта
        savings_rate = get_current_rate()

        # Позиция SBMM в портфеле
        sbmm_lots  = await AsyncORM.get_lots_by_ticker(session, "SBMM")
        sbmm_price = await AsyncORM.get_latest_price(session, "SBMM")

        sbmm_value = Decimal("0")
        if sbmm_price:
            for lot in sbmm_lots:
                sbmm_value += Decimal(str(lot.quantity)) * Decimal(str(sbmm_price.price))

        # Текущий баланс накопительного
        savings_balance = await AsyncORM.get_latest_savings_balance(session)

        # Победитель
        winner: Optional[str] = None
        delta: Optional[float] = None
        if sbmm_yield is not None:
            if sbmm_yield > savings_rate:
                winner = "sbmm"
                delta  = round(sbmm_yield - savings_rate, 2)
            else:
                winner = "savings"
                delta  = round(savings_rate - sbmm_yield, 2)

        # Годовой доход при текущих суммах
        def annual_income(balance: Decimal, rate_pct: float) -> float:
            return float(balance) * rate_pct / 100

        return {
            "sbmm": {
                "yield_annual_pct": sbmm_yield,
                "ruonia_pct":       float(ruonia) if ruonia else None,
                "ter_pct":          sbmm_ter,
                "current_value":    float(sbmm_value),
                "annual_income":    annual_income(sbmm_value, sbmm_yield) if sbmm_yield else None,
            },
            "savings": {
                "yield_annual_pct": savings_rate,
                "current_balance":  float(savings_balance),
                "annual_income":    annual_income(savings_balance, savings_rate),
            },
            "winner":    winner,
            "delta_pct": delta,
            "summary":   cls._compare_summary(winner, delta, sbmm_yield, savings_rate),
        }

    @staticmethod
    def _compare_summary(
        winner: Optional[str], delta: Optional[float],
        sbmm: Optional[float], savings: float
    ) -> str:
        if winner is None:
            return f"Нет данных по RUONIA. Ставка накопительного: {savings}%"
        if winner == "sbmm":
            return (
                f"SBMM выгоднее накопительного на {delta}% годовых "
                f"({sbmm}% vs {savings}%)"
            )
        return (
            f"Накопительный счёт выгоднее SBMM на {delta}% годовых "
            f"({savings}% vs {sbmm}%)"
        )
