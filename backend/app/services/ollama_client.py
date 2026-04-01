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
  "card_tail": "<последние 4 цифры карты/счёта>",
  "account_type": "<credit | debit | savings>",
  "amount": <число или null>,
  "merchant": "<название или null>",
  "category": "<категория операции>",
  "is_expense": <true/false>,
  "balance_after": <число или null>
}

═══════════════════════════════════════════
МАРШРУТИЗАЦИЯ ПО СЧЕТАМ (КРИТИЧНО!):
═══════════════════════════════════════════

★ МИР7600 или просто 7600 → card_tail: "7600", account_type: "credit"
  Кредитная карта, лимит 150 000₽

★ ECMC6517 или 6517 → card_tail: "6517", account_type: "debit"
  Дебетовая карта

★ Накопительный счет *1837 или *1837 → card_tail: "1837", account_type: "savings"
  Накопительный счёт

Если в тексте упоминаются СЧЁТ9103 или другие счета — это промежуточные счета,
определяй card_tail по ОСНОВНОЙ карте/счёту, с которого или на который идёт операция.

═══════════════════════════════════════════
КАТЕГОРИИ:
═══════════════════════════════════════════

Используй одну из категорий:
- "Продукты" — PYATEROCHKA, PEREKRESTOK, MAGNIT и т.п.
- "Здоровье" — APTEKA, STOMATOLOG, клиники
- "Транспорт" — Метро, такси, топливо
- "Рестораны" — кафе, рестораны, фастфуд
- "Образование" — MIFI, NIYAU, вузы, курсы
- "Онлайн-сервисы" — NLK, подписки, стриминг
- "Платёжные системы" — NETMONET, SBP переводы
- "Перевод между счетами" — переводы между своими счетами
- "Зачисление" — зарплата, кэшбек, возврат, проценты
- "Снятие наличных" — ATM, банкоматы
- "Комиссия" — банковские комиссии
- "Другое" — если не подходит ни одна

═══════════════════════════════════════════
ПРАВИЛА is_expense (расход или доход):
═══════════════════════════════════════════

is_expense = true  → деньги УШЛИ со счёта/карты:
  покупки, оплаты, списания, переводы (со счёта), снятие наличных, комиссии

is_expense = false → деньги ПРИШЛИ на счёт/карту:
  зачисления, возвраты, переводы (на счёт)

═══════════════════════════════════════════
ОСОБЫЕ СЛУЧАИ (ВАЖНО!):
═══════════════════════════════════════════

★ СМС с кодом подтверждения:
  "Никому не сообщайте код XXXX Списание NNNNр с ECMCXXXX МЕРЧАНТ"
  → Это РЕАЛЬНОЕ списание! Игнорируй текст про код.
  → is_expense: true, merchant: "МЕРЧАНТ"

★ Оплата услуг вуза/организации:
  "Покупка 7050р OPLATA USLUG NIYAU MIFI"
  → merchant: "NIYAU MIFI" (убери "OPLATA USLUG"), category: "Образование"

★ Перевод между своими счетами:
  "Накопительный счет *1837 Перевод 2500р на СЧЁТ9103"
  → card_tail: "1837", account_type: "savings", is_expense: true,
    category: "Перевод между счетами"

★ Зачисление на счёт:
  "СЧЁТ9103 Зачисление средств 50067.12р на счёт Накопительный счет *1837"
  → card_tail: "1837", account_type: "savings", is_expense: false,
    category: "Зачисление"

★ balance_after — баланс ПОСЛЕ операции:
  Если в СМС есть "Баланс: 63377.02р" → balance_after: 63377.02
  Если баланса нет → balance_after: null

═══════════════════════════════════════════
ПРИМЕРЫ (вход → выход):
═══════════════════════════════════════════

СМС: "Счёт карты ECMC6517 12:08 Покупка 800р STOMATOLOG Баланс: 63 377.02р"
→ {"card_tail": "6517", "account_type": "debit", "amount": 800.0, "merchant": "STOMATOLOG", "category": "Здоровье", "is_expense": true, "balance_after": 63377.02}

СМС: "Никому не сообщайте код 1414 Списание 6912р с ECMC6517 NETMONET"
→ {"card_tail": "6517", "account_type": "debit", "amount": 6912.0, "merchant": "NETMONET", "category": "Платёжные системы", "is_expense": true, "balance_after": null}

СМС: "Счёт карты ECMC6517 16:05 Покупка 170р NLK Баланс: 6093.70р"
→ {"card_tail": "6517", "account_type": "debit", "amount": 170.0, "merchant": "NLK", "category": "Онлайн-сервисы", "is_expense": true, "balance_after": 6093.70}

СМС: "СЧЁТ9103 Зачисление средств 50 067.12р на счёт Накопительный счет *1837. Баланс *9103: 7745.56р."
→ {"card_tail": "1837", "account_type": "savings", "amount": 50067.12, "merchant": "СЧЁТ9103", "category": "Зачисление", "is_expense": false, "balance_after": 7745.56}

СМС: "Накопительный счет *1837 08:54 Перевод 2500р на СЧЁТ9103. Баланс *9103: 5954.69р"
→ {"card_tail": "1837", "account_type": "savings", "amount": 2500.0, "merchant": "СЧЁТ9103", "category": "Перевод между счетами", "is_expense": true, "balance_after": 5954.69}

СМС: "МИР7600 15:30 Покупка 3200р PYATEROCHKA Баланс: 146 800.00р"
→ {"card_tail": "7600", "account_type": "credit", "amount": 3200.0, "merchant": "PYATEROCHKA", "category": "Продукты", "is_expense": true, "balance_after": 146800.0}
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
