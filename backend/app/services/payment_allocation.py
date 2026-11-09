"""Pure FIFO allocation for credit-card repayments."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable


@dataclass(frozen=True)
class PaymentAllocation:
    """Part of a payment assigned to one billing period."""

    period_id: int
    amount: Decimal


def allocate_payment_fifo(
    payment_amount: Decimal,
    period_outstandings: Iterable[tuple[int, Decimal]],
) -> tuple[list[PaymentAllocation], Decimal]:
    """
    Allocate a payment from the oldest period to the newest.

    ``period_outstandings`` must already be ordered by payment priority.
    The returned remainder is the part that exceeds all known debt.
    """
    remaining = Decimal(str(payment_amount))
    if remaining < 0:
        raise ValueError("payment_amount must not be negative")

    allocations: list[PaymentAllocation] = []
    for period_id, raw_outstanding in period_outstandings:
        if remaining == 0:
            break

        outstanding = max(Decimal("0"), Decimal(str(raw_outstanding)))
        if outstanding == 0:
            continue

        allocated = min(remaining, outstanding)
        allocations.append(PaymentAllocation(period_id=period_id, amount=allocated))
        remaining -= allocated

    return allocations, remaining
