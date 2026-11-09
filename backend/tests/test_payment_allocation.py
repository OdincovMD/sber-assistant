import unittest
from decimal import Decimal

from app.services.payment_allocation import allocate_payment_fifo


class PaymentAllocationTests(unittest.TestCase):
    def test_partial_payment_reduces_oldest_period(self):
        allocations, remainder = allocate_payment_fifo(
            Decimal("200"),
            [(1, Decimal("500")), (2, Decimal("400"))],
        )

        self.assertEqual(
            [(item.period_id, item.amount) for item in allocations],
            [(1, Decimal("200"))],
        )
        self.assertEqual(remainder, Decimal("0"))

    def test_payment_spills_into_next_period(self):
        allocations, remainder = allocate_payment_fifo(
            Decimal("700"),
            [(1, Decimal("500")), (2, Decimal("400"))],
        )

        self.assertEqual(
            [(item.period_id, item.amount) for item in allocations],
            [(1, Decimal("500")), (2, Decimal("200"))],
        )
        self.assertEqual(remainder, Decimal("0"))

    def test_overpayment_is_returned_as_unallocated_remainder(self):
        allocations, remainder = allocate_payment_fifo(
            Decimal("1000"),
            [(1, Decimal("500")), (2, Decimal("400"))],
        )

        self.assertEqual(
            [(item.period_id, item.amount) for item in allocations],
            [(1, Decimal("500")), (2, Decimal("400"))],
        )
        self.assertEqual(remainder, Decimal("100"))

    def test_zero_debt_periods_are_skipped(self):
        allocations, remainder = allocate_payment_fifo(
            Decimal("100"),
            [(1, Decimal("0")), (2, Decimal("250"))],
        )

        self.assertEqual(
            [(item.period_id, item.amount) for item in allocations],
            [(2, Decimal("100"))],
        )
        self.assertEqual(remainder, Decimal("0"))

    def test_negative_payment_is_rejected(self):
        with self.assertRaises(ValueError):
            allocate_payment_fifo(Decimal("-1"), [(1, Decimal("100"))])


if __name__ == "__main__":
    unittest.main()
