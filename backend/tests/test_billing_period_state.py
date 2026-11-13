import unittest
from decimal import Decimal

from app.services.billing_period_state import calculate_billing_period_state


class BillingPeriodStateTests(unittest.TestCase):
    def test_partial_payment_keeps_period_open(self):
        state = calculate_billing_period_state(
            Decimal("50000.00"),
            Decimal("20000.00"),
        )

        self.assertEqual(state.outstanding, Decimal("30000.00"))
        self.assertFalse(state.is_closed)

    def test_full_payment_closes_period(self):
        state = calculate_billing_period_state("50000.00", "50000.00")

        self.assertEqual(state.outstanding, Decimal("0"))
        self.assertTrue(state.is_closed)

    def test_overpayment_never_creates_negative_debt(self):
        state = calculate_billing_period_state("50000.00", "60000.00")

        self.assertEqual(state.outstanding, Decimal("0"))
        self.assertTrue(state.is_closed)

    def test_late_purchase_reopens_previously_paid_period(self):
        previously_paid = calculate_billing_period_state("50000.00", "50000.00")
        after_late_purchase = calculate_billing_period_state(
            "55000.00",
            previously_paid.total_paid,
        )

        self.assertTrue(previously_paid.is_closed)
        self.assertEqual(after_late_purchase.outstanding, Decimal("5000.00"))
        self.assertFalse(after_late_purchase.is_closed)

    def test_empty_values_are_normalized_to_closed_zero_debt(self):
        state = calculate_billing_period_state(None, None)

        self.assertEqual(state.total_spent, Decimal("0"))
        self.assertEqual(state.total_paid, Decimal("0"))
        self.assertTrue(state.is_closed)


if __name__ == "__main__":
    unittest.main()
