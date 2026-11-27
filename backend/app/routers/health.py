import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

from app.config import get_settings
from app.db import AsyncORM
from app.schemas import HealthResponse, ReadinessResponse
from app.services.ollama_client import ollama_client
from app.services.readiness import (
    get_readiness_timeout_seconds,
    run_readiness_checks,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Базовый health check — backend жив."""
    return HealthResponse(status="ok")


@router.get("/health/db", response_model=HealthResponse)
async def health_db():
    """Проверка подключения к PostgreSQL."""
    is_healthy = await AsyncORM.health_check()
    if is_healthy:
        return HealthResponse(status="ok", detail="PostgreSQL connected")
    return HealthResponse(status="error", detail="PostgreSQL unreachable")


@router.get("/health/ollama", response_model=HealthResponse)
async def health_ollama():
    """Проверка подключения к Ollama."""
    is_healthy = await ollama_client.health_check()
    if is_healthy:
        return HealthResponse(status="ok", detail="Ollama connected")
    return HealthResponse(status="error", detail="Ollama unreachable")


async def _redis_health_check() -> bool:
    """Ping Redis without keeping an extra connection pool alive."""
    from redis.asyncio import Redis

    redis_client = Redis.from_url(get_settings().redis_url)
    try:
        return bool(await redis_client.ping())
    finally:
        await redis_client.aclose()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ReadinessResponse}},
)
async def health_ready(response: Response):
    """Проверить готовность БД, очереди и модели обработки СМС."""
    timeout_seconds = get_readiness_timeout_seconds()
    checks = await run_readiness_checks(
        {
            "postgres": (
                AsyncORM.health_check,
                "PostgreSQL connected",
                "PostgreSQL unreachable",
            ),
            "redis": (
                _redis_health_check,
                "Redis connected",
                "Redis unreachable",
            ),
            "ollama_model": (
                ollama_client.is_model_loaded,
                "Ollama model loaded",
                "Ollama model unavailable",
            ),
        },
        timeout_seconds=timeout_seconds,
    )
    is_ready = all(check["status"] == "ok" for check in checks.values())
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if is_ready else "not_ready",
        checked_at=datetime.now(timezone.utc),
        timeout_seconds=timeout_seconds,
        checks=checks,
    )
