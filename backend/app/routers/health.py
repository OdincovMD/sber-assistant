import logging

from fastapi import APIRouter

from app.db import AsyncORM
from app.schemas import HealthResponse
from app.services.ollama_client import ollama_client

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
