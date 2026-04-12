"""
MOEXClient — асинхронный клиент Московской биржи (ISS API).

Получает цены биржевых БПИФ (SBGB, SBGD, SBFR, SBMM) с доски TQTF.
Документация ISS: https://iss.moex.com/iss/reference/
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

_ISS_BASE = "https://iss.moex.com/iss"
_BOARD    = "TQTF"   # доска для биржевых БПИФ
_MARKET   = "shares"
_ENGINE   = "stock"

# Тикеры, торгующиеся на MOEX (ПИФ_НАК — не биржевой, не запрашиваем)
MOEX_TICKERS = ("SBGB", "SBGD", "SBFR", "SBMM")


class MOEXClient:
    """Клиент MOEX ISS API — только чтение котировок, без авторизации."""

    # ─── Текущая цена ───────────────────────────────────────────

    @staticmethod
    async def get_price(ticker: str) -> Optional[Decimal]:
        """
        Последняя торговая цена инструмента на доске TQTF.

        Возвращает LCURRENTPRICE (последняя цена сделки)
        или PREVADMITTEDQUOTE (допущенная цена предыдущего дня) как fallback.
        """
        url = (
            f"{_ISS_BASE}/engines/{_ENGINE}/markets/{_MARKET}"
            f"/boards/{_BOARD}/securities/{ticker}.json"
            "?iss.meta=off&iss.only=marketdata,securities"
        )

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"MOEX ISS {ticker}: HTTP {resp.status}")
                        return None
                    data = await resp.json()

            # marketdata — живые данные
            md = data.get("marketdata", {})
            md_cols = md.get("columns", [])
            md_rows = md.get("data", [])

            if md_rows:
                row = dict(zip(md_cols, md_rows[0]))
                price = row.get("LAST") or row.get("LCURRENTPRICE")
                if price:
                    return Decimal(str(price))

            # fallback: securities — допущенная цена предыдущего дня
            sec = data.get("securities", {})
            sec_cols = sec.get("columns", [])
            sec_rows = sec.get("data", [])

            if sec_rows:
                row = dict(zip(sec_cols, sec_rows[0]))
                price = row.get("PREVADMITTEDQUOTE") or row.get("PREVPRICE")
                if price:
                    logger.info(f"MOEX {ticker}: использована цена предыдущего дня ({price})")
                    return Decimal(str(price))

            logger.warning(f"MOEX ISS {ticker}: цена не найдена в ответе")
            return None

        except Exception as e:
            logger.error(f"MOEX ISS {ticker}: ошибка запроса — {e}")
            return None

    @staticmethod
    async def get_prices_bulk(tickers: tuple[str, ...] = MOEX_TICKERS) -> dict[str, Optional[Decimal]]:
        """
        Текущие цены для нескольких тикеров.
        Запросы выполняются последовательно — ISS не поддерживает batch по нескольким бумагам одним запросом.
        """
        import asyncio
        results: dict[str, Optional[Decimal]] = {}
        tasks = {ticker: MOEXClient.get_price(ticker) for ticker in tickers}
        for ticker, coro in tasks.items():
            results[ticker] = await coro
        return results

    # ─── История цен ────────────────────────────────────────────

    @staticmethod
    async def get_price_history(
        ticker: str,
        from_date: date,
        till_date: Optional[date] = None,
    ) -> list[dict]:
        """
        История закрытий (CLOSE) по инструменту за период.

        Returns:
            Список словарей [{"date": date, "close": Decimal}, ...]
        """
        till = till_date or date.today()
        url = (
            f"{_ISS_BASE}/history/engines/{_ENGINE}/markets/{_MARKET}"
            f"/boards/{_BOARD}/securities/{ticker}.json"
            f"?from={from_date.isoformat()}&till={till.isoformat()}"
            "&iss.meta=off&iss.only=history"
            "&history.columns=TRADEDATE,CLOSE,VOLUME"
        )

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(f"MOEX history {ticker}: HTTP {resp.status}")
                        return []
                    data = await resp.json()

            hist = data.get("history", {})
            cols = hist.get("columns", [])
            rows = hist.get("data", [])

            result = []
            for row_data in rows:
                row = dict(zip(cols, row_data))
                close = row.get("CLOSE")
                trade_date = row.get("TRADEDATE")
                if close and trade_date:
                    result.append({
                        "date":  date.fromisoformat(trade_date),
                        "close": Decimal(str(close)),
                    })

            return result

        except Exception as e:
            logger.error(f"MOEX history {ticker}: ошибка — {e}")
            return []

    # ─── ISIN ───────────────────────────────────────────────────

    @staticmethod
    async def get_isin(ticker: str) -> Optional[str]:
        """Получить ISIN инструмента с MOEX."""
        url = (
            f"{_ISS_BASE}/engines/{_ENGINE}/markets/{_MARKET}"
            f"/boards/{_BOARD}/securities/{ticker}.json"
            "?iss.meta=off&iss.only=securities"
            "&securities.columns=SECID,ISIN,SECNAME"
        )
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()

            sec = data.get("securities", {})
            cols = sec.get("columns", [])
            rows = sec.get("data", [])
            if rows:
                row = dict(zip(cols, rows[0]))
                return row.get("ISIN")
            return None

        except Exception as e:
            logger.error(f"MOEX ISIN {ticker}: {e}")
            return None
