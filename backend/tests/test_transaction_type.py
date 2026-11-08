import unittest

from app.services.transaction_type import (
    is_credit_repayment,
    normalize_transaction_type,
)


class NormalizeTransactionTypeTests(unittest.TestCase):
    def test_explicit_type_has_priority(self):
        result = normalize_transaction_type(
            explicit_type="fee",
            account_type="credit",
            category="Другое",
            is_expense=True,
        )
        self.assertEqual(result, "fee")

    def test_credit_card_income_is_payment_when_llm_omits_type(self):
        result = normalize_transaction_type(
            explicit_type=None,
            account_type="credit",
            category="Зачисление",
            is_expense=False,
        )
        self.assertEqual(result, "payment")

    def test_incoming_transfer_to_credit_card_is_payment(self):
        result = normalize_transaction_type(
            explicit_type="unknown",
            account_type="credit",
            category="Перевод между счетами",
            is_expense=False,
        )
        self.assertEqual(result, "payment")

    def test_savings_income_is_deposit(self):
        result = normalize_transaction_type(
            explicit_type=None,
            account_type="savings",
            category="Зачисление",
            is_expense=False,
        )
        self.assertEqual(result, "deposit")

    def test_expense_falls_back_to_purchase(self):
        result = normalize_transaction_type(
            explicit_type=None,
            account_type="debit",
            category="Продукты",
            is_expense=True,
        )
        self.assertEqual(result, "purchase")


class CreditRepaymentTests(unittest.TestCase):
    def test_credit_payment_type_is_repayment(self):
        self.assertTrue(
            is_credit_repayment(
                account_type="credit",
                transaction_type="payment",
                is_expense=False,
            )
        )

    def test_credit_income_is_repayment_even_without_type(self):
        self.assertTrue(
            is_credit_repayment(
                account_type="credit",
                transaction_type="unknown",
                is_expense=False,
            )
        )

    def test_debit_income_is_not_credit_repayment(self):
        self.assertFalse(
            is_credit_repayment(
                account_type="debit",
                transaction_type="deposit",
                is_expense=False,
            )
        )


if __name__ == "__main__":
    unittest.main()
