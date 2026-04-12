import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import AsyncORM
from app.routers import health, webhook, finance, investment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: инициализация БД при старте, закрытие при остановке."""
    logger.info("Sber Grace Assistant — Backend starting...")
    await AsyncORM.init()
    logger.info("Backend ready")
    yield
    await AsyncORM.close()
    logger.info("Backend shutting down...")


app = FastAPI(
    title="Sber Grace Assistant",
    description="Мониторинг финансов и грейс-периода кредитной карты Сбера",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(webhook.router)
app.include_router(finance.router)
app.include_router(investment.router)
