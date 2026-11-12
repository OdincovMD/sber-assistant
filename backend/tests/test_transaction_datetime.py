import unittest
from datetime import datetime, timezone

from app.services.transaction_datetime import MOSCOW_TIMEZONE, parse_transaction_datetime


class TransactionDatetimeTests(unittest.TestCase):
    def setUp(self):
        self.received_at = datetime(2026, 11, 12, 18, 45, tzinfo=timezone.utc)

    def test_extracts_full_year_and_time(self):
        result = parse_transaction_datetime(
            "Покупка 500 руб. 31.10.2026 23:58 Магазин",
            self.received_at,
        )

        self.assertEqual(
            result,
            datetime(2026, 10, 31, 23, 58, tzinfo=MOSCOW_TIMEZONE),
        )

    def test_extracts_short_year_and_single_digit_date(self):
        result = parse_transaction_datetime(
            "Операция 5/1/26 09:07 выполнена",
            self.received_at,
        )

        self.assertEqual(
            result,
            datetime(2026, 1, 5, 9, 7, tzinfo=MOSCOW_TIMEZONE),
        )

    def test_date_without_time_preserves_receipt_local_time(self):
        result = parse_transaction_datetime(
            "Платеж принят 01-11-2026",
            self.received_at,
        )

        self.assertEqual(
            result,
            datetime(2026, 11, 1, 21, 45, tzinfo=MOSCOW_TIMEZONE),
        )

    def test_missing_date_falls_back_to_receipt_time_in_moscow(self):
        received_at = datetime(2026, 10, 31, 21, 30, tzinfo=timezone.utc)

        result = parse_transaction_datetime("Покупка выполнена", received_at)

        self.assertEqual(
            result,
            datetime(2026, 11, 1, 0, 30, tzinfo=MOSCOW_TIMEZONE),
        )

    def test_invalid_calendar_date_falls_back_safely(self):
        result = parse_transaction_datetime(
            "Операция 31.02.2026 12:00 отклонена",
            self.received_at,
        )

        self.assertEqual(
            result,
            datetime(2026, 11, 12, 21, 45, tzinfo=MOSCOW_TIMEZONE),
        )

    def test_naive_receipt_time_is_treated_as_moscow_time(self):
        result = parse_transaction_datetime(
            "Дата в сообщении отсутствует",
            datetime(2026, 11, 12, 21, 45),
        )

        self.assertEqual(
            result,
            datetime(2026, 11, 12, 21, 45, tzinfo=MOSCOW_TIMEZONE),
        )


if __name__ == "__main__":
    unittest.main()
