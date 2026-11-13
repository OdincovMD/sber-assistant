"""Pure calculation of billing-period debt and lifecycle state."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Union


Numeric = Union[Decimal, int, float, str]


@dataclass(frozen=True)
class BillingPeriodState:
    total_spent: Decimal
    total_paid: Decimal
    outstanding: Decimal
    is_closed: bool


def calculate_billing_period_state(
    total_spent: Optional[Numeric],
    total_paid: Optional[Numeric],
) -> BillingPeriodState:
    """Return normalized debt and whether a period has been fully repaid."""
    spent = max(Decimal("0"), Decimal(str(total_spent or 0)))
    paid = max(Decimal("0"), Decimal(str(total_paid or 0)))
    outstanding = max(Decimal("0"), spent - paid)

    return BillingPeriodState(
        total_spent=spent,
        total_paid=paid,
        outstanding=outstanding,
        is_closed=outstanding == 0,
    )
