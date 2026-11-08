"""
Ollama Client — взаимодействие с LLM для парсинга СМС Сбербанка.

Включает мощный системный промпт, обученный на реальных форматах
СМС Сбера (покупки, списания, коды подтверждения, зачисления, переводы).
"""

import json
import logging
import re
from typing import Optional

import httpx

from app.config import ACCOUNTS, get_settings
from app.schemas import OllamaParseResult

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── Системный промпт ──────────────────────────────────────────────

ACCOUNT_RULES = ", ".join(
    f"{tail}→{metadata['type']}" for tail, metadata in ACCOUNTS.items()
)

SYSTEM_PROMPT = f"""\
Парсер СМС Сбербанка. Верни ТОЛЬКО JSON без пояснений.

ПОЛЯ:
{{"card_tail":"<4 цифры>","account_type":"<credit|debit|savings>","amount":<число|null>,"merchant":"<название|null>","category":"<категория>","type":"<purchase|payment|transfer|deposit|withdrawal|fee|unknown>","is_expense":<true|false>,"balance_after":<число|null>}}

СЧЕТА: {ACCOUNT_RULES}. Если в сообщении есть промежуточный и основной счёт, используй основной счёт.

КАТЕГОРИИ: Продукты, Здоровье, Транспорт, Рестораны, Образование, Онлайн-сервисы, Платёжные системы, Перевод между счетами, Зачисление, Снятие наличных, Комиссия, Другое.

is_expense=true: покупка/списание/перевод со счёта/снятие/комиссия.
is_expense=false: зачисление/возврат/перевод на счёт.

ТИП ОПЕРАЦИИ:
- purchase: покупка или обычное списание за товар/услугу;
- payment: любое зачисление на кредитную карту, включая погашение и возврат покупки;
- transfer: исходящий перевод;
- deposit: зачисление на дебетовый или накопительный счёт;
- withdrawal: снятие наличных;
- fee: комиссия банка;
- unknown: только если тип невозможно определить.

ВАЖНО: "Никому не сообщайте код XXXX Списание NNNNр с ECMCXXXX МЕРЧАНТ" — реальное списание, is_expense=true.
balance_after: число из "Баланс: Xр", иначе null.

ПРИМЕРЫ КЛАССИФИКАЦИИ:
- покупка по кредитной или дебетовой карте → type=purchase, is_expense=true;
- зачисление на кредитную карту → type=payment, is_expense=false;
- зачисление на дебетовый или накопительный счёт → type=deposit, is_expense=false.
"""


class OllamaClient:
    """Клиент для взаимодействия с Ollama API."""

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.timeout = httpx.Timeout(120.0, connect=10.0)

    async def health_check(self) -> bool:
        """Проверка доступности Ollama."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Ollama health check failed: {e}")
            return False

    async def is_model_loaded(self) -> bool:
        """Проверить, загружена ли нужная модель."""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    models = response.json().get("models", [])
                    return any(m.get("name", "").startswith(self.model) for m in models)
        except Exception as e:
            logger.error(f"Model check failed: {e}")
        return False

    async def parse_sms(self, sms_text: str) -> tuple[Optional[OllamaParseResult], Optional[str]]:
        """
        Парсинг СМС через Ollama LLM.

        Returns:
            tuple: (parsed_result, raw_response)
                - parsed_result: OllamaParseResult или None при ошибке
                - raw_response: сырой текст ответа от LLM
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": f"Распарси эту СМС от Сбербанка:\n\n{sms_text}",
                        "system": SYSTEM_PROMPT,
                        "stream": False,
                        "format": "json",
                        "options": {
                            "temperature": 0.0,
                            "num_predict": 200,
                            "num_ctx": 512,
                            "repeat_penalty": 1.0,
                        },
                    },
                )
                response.raise_for_status()

            data = response.json()
            raw_response = data.get("response", "")
            logger.info(f"Ollama raw response: {raw_response}")

            # Пробуем извлечь JSON из ответа
            parsed = self._extract_json(raw_response)
            if parsed:
                result = OllamaParseResult(**parsed)
                logger.info(
                    f"Parsed: {result.card_tail} ({result.account_type}) | "
                    f"{result.amount}₽ | {result.merchant} | "
                    f"cat={result.category} | expense={result.is_expense}"
                )
                return result, raw_response

            logger.warning(f"Could not parse Ollama response as JSON: {raw_response}")
            return None, raw_response

        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP error: {e.response.status_code} — {e.response.text}")
            return None, None
        except Exception as e:
            logger.error(f"Ollama request failed: {e}")
            return None, None

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Извлечь JSON из текста ответа LLM (может содержать мусор вокруг JSON)."""
        # Попытка 1: весь текст — валидный JSON
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Попытка 2: JSON внутри ```json ... ``` блока
        match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Попытка 3: первый { ... } в тексте (поддержка вложенных скобок)
        depth = 0
        start = None
        for i, ch in enumerate(text):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        start = None

        return None


# Синглтон клиента
ollama_client = OllamaClient()
