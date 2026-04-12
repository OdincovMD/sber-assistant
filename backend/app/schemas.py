from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AccountType(str, Enum):
    """Тип банковского счёта/карты."""
    CREDIT = "credit"
    DEBIT = "debit"
    SAVINGS = "savings"


# ─── Request ───────────────────────────────────────────────────

class SmsWebhookRequest(BaseModel):
    """Входящий вебхук от iOS Shortcuts с текстом СМС."""
    sms_text: str = Field(..., min_length=1, max_length=2000, description="Текст СМС от Сбера")


# ─── Ollama ────────────────────────────────────────────────────

class OllamaParseResult(BaseModel):
    """Результат парсинга СМС через Ollama LLM (v2 — мульти-аккаунт)."""
    card_tail: Optional[str] = Field(None, description="Последние 4 цифры карты/счёта (7600, 6517, 7757, 1837)")
    account_type: Optional[str] = Field(None, description="Тип счёта: credit / debit / savings")
    amount: Optional[Decimal] = Field(None, description="Сумма транзакции")
    merchant: Optional[str] = Field(None, description="Получатель / мерчант / источник")
    category: Optional[str] = Field(None, description="Категория операции (Продукты, Здоровье, Перевод между счетами, ...)")
    is_expense: Optional[bool] = Field(None, description="True = расход (списание), False = доход (пополнение)")
    balance_after: Optional[Decimal] = Field(None, description="Баланс после операции")

    # Обратная совместимость (legacy-поля, заполняются из card_tail + account_type)
    type: Optional[str] = Field(None, description="Тип: purchase / payment / transfer / deposit / withdrawal / fee / unknown")
    is_grace_safe: Optional[bool] = Field(None, description="Безопасна ли для грейс-периода")
    card: Optional[str] = Field(None, description="Маска карты/счёта (ECMC6517, СЧЁТ9103, *1837)")


# ─── Response ──────────────────────────────────────────────────

class SmsWebhookResponse(BaseModel):
    """Ответ на вебхук. Обработка происходит асинхронно в Celery."""
    status: str = "queued"
    transaction_id: Optional[int] = None


class HealthResponse(BaseModel):
    """Ответ health check."""
    status: str
    detail: Optional[str] = None


# ─── Finance ──────────────────────────────────────────────────

class BonusStatusResponse(BaseModel):
    """Статус бонусного порога трат."""
    month: str
    total_spent: float
    target: float
    remaining: float
    is_target_reached: bool
    progress_percent: float


class SpendingStatsResponse(BaseModel):
    """Статистика реальных трат (без переводов себе)."""
    daily: float
    weekly: float
    monthly: float


class NetWorthResponse(BaseModel):
    """Чистый капитал."""
    savings_balance: float
    investment_value: float
    credit_debt: float
    total: float


class FinancialSummaryResponse(BaseModel):
    """Полная финансовая сводка."""
    available_limit: float
    credit_limit: float
    credit_usage_percent: float
    total_unpaid: float
    bonus_status: BonusStatusResponse
    open_periods: list[dict]
    spending_stats: SpendingStatsResponse
    latest_savings_balance: float
    debit_monthly_limit: float
    net_worth: NetWorthResponse
