import unittest
from datetime import datetime, timezone

from app.services.failure_alerts import build_transaction_failure_alert


class TransactionFailureAlertTests(unittest.TestCase):
    def test_message_contains_operator_context_and_retry_action(self):
        message = build_transaction_failure_alert(
            transaction_id=42,
            created_at=datetime(2026, 11, 26, 17, 30, tzinfo=timezone.utc),
            error="Background processing failed: RuntimeError",
            recovery_attempts=2,
        )

        self.assertIn("ID: 42", message)
        self.assertIn("26.11.2026 20:30 МСК", message)
        self.assertIn("Автовосстановлений: 2", message)
        self.assertIn("Background processing failed: RuntimeError", message)
        self.assertIn("POST /api/transactions/42/retry", message)

    def test_error_is_normalized_and_bounded(self):
        message = build_transaction_failure_alert(
            transaction_id=7,
            created_at=datetime(2026, 11, 26, 20, 30),
            error="  Redis\n\n" + "x" * 700,
            recovery_attempts=3,
        )

        reason = next(
            line.removeprefix("Причина: ")
            for line in message.splitlines()
            if line.startswith("Причина: ")
        )
        self.assertEqual(len(reason), 500)
        self.assertTrue(reason.startswith("Redis x"))
        self.assertTrue(reason.endswith("…"))
        self.assertNotIn("\n\n", reason)
