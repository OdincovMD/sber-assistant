"""
FinancialAnalyst — AI-анализ финансовой сводки через локальный LLM.

Генерирует персонализированные рекомендации на основе текущего состояния
кредитного лимита, грейс-периода, бонусных целей и дебетовых бюджетов.
"""

import json
import logging
from decimal import Decimal

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

ANALYST_SYSTEM_PROMPT = (
    "You are a strict, professional financial advisor. "
    "Analyze the provided financial JSON data. "
    "Your goal is to help the user: "
    "1) Avoid breaking the 120-day credit grace period. "
    "2) Hit the 100k bonus spending target. "
    "3) Stay within debit budgets. "
    "Provide exactly 3 short, actionable bullet points based on the current numbers. "
    "DO NOT use emojis. "
    "Use plain text and strictly professional Russian language."
)


class FinancialAnalyst:
    """Генератор финансовых рекомендаций на основе локального LLM."""

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.timeout = httpx.Timeout(300.0, connect=10.0)

    async def generate_advice(self, summary_data: dict) -> str:
        """
        Сгенерировать 3 конкретных финансовых совета на основе сводки.

        Args:
            summary_data: словарь из CreditCardService.get_financial_summary()

        Returns:
            Текстовые рекомендации от LLM (plain text, без эмодзи).
        """
        # Convert Decimal values to float for JSON serialization
        def convert_decimals(obj):
            if isinstance(obj, Decimal):
                return float(obj)
            elif isinstance(obj, dict):
                return {k: convert_decimals(v) for k, v in obj.items()}
            elif isinstance(obj, (list, tuple)):
                return [convert_decimals(item) for item in obj]
            return obj

        clean_data = convert_decimals(summary_data)
        summary_json = json.dumps(clean_data, ensure_ascii=False, indent=2)
        prompt = f"Финансовая сводка пользователя:\n\n{summary_json}"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": ANALYST_SYSTEM_PROMPT,
                        "stream": False,
                        "options": {
                            "temperature": 0.3,
                            "num_predict": 400,
                            "top_p": 0.9,
                            "repeat_penalty": 1.1,
                        },
                    },
                )
                response.raise_for_status()

            data = response.json()
            advice = data.get("response", "").strip()

            if not advice:
                logger.warning("FinancialAnalyst: LLM вернул пустой ответ")
                return "Недостаточно данных для формирования рекомендаций."

            logger.info(f"FinancialAnalyst: совет сгенерирован ({len(advice)} символов)")
            return advice

        except httpx.HTTPStatusError as e:
            logger.error(f"FinancialAnalyst HTTP error: {e.response.status_code} — {e.response.text}")
            return "Ошибка связи с LLM. Рекомендации недоступны."
        except Exception as e:
            logger.error(f"FinancialAnalyst request failed: {e}")
            return "Ошибка генерации рекомендаций."
