"""
CBRClient — клиент ЦБ РФ.

Получает данные через официальный SOAP веб-сервис ЦБ РФ.
WSDL: https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx?WSDL

Что используется:
- Ключевая ставка (KeyRate) — критична для SBMM, SBFR, SBGB
- RUONIA — ориентир доходности SBMM
- Расписание заседаний Совета директоров по ставке
"""

import logging
from datetime import date
from decimal import Decimal
from typing import Optional
from xml.etree import ElementTree as ET

import aiohttp

from app.config import CBR_KEY_RATE_FALLBACK

logger = logging.getLogger(__name__)

_SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"
_NS       = "http://web.cbr.ru/"


def _soap_envelope(method: str, from_date: date, to_date: date) -> bytes:
    """Собрать SOAP-конверт для методов KeyRate / Ruonia."""
    fd = from_date.strftime("%Y-%m-%dT00:00:00")
    td = to_date.strftime("%Y-%m-%dT00:00:00")
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:tns="{_NS}">'
        f"<soap:Body>"
        f"<tns:{method}><tns:fromDate>{fd}</tns:fromDate><tns:ToDate>{td}</tns:ToDate></tns:{method}>"
        f"</soap:Body></soap:Envelope>"
    ).encode("utf-8")


async def _soap_request(method: str, from_date: date, to_date: date) -> Optional[ET.Element]:
    """Выполнить SOAP-запрос и вернуть корень XML-ответа (или None при ошибке)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(
                _SOAP_URL,
                data=_soap_envelope(method, from_date, to_date),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f"{_NS}{method}",
                },
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"CBR SOAP {method}: HTTP {resp.status}")
                    return None
                text = await resp.text()
        return ET.fromstring(text)
    except Exception as e:
        logger.error(f"CBR SOAP {method}: {e}")
        return None


class CBRClient:
    """Клиент ЦБ РФ — ключевая ставка и RUONIA через официальный SOAP API."""

    # ─── Ключевая ставка ────────────────────────────────────────

    @staticmethod
    async def get_key_rate() -> Decimal:
        """
        Текущая ключевая ставка ЦБ РФ (% годовых).

        Источник: SOAP метод KeyRate (DailyInfoWebServ).
        Структура ответа: KR/DT (дата), KR/Rate (ставка).
        При недоступности API — возвращает CBR_KEY_RATE_FALLBACK из config.py.
        """
        today     = date.today()
        from_date = date(today.year, today.month, 1)

        root = await _soap_request("KeyRate", from_date, today)
        if root is not None:
            # Ищем все элементы KR и берём последний по дате
            records = root.findall(".//{http://web.cbr.ru/}KR")
            if not records:
                # Попробуем без namespace (зависит от версии ET)
                records = root.findall(".//KR")

            best_rate: Optional[Decimal] = None
            best_dt: Optional[str] = None
            for rec in records:
                dt   = rec.findtext("{http://web.cbr.ru/}DT") or rec.findtext("DT")
                rate = rec.findtext("{http://web.cbr.ru/}Rate") or rec.findtext("Rate")
                if rate and (best_dt is None or dt > best_dt):
                    best_dt   = dt
                    best_rate = Decimal(str(rate))

            if best_rate is not None:
                logger.info(f"CBR key rate: {best_rate}% (дата: {best_dt})")
                return best_rate

        logger.warning(
            f"CBR: ключевая ставка недоступна — используется fallback {CBR_KEY_RATE_FALLBACK}%"
        )
        return Decimal(str(CBR_KEY_RATE_FALLBACK))

    # ─── RUONIA ─────────────────────────────────────────────────

    @staticmethod
    async def get_ruonia() -> Decimal:
        """
        Ставка RUONIA (Russian OverNight Index Average).
        Ориентир доходности SBMM (фонд денежного рынка отслеживает RUONIA).

        Источник: SOAP метод Ruonia (DailyInfoWebServ).
        Структура ответа: ro/D0 (дата), ro/ruo (ставка).
        Fallback: ключевая ставка − 0.2%.
        """
        today     = date.today()
        from_date = date(today.year, today.month, 1)

        root = await _soap_request("Ruonia", from_date, today)
        if root is not None:
            records = root.findall(".//{http://web.cbr.ru/}ro")
            if not records:
                records = root.findall(".//ro")

            best_rate: Optional[Decimal] = None
            best_dt: Optional[str] = None
            for rec in records:
                dt   = rec.findtext("{http://web.cbr.ru/}D0") or rec.findtext("D0")
                rate = rec.findtext("{http://web.cbr.ru/}ruo") or rec.findtext("ruo")
                if rate and (best_dt is None or dt > best_dt):
                    best_dt   = dt
                    best_rate = Decimal(str(rate))

            if best_rate is not None:
                logger.info(f"CBR RUONIA: {best_rate}% (дата: {best_dt})")
                return best_rate

        key_rate = await CBRClient.get_key_rate()
        fallback = key_rate - Decimal("0.2")
        logger.warning(f"CBR RUONIA недоступен — расчётный fallback: {fallback}%")
        return fallback

    # ─── Расписание заседаний ────────────────────────────────────

    @staticmethod
    def get_upcoming_meeting_dates() -> list[date]:
        """
        Ближайшие даты заседаний Совета директоров ЦБ по ключевой ставке.

        Расписание публикуется ежегодно на cbr.ru. Здесь — хардкод на 2026 год.
        Источник: https://www.cbr.ru/press/keypr/
        """
        meetings_2026 = [
            date(2026, 2, 14),
            date(2026, 3, 21),
            date(2026, 4, 25),
            date(2026, 6, 6),
            date(2026, 7, 25),
            date(2026, 9, 12),
            date(2026, 10, 24),
            date(2026, 12, 19),
        ]
        today = date.today()
        return [d for d in meetings_2026 if d >= today]

    @staticmethod
    def get_next_meeting_date() -> Optional[date]:
        """Ближайшее заседание ЦБ по ключевой ставке."""
        upcoming = CBRClient.get_upcoming_meeting_dates()
        return upcoming[0] if upcoming else None

    @staticmethod
    def days_to_next_meeting() -> Optional[int]:
        """Дней до следующего заседания ЦБ."""
        next_meeting = CBRClient.get_next_meeting_date()
        return (next_meeting - date.today()).days if next_meeting else None

    # ─── Сводка для инвестиционного дайджеста ───────────────────

    @staticmethod
    async def get_summary() -> dict:
        """
        Краткая сводка для инвестиционного дайджеста.

        Returns:
            {
                "key_rate": 15.0,
                "ruonia": 14.8,
                "next_meeting": "2026-04-25",
                "days_to_meeting": 13,
            }
        """
        key_rate     = await CBRClient.get_key_rate()
        ruonia       = await CBRClient.get_ruonia()
        next_meeting = CBRClient.get_next_meeting_date()
        days         = CBRClient.days_to_next_meeting()

        return {
            "key_rate":        float(key_rate),
            "ruonia":          float(ruonia),
            "next_meeting":    next_meeting.isoformat() if next_meeting else None,
            "days_to_meeting": days,
        }
