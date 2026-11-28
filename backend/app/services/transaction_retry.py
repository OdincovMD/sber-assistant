"""State transitions shared by individual and bulk transaction retries."""

from datetime import datetime


def prepare_transaction_for_retry(transaction, *, queued_at: datetime) -> None:
    """Reset terminal processing metadata before publishing a new task."""
    transaction.processing_status = "queued"
    transaction.processing_error = None
    transaction.processed_at = None
    transaction.queued_at = queued_at
    transaction.queue_recovery_attempts = 0
    transaction.failure_notified_at = None


def mark_retry_queue_failure(
    transaction,
    *,
    failed_at: datetime,
    error_type: str,
) -> None:
    """Restore a retry to failed when its Celery task could not be published."""
    transaction.processing_status = "failed"
    transaction.processing_error = (
        f"Background queue unavailable: {error_type}"
    )
    transaction.processed_at = failed_at
