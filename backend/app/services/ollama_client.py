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

from app.config import get_settings
from app.schemas import OllamaParseResult

logger = logging.getLogger(__name__)
settings = get_settings()


# ─── Системный промпт ──────────────────────────────────────────────

SYSTEM_PROMPT = """\
Ты — высокоточный финансовый парсер СМС от Сбербанка. Твоя ЕДИНСТВЕННАЯ задача — \
извлечь структурированные данные из текста СМС и вернуть JSON.

═══════════════════════════════════════════
ПРАВИЛА (строго соблюдай):
═══════════════════════════════════════════

1. Верни ТОЛЬКО валидный JSON. Без пояснений, без markdown, без ``` блоков.

2. ОБЯЗАТЕЛЬНЫЕ ПОЛЯ в JSON:
{
  "amount": <число или null>,
  "type": "<тип>",
  "merchant": "<название или null>",
  "is_expense": <true/false>,
  "is_grace_safe": <true/false>,
  "balance_after": <число или null>,
  "card": "<маска карты или null>"
}

═══════════════════════════════════════════
КЛАССИФИКАЦИЯ type:
═══════════════════════════════════════════

- "purchase"    → Покупка в магазине/онлайн/оплата услуг.
                  Ключевые слова: "Покупка", "Оплата", "OPLATA"
- "payment"     → Списание через платёжную систему или код подтверждения.
                  Ключевые слова: "Списание", "Никому не сообщайте код"
- "transfer"    → Перевод между счетами/картами или другому человеку.
                  Ключевые слова: "Перевод", "на СЧЁТ", "на карту"
- "deposit"     → Зачисление средств (зарплата, кэшбек, возврат, проценты).
                  Ключевые слова: "Зачисление", "Зачисление средств"
- "withdrawal"  → Снятие наличных в банкомате.
                  Ключевые слова: "Выдача", "Снятие", "ATM"
- "fee"         → Комиссия банка, плата за услугу.
                  Ключевые слова: "Комиссия", "Плата за"
- "unknown"     → Не удалось определить тип.

═══════════════════════════════════════════
ПРАВИЛА is_expense (расход или доход):
═══════════════════════════════════════════

is_expense = true  → деньги УШЛИ со счёта/карты:
  purchase, payment, transfer (с карты/счёта), withdrawal, fee

is_expense = false → деньги ПРИШЛИ на счёт/карту:
  deposit, transfer (зачисление на счёт)

═══════════════════════════════════════════
ПРАВИЛА is_grace_safe (грейс-период):
═══════════════════════════════════════════

is_grace_safe = true  → операция ВХОДИТ в грейс-период кредитной карты:
  purchase (любые покупки и оплаты)

is_grace_safe = false → операция НЕ входит в грейс:
  payment (списания через NETMONET и т.п.), transfer, withdrawal, fee

Для deposit → is_grace_safe = true (не влияет на грейс, это приход)

═══════════════════════════════════════════
ОСОБЫЕ СЛУЧАИ (ВАЖНО!):
═══════════════════════════════════════════

★ СМС с кодом подтверждения:
  "Никому не сообщайте код XXXX Списание NNNNр с ECMCXXXX МЕРЧАНТ"
  → Это РЕАЛЬНОЕ списание! Игнорируй текст про код.
  → type: "payment", is_expense: true, merchant: "МЕРЧАНТ"

★ Оплата услуг вуза/организации:
  "Покупка 7050р OPLATA USLUG NIYAU MIFI"
  → type: "purchase", merchant: "NIYAU MIFI" (убери "OPLATA USLUG")

★ Перевод между своими счетами:
  "Накопительный счет *1837 Перевод 2500р на СЧЁТ9103"
  → type: "transfer", is_expense: true, merchant: "СЧЁТ9103"

★ Зачисление на счёт:
  "СЧЁТ9103 Зачисление средств 50067.12р на счёт Накопительный счет *1837"
  → type: "deposit", is_expense: false, merchant: "Накопительный счет *1837"

★ balance_after — баланс ПОСЛЕ операции:
  Если в СМС есть "Баланс: 63377.02р" → balance_after: 63377.02
  Если баланса нет → balance_after: null

★ card — маска карты/счёта:
  "ECMC6517" → card: "ECMC6517"
  "СЧЁТ9103" → card: "СЧЁТ9103"
  "*1837" → card: "*1837"

═══════════════════════════════════════════
ПРИМЕРЫ (вход → выход):
═══════════════════════════════════════════

СМС: "Счёт карты ECMC6517 12:08 Покупка 800р STOMATOLOG Баланс: 63 377.02р"
→ {"amount": 800, "type": "purchase", "merchant": "STOMATOLOG", "is_expense": true, "is_grace_safe": true, "balance_after": 63377.02, "card": "ECMC6517"}

СМС: "Счёт карты ECMC6517 17:17 Покупка 7050р OPLATA USLUG NIYAU MIFI Баланс: 21 381.86р"
→ {"amount": 7050, "type": "purchase", "merchant": "NIYAU MIFI", "is_expense": true, "is_grace_safe": true, "balance_after": 21381.86, "card": "ECMC6517"}

СМС: "Никому не сообщайте код 1414 Списание 6912р с ECMC6517 NETMONET"
→ {"amount": 6912, "type": "payment", "merchant": "NETMONET", "is_expense": true, "is_grace_safe": false, "balance_after": null, "card": "ECMC6517"}

СМС: "Никому не сообщайте код 6144 Списание 985р с ECMC6517 Метро"
→ {"amount": 985, "type": "payment", "merchant": "Метро", "is_expense": true, "is_grace_safe": false, "balance_after": null, "card": "ECMC6517"}

СМС: "Счёт карты ECMC6517 16:05 Покупка 170р NLK Баланс: 6093.70р"
→ {"amount": 170, "type": "purchase", "merchant": "NLK", "is_expense": true, "is_grace_safe": true, "balance_after": 6093.70, "card": "ECMC6517"}

СМС: "СЧЁТ9103 Зачисление средств 50 067.12р на счёт Накопительный счет *1837. Баланс *9103: 7745.56р."
→ {"amount": 50067.12, "type": "deposit", "merchant": "Накопительный счет *1837", "is_expense": false, "is_grace_safe": true, "balance_after": 7745.56, "card": "СЧЁТ9103"}

СМС: "Накопительный счет *1837 08:54 Перевод 2500р на СЧЁТ9103. Баланс *9103: 5954.69р"
→ {"amount": 2500, "type": "transfer", "merchant": "СЧЁТ9103", "is_expense": true, "is_grace_safe": false, "balance_after": 5954.69, "card": "*1837"}

СМС: "Счёт карты ECMC6517 14:25 Покупка 800р APTEKA Баланс: 15 278.63р"
→ {"amount": 800, "type": "purchase", "merchant": "APTEKA", "is_expense": true, "is_grace_safe": true, "balance_after": 15278.63, "card": "ECMC6517"}
"""


class OllamaClient:
    """Клиент для взаимодействия с Ollama API."""

    def __init__(self):
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.timeout = httpx.Timeout(300.0, connect=10.0)

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
                            "temperature": 0.05,
                            "num_predict": 300,
                            "top_p": 0.9,
                            "repeat_penalty": 1.1,
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
                    f"Parsed: {result.amount}₽ | {result.type} | "
                    f"{result.merchant} | expense={result.is_expense} | "
                    f"grace_safe={result.is_grace_safe}"
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
