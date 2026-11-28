import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.transaction_retry import (
    mark_retry_queue_failure,
    prepare_transaction_for_retry,
)


class TransactionRetryStateTests(unittest.TestCase):
    def test_prepare_resets_terminal_and_notification_metadata(self):
        transaction = SimpleNamespace(
            processing_status="failed",
            processing_error="old error",
            processed_at=datetime.now(timezone.utc),
            queued_at=None,
            queue_recovery_attempts=3,
            failure_notified_at=datetime.now(timezone.utc),
        )
        queued_at = datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc)

        prepare_transaction_for_retry(transaction, queued_at=queued_at)

        self.assertEqual(transaction.processing_status, "queued")
        self.assertIsNone(transaction.processing_error)
        self.assertIsNone(transaction.processed_at)
        self.assertEqual(transaction.queued_at, queued_at)
        self.assertEqual(transaction.queue_recovery_attempts, 0)
        self.assertIsNone(transaction.failure_notified_at)

    def test_queue_failure_restores_terminal_state(self):
        transaction = SimpleNamespace(
            processing_status="queued",
            processing_error=None,
            processed_at=None,
        )
        failed_at = datetime(2026, 11, 28, 12, 1, tzinfo=timezone.utc)

        mark_retry_queue_failure(
            transaction,
            failed_at=failed_at,
            error_type="RuntimeError",
        )

        self.assertEqual(transaction.processing_status, "failed")
        self.assertEqual(
            transaction.processing_error,
            "Background queue unavailable: RuntimeError",
        )
        self.assertEqual(transaction.processed_at, failed_at)
