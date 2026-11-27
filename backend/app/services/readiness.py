"""Dependency-agnostic orchestration for bounded readiness checks."""

import asyncio
import os
from collections.abc import Awaitable, Callable


DEFAULT_READINESS_TIMEOUT_SECONDS = 5.0

ReadinessCheck = Callable[[], Awaitable[bool]]
ReadinessCheckConfig = tuple[ReadinessCheck, str, str]


def get_readiness_timeout_seconds() -> float:
    """Read a safe positive readiness timeout from the environment."""
    raw_value = os.getenv(
        "READINESS_TIMEOUT_SECONDS",
        str(DEFAULT_READINESS_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw_value)
    except ValueError:
        return DEFAULT_READINESS_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_READINESS_TIMEOUT_SECONDS


async def run_readiness_checks(
    checks: dict[str, ReadinessCheckConfig],
    *,
    timeout_seconds: float,
) -> dict[str, dict[str, str]]:
    """Run dependency checks concurrently with one timeout per dependency."""

    async def run_one(config: ReadinessCheckConfig) -> dict[str, str]:
        check, success_detail, failure_detail = config
        try:
            is_healthy = await asyncio.wait_for(
                check(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return {
                "status": "error",
                "detail": f"{failure_detail}: timeout",
            }
        except Exception as exc:
            return {
                "status": "error",
                "detail": f"{failure_detail}: {type(exc).__name__}",
            }

        return {
            "status": "ok" if is_healthy else "error",
            "detail": success_detail if is_healthy else failure_detail,
        }

    names = list(checks)
    results = await asyncio.gather(*(run_one(checks[name]) for name in names))
    return dict(zip(names, results))
