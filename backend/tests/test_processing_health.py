import os
import unittest
from unittest.mock import patch

from app.services.processing_health import (
    DEFAULT_QUEUE_STALE_MINUTES,
    determine_processing_health,
    get_queue_stale_minutes,
)


class ProcessingHealthTests(unittest.TestCase):
    def test_health_prioritizes_actionable_pipeline_problems(self):
        self.assertEqual(
            determine_processing_health(
                queued=0,
                stale_queued=0,
                failed=0,
                unnotified_failures=0,
            ),
            "ok",
        )
        self.assertEqual(
            determine_processing_health(
                queued=1,
                stale_queued=0,
                failed=0,
                unnotified_failures=0,
            ),
            "degraded",
        )
        self.assertEqual(
            determine_processing_health(
                queued=1,
                stale_queued=1,
                failed=0,
                unnotified_failures=0,
            ),
            "critical",
        )
        self.assertEqual(
            determine_processing_health(
                queued=0,
                stale_queued=0,
                failed=1,
                unnotified_failures=1,
            ),
            "critical",
        )

    def test_invalid_stale_threshold_falls_back_safely(self):
        for value in ("not-a-number", "0", "-5"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"QUEUE_STALE_MINUTES": value}):
                    self.assertEqual(
                        get_queue_stale_minutes(),
                        DEFAULT_QUEUE_STALE_MINUTES,
                    )

        with patch.dict(os.environ, {"QUEUE_STALE_MINUTES": "25"}):
            self.assertEqual(get_queue_stale_minutes(), 25)
