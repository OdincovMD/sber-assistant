import unittest
from datetime import date

from app.services.idempotency import build_webhook_idempotency_key


class WebhookIdempotencyTests(unittest.TestCase):
    def test_same_sms_retry_has_same_daily_key(self):
        first = build_webhook_idempotency_key(
            "Покупка   в магазине\nБаланс изменён",
            event_date=date(2026, 11, 10),
        )
        retry = build_webhook_idempotency_key(
            "Покупка в магазине Баланс изменён",
            event_date=date(2026, 11, 10),
        )

        self.assertEqual(first, retry)

    def test_same_sms_on_another_day_is_allowed(self):
        first = build_webhook_idempotency_key(
            "Одинаковый текст",
            event_date=date(2026, 11, 10),
        )
        next_day = build_webhook_idempotency_key(
            "Одинаковый текст",
            event_date=date(2026, 11, 11),
        )

        self.assertNotEqual(first, next_day)

    def test_client_key_has_priority_over_sms_text_and_date(self):
        first = build_webhook_idempotency_key(
            "Первый текст",
            provided_key="ios-event-id",
            event_date=date(2026, 11, 10),
        )
        retry = build_webhook_idempotency_key(
            "Другой текст",
            provided_key="ios-event-id",
            event_date=date(2026, 11, 11),
        )

        self.assertEqual(first, retry)

    def test_different_client_keys_are_distinct(self):
        first = build_webhook_idempotency_key(
            "Текст",
            provided_key="event-one",
        )
        second = build_webhook_idempotency_key(
            "Текст",
            provided_key="event-two",
        )

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
