import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from app.services.readiness import (
    DEFAULT_READINESS_TIMEOUT_SECONDS,
    get_readiness_timeout_seconds,
    run_readiness_checks,
)


class ReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_checks_run_concurrently_and_preserve_details(self):
        healthy = AsyncMock(return_value=True)
        unhealthy = AsyncMock(return_value=False)

        result = await run_readiness_checks(
            {
                "healthy": (healthy, "connected", "unreachable"),
                "unhealthy": (unhealthy, "connected", "unreachable"),
            },
            timeout_seconds=1,
        )

        self.assertEqual(
            result,
            {
                "healthy": {"status": "ok", "detail": "connected"},
                "unhealthy": {"status": "error", "detail": "unreachable"},
            },
        )
        healthy.assert_awaited_once()
        unhealthy.assert_awaited_once()

    async def test_timeout_and_exception_are_isolated(self):
        async def hangs():
            await asyncio.sleep(1)
            return True

        async def crashes():
            raise RuntimeError("secret details must not leak")

        result = await run_readiness_checks(
            {
                "slow": (hangs, "ready", "slow dependency"),
                "broken": (crashes, "ready", "broken dependency"),
            },
            timeout_seconds=0.01,
        )

        self.assertEqual(
            result["slow"],
            {"status": "error", "detail": "slow dependency: timeout"},
        )
        self.assertEqual(
            result["broken"],
            {"status": "error", "detail": "broken dependency: RuntimeError"},
        )
        self.assertNotIn("secret details", result["broken"]["detail"])

    def test_invalid_timeout_falls_back_safely(self):
        for value in ("invalid", "0", "-1"):
            with self.subTest(value=value):
                with patch.dict(os.environ, {"READINESS_TIMEOUT_SECONDS": value}):
                    self.assertEqual(
                        get_readiness_timeout_seconds(),
                        DEFAULT_READINESS_TIMEOUT_SECONDS,
                    )

        with patch.dict(os.environ, {"READINESS_TIMEOUT_SECONDS": "2.5"}):
            self.assertEqual(get_readiness_timeout_seconds(), 2.5)
