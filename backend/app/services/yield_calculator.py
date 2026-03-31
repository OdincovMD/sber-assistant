from decimal import Decimal, ROUND_HALF_UP
from app.config import get_current_rate

def calculate_daily_yield(current_balance: Decimal) -> Decimal:
    """
    Рассчитать ежедневную прибыль по накопительному счёту.
    Формула: баланс * (ставка / 100) / 365.
    Округление до 2 знаков после запятой.
    """
    rate = Decimal(str(get_current_rate()))
    daily = (current_balance * (rate / Decimal("100")) / Decimal("365"))
    return daily.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
