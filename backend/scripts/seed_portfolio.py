"""
Seed-скрипт: заносит реальные лоты инвестиционного портфеля в БД.

Идемпотентен — повторный запуск не создаёт дубликаты.
Запуск:
    cd backend
    python -m scripts.seed_portfolio
"""

import asyncio
import sys
import os
from datetime import date
from decimal import Decimal

# Добавляем корень backend/ в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.async_orm import AsyncORM
from app.db.models import FundType
from app.config import INVESTMENT_FUNDS


# ─── Данные портфеля ────────────────────────────────────────────────────────
# Формат: (ticker, quantity, purchase_price, purchase_date)
# ЛДВ = purchase_date + 3 года (вычисляется автоматически)

PORTFOLIO_LOTS: list[tuple[str, Decimal, Decimal, date]] = [
    # SBGB — БПИФ Гос. облигации
    ("SBGB", Decimal("1043"),     Decimal("14.3770"), date(2025, 8,  5)),
    ("SBGB", Decimal("17"),       Decimal("14.0310"), date(2025, 10, 3)),

    # SBGD — БПИФ Доступное золото
    ("SBGD", Decimal("645"),      Decimal("38.7950"), date(2026, 3,  5)),

    # SBFR — БПИФ Облигации флоатеры
    ("SBFR", Decimal("1531"),     Decimal("13.0580"), date(2025, 8,  5)),
    ("SBFR", Decimal("1503"),     Decimal("13.3710"), date(2025, 10, 3)),
    ("SBFR", Decimal("1831"),     Decimal("13.6240"), date(2025, 11, 6)),

    # SBMM — БПИФ Сберегательный (денежный рынок)
    ("SBMM", Decimal("1759"),     Decimal("16.8545"), date(2025, 10, 3)),
    ("SBMM", Decimal("1448"),     Decimal("17.0965"), date(2025, 11, 6)),

    # PIF_NAK — ОПИФ Накопительный (дробные паи)
    # Цена рассчитана: сумма / количество паёв
    ("PIF_NAK", Decimal("63.53677"), Decimal("1888.6988"), date(2025, 9,  9)),
    ("PIF_NAK", Decimal("10.49070"), Decimal("1906.4536"), date(2025, 10, 6)),
]


def get_ldv_date(purchase_date: date) -> date:
    """ЛДВ = дата покупки + 3 года."""
    return purchase_date.replace(year=purchase_date.year + 3)


async def seed() -> None:
    await AsyncORM.init()

    async with AsyncORM.get_session()() as session:
        created = 0
        skipped = 0

        for ticker, quantity, purchase_price, purchase_date in PORTFOLIO_LOTS:
            fund_meta = INVESTMENT_FUNDS[ticker]

            # Идемпотентность: не создавать дубликат
            exists = await AsyncORM.lot_exists(session, ticker, purchase_date, quantity)
            if exists:
                print(f"  SKIP  {ticker:8s} {quantity:>12} шт @ {purchase_price} ({purchase_date})")
                skipped += 1
                continue

            lot = await AsyncORM.create_investment_lot(
                session,
                ticker=ticker,
                isin=fund_meta["isin"],
                fund_name=fund_meta["name"],
                fund_type=FundType(fund_meta["fund_type"]),
                quantity=quantity,
                purchase_price=purchase_price,
                purchase_date=purchase_date,
                ldv_date=get_ldv_date(purchase_date),
            )

            invested = quantity * purchase_price
            print(
                f"  CREATE {ticker:8s} {quantity:>12} шт @ {purchase_price:>10} "
                f"= {invested:>12.2f} ₽  (ЛДВ: {lot.ldv_date})"
            )
            created += 1

        await session.commit()

    # Итоговая сводка
    print()
    print(f"Готово: создано {created} лотов, пропущено {skipped} (уже существуют).")
    print()

    # Сводка по портфелю
    total_invested = sum(qty * price for _, qty, price, _ in PORTFOLIO_LOTS)
    print(f"Всего вложено: {total_invested:,.2f} ₽")
    by_ticker: dict[str, Decimal] = {}
    for ticker, qty, price, _ in PORTFOLIO_LOTS:
        by_ticker[ticker] = by_ticker.get(ticker, Decimal("0")) + qty * price
    for ticker, amount in sorted(by_ticker.items()):
        pct = amount / total_invested * 100
        print(f"  {ticker:8s}: {amount:>12,.2f} ₽  ({pct:.1f}%)")

    await AsyncORM.close()


if __name__ == "__main__":
    asyncio.run(seed())
