"""Pure rules for credit-card grace-deadline alerts."""

from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from typing import Optional, Union


Numeric = Union[Decimal, int, float, str]
REMINDER_DAYS = frozenset({7, 3, 1, 0})


@dataclass(frozen=True)
class GraceDeadlineAlert:
    period_id: int
    month: str
    outstanding: Decimal
    grace_deadline: date
    days_left: int
    severity: str
    should_notify: bool
    message: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["outstanding"] = float(self.outstanding)
        data["grace_deadline"] = self.grace_deadline.isoformat()
        return data


def _format_rubles(amount: Decimal) -> str:
    normalized = amount.quantize(Decimal("0.01"))
    if normalized == normalized.to_integral():
        return f"{int(normalized):,}".replace(",", " ")
    return f"{normalized:,.2f}".replace(",", " ")


def _days_word(value: int) -> str:
    value = abs(value)
    if value % 10 == 1 and value % 100 != 11:
        return "день"
    if value % 10 in (2, 3, 4) and value % 100 not in (12, 13, 14):
        return "дня"
    return "дней"


def build_grace_deadline_alert(
    *,
    period_id: int,
    month: date,
    grace_deadline: date,
    outstanding: Numeric,
    target_date: date,
) -> Optional[GraceDeadlineAlert]:
    """Build an alert for debt due within seven days or already overdue."""
    debt = max(Decimal("0"), Decimal(str(outstanding)))
    if debt == 0:
        return None

    days_left = (grace_deadline - target_date).days
    if days_left > max(REMINDER_DAYS):
        return None

    if days_left < 0:
        severity = "overdue"
        timing = f"просрочено на {abs(days_left)} {_days_word(days_left)}"
    elif days_left == 0:
        severity = "critical"
        timing = "срок погашения сегодня"
    elif days_left <= 1:
        severity = "critical"
        timing = f"до срока {days_left} {_days_word(days_left)}"
    elif days_left <= 3:
        severity = "urgent"
        timing = f"до срока {days_left} {_days_word(days_left)}"
    else:
        severity = "warning"
        timing = f"до срока {days_left} {_days_word(days_left)}"

    amount_text = _format_rubles(debt)
    message = (
        f"Период {month.strftime('%Y-%m')}: {timing}. "
        f"Погасить {amount_text} руб. до {grace_deadline.strftime('%d.%m.%Y')}."
    )

    return GraceDeadlineAlert(
        period_id=period_id,
        month=month.strftime("%Y-%m"),
        outstanding=debt,
        grace_deadline=grace_deadline,
        days_left=days_left,
        severity=severity,
        should_notify=days_left < 0 or days_left in REMINDER_DAYS,
        message=message,
    )
