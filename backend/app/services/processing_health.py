"""Rules for summarizing asynchronous transaction processing health."""

import os
from typing import Literal


DEFAULT_QUEUE_STALE_MINUTES = 10


def get_queue_stale_minutes() -> int:
    """Read a safe positive stale-queue threshold from the environment."""
    raw_value = os.getenv(
        "QUEUE_STALE_MINUTES",
        str(DEFAULT_QUEUE_STALE_MINUTES),
    )
    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_QUEUE_STALE_MINUTES
    return value if value > 0 else DEFAULT_QUEUE_STALE_MINUTES


def determine_processing_health(
    *,
    queued: int,
    stale_queued: int,
    failed: int,
    unnotified_failures: int,
) -> Literal["ok", "degraded", "critical"]:
    """Classify operational health without treating business rejects as outages."""
    if stale_queued > 0 or unnotified_failures > 0:
        return "critical"
    if queued > 0 or failed > 0:
        return "degraded"
    return "ok"
