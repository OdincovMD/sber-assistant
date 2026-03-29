from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


# ─── Request ───────────────────────────────────────────────────

class SmsWebhookRequest(BaseModel):
    """Входящий вебхук от iOS Shortcuts с текстом СМС."""
    sms_text: str = Field(..., min_length=1, max_length=2000, description="Текст СМС от Сбера")


# ─── Ollama ────────────────────────────────────────────────────

class OllamaParseResult(BaseModel):
    """Результат парсинга СМС через Ollama LLM."""
    amount: Optional[Decimal] = Field(None, description="Сумма транзакции")
    type: Optional[str] = Field(
        None,
        description="Тип: purchase / payment / transfer / deposit / withdrawal / fee / unknown",
    )
    merchant: Optional[str] = Field(None, description="Получатель / мерчант / источник")
    is_expense: Optional[bool] = Field(None, description="True = расход, False = доход")
    is_grace_safe: Optional[bool] = Field(None, description="Безопасна ли для грейс-периода")
    balance_after: Optional[Decimal] = Field(None, description="Баланс после операции")
    card: Optional[str] = Field(None, description="Маска карты/счёта (ECMC6517, СЧЁТ9103, *1837)")


# ─── Response ──────────────────────────────────────────────────

class SmsWebhookResponse(BaseModel):
    """Ответ на вебхук."""
    status: str = "received"
    transaction_id: Optional[int] = None
    parsed_data: Optional[OllamaParseResult] = None
    grace_deadline: Optional[str] = None
    billing_month: Optional[str] = None


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


class FinancialSummaryResponse(BaseModel):
    """Полная финансовая сводка."""
    available_limit: float
    credit_limit: float
    bonus_status: BonusStatusResponse
    open_periods: list[dict]
