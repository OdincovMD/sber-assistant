# Отчёт о тестировании — Sber Grace Assistant
**Дата:** 2026-04-11  
**Версия:** 0.1.0  
**Тестировал:** Claude Code (автоматизированное полное тестирование)

---

## 1. Окружение

| Компонент | Статус | Версия/Детали |
|-----------|--------|---------------|
| backend | healthy | FastAPI, uvicorn |
| postgres | healthy | PostgreSQL 16 |
| ollama | healthy | qwen2.5:1.5b |
| redis | healthy | Redis 7 |
| celery | running | Worker + Beat |
| nginx | healthy | порт 80 |

---

## 2. Результаты тестирования по группам

### 2.1 Health Checks

| Эндпоинт | HTTP | Результат |
|----------|------|-----------|
| `GET /health` | 200 | `{"status": "ok"}` |
| `GET /health/db` | 200 | `{"status": "ok", "detail": "PostgreSQL connected"}` |
| `GET /health/ollama` | 200 | `{"status": "ok", "detail": "Ollama connected"}` |

**Итог:** ✅ Все 3/3 прошли

---

### 2.2 Finance API — GET-эндпоинты

| Эндпоинт | HTTP | Результат |
|----------|------|-----------|
| `GET /api/finance/summary` | 200 | Полная финансовая сводка (5 обязательных полей — OK) |
| `GET /api/finance/limit` | 200 | `available=146800, limit=150000, used=2.1%` |
| `GET /api/finance/bonus` | 200 | `3200₽/50000₽ (6.4%), до цели: 46800₽` |
| `GET /api/finance/periods` | 200 | 1 открытый период: 2026-04, дедлайн 2026-07-31 |
| `GET /api/finance/budgets` | 200 | Пустой список (до добавления бюджетов) |
| `GET /api/finance/spending/by-category` | 200 | 3 категории: Образование, Платёжные системы, Здоровье |

**Итог:** ✅ Все 6/6 прошли

---

### 2.3 Finance API — Управление бюджетами

| Сценарий | HTTP | Результат |
|----------|------|-----------|
| `POST /api/finance/budgets/Продукты?limit=15000` | 200 | Бюджет создан: 15000₽/мес |
| `POST /api/finance/budgets/Здоровье?limit=5000` | 200 | Бюджет создан: 5000₽/мес |
| `GET /api/finance/budgets` (после добавления) | 200 | 2 бюджета, ID=1 и ID=2 |

**Итог:** ✅ Все 3/3 прошли

---

### 2.4 Webhook — Приём СМС

| Сценарий | SMS-текст | HTTP | Ответ |
|----------|-----------|------|-------|
| Покупка по кредитной карте | `ECMC7600 02.04.26 Покупка 1500р ПЯТЕРОЧКА...` | 200 | `{status: "queued", transaction_id: 14}` |
| Зачисление на кредитную карту | `ECMC7600 02.04.26 ЗАЧИСЛЕНИЕ 5000р...` | 200 | `{status: "queued", transaction_id: 15}` |
| Пополнение накопительного счёта | `СЧЁТ1837 02.04.26 Пополнение 10000р...` | 200 | `{status: "queued", transaction_id: 16}` |
| Перевод (grace-unsafe) | `ECMC7600 02.04.26 Перевод 3000р ИВАНОВ ИВАН...` | 200 | `{status: "queued", transaction_id: 17}` |
| Покупка по дебетовой карте | `ECMC6517 02.04.26 Покупка 890р АПТЕКА 36.6...` | 200 | `{status: "queued", transaction_id: 18}` |
| Комиссия (grace-unsafe) | `ECMC7600 01.04.26 Комиссия за обслуживание 99р...` | 200 | `{status: "queued", transaction_id: 19}` |

**Итог:** ✅ Все 6/6 приняты в очередь

---

### 2.5 Webhook — Валидация входных данных

| Сценарий | Ожидаемый результат | Фактический HTTP | Вердикт |
|----------|---------------------|------------------|---------|
| Пустая строка `sms_text: ""` | 422 Validation Error | 422 | ✅ |
| Отсутствует поле `sms_text` | 422 Validation Error | 422 | ✅ |
| Текст > 2000 символов | 422 Validation Error | 422 | ✅ |
| `GET /api/nonexistent` | 404 Not Found | 404 | ✅ |
| `DELETE /api/finance/summary` | 405 Method Not Allowed | 405 | ✅ |

**Итог:** ✅ Все 5/5 прошли

---

### 2.6 Investment API — Портфель

| Сценарий | HTTP | Результат |
|----------|------|-----------|
| `GET /api/investment/portfolio` (пустой) | 200 | `total_invested: 0, prices_stale: true` |
| `POST /api/investment/lots` SBMM (100 × 15.50₽, 2024-01-15) | 201 | `id=1, ldv_date=2027-01-15, invested=1550₽` |
| `POST /api/investment/lots` SBGB (50 × 125₽, 2023-06-10) | 201 | `id=2, ldv_date=2026-06-10, invested=6250₽` |
| `POST /api/investment/price` SBMM → 16.25₽ | 200 | `source="manual"` |
| `POST /api/investment/price` SBGB → 130.50₽ | 200 | `source="manual"` |
| `GET /api/investment/portfolio` (после) | 200 | `total_invested=7800, current=8150, P&L=+350₽ (+4.49%), tax_if_sold=45.50₽` |

**Итог:** ✅ Все 6/6 прошли

#### Детали по тикерам в портфеле:
- **SBGB**: 6250₽ → 6525₽, P&L +275₽ (+4.4%), доля 80.1%, НДФЛ к уплате 35.75₽
- **SBMM**: 1550₽ → 1625₽, P&L +75₽ (+4.84%), доля 19.9%, НДФЛ к уплате 9.75₽

---

### 2.7 Investment API — ЛДВ-календарь

| Лот | Дней до ЛДВ | Alert Level | Вердикт |
|-----|-------------|-------------|---------|
| SBGB (2023-06-10) | 60 | `warn_90d` (менее 3 мес.) | ✅ Корректно |
| SBMM (2024-01-15) | 279 | `ok` (более 90 дней) | ✅ Корректно |

**Итог:** ✅ Все 2/2 лота с правильными уровнями тревоги

---

### 2.8 Investment API — Ликвидность для grace period

| Параметр | Значение | Вердикт |
|----------|----------|---------|
| T+1 доступно | 8150₽ | ✅ |
| T+4 доступно | 0₽ | ✅ |
| Нужно для grace period | 3200₽ | ✅ |
| `can_cover_t1` | true | ✅ |
| Рекомендация | "SBMM и БПИФ покрывают долг: 8 150 ₽ T+1 (нужно 3 200 ₽)" | ✅ |

**Итог:** ✅ Ликвидный анализ работает корректно

---

### 2.9 Investment API — Сравнение SBMM vs накопительный

| Параметр | Значение | Вердикт |
|----------|----------|---------|
| SBMM доходность | null (нет RUONIA от ЦБ) | ⚠️ ЦБ API недоступен |
| Накопительный счёт | 11.5% годовых | ✅ |
| Баланс накопительного | 5954.69₽ | ✅ |
| Годовой доход | 684.79₽ | ✅ |
| Победитель | null (нет данных RUONIA) | ⚠️ Частично |

**Итог:** ⚠️ Логика сравнения корректна, но ЦБ API (`cbr.ru`) не отдаёт данные (rate=null, ruonia=null)

---

### 2.10 Investment API — Данные ЦБ РФ

| Параметр | Значение | Вердикт |
|----------|----------|---------|
| `key_rate` | null | ⚠️ ЦБ API недоступен |
| `ruonia` | null | ⚠️ ЦБ API недоступен |
| `next_meeting` | 2026-04-25 | ✅ |
| `days_to_meeting` | 14 | ✅ |

**Итог:** ⚠️ Расписание заседаний работает, внешний API ЦБ РФ недоступен из контейнера

---

### 2.11 Investment API — Валидация

| Сценарий | Ожидаемый HTTP | Фактический HTTP | Вердикт |
|----------|----------------|------------------|---------|
| Неизвестный тикер `INVALID` | 400 | 400 | ✅ |
| `quantity: 0` (нарушение gt>0) | 422 | 422 | ✅ |
| Отрицательная цена `price: -5` | 422 | 422 | ✅ |

**Итог:** ✅ Все 3/3 прошли

---

### 2.12 Бизнес-логика: расчёт grace deadline

Модульный тест `calculate_grace_deadline()` — все 10 граничных случаев:

| Дата покупки | Ожидаемый дедлайн | Результат |
|-------------|-------------------|-----------|
| 01.04.2026 | 31.07.2026 | ✅ PASS |
| 15.04.2026 | 31.07.2026 | ✅ PASS |
| 15.05.2026 | 31.08.2026 | ✅ PASS |
| 01.01.2026 | 30.04.2026 | ✅ PASS |
| 01.11.2026 | 28.02.2027 | ✅ PASS |
| 01.11.2024 | 28.02.2025 | ✅ PASS |
| 01.11.2028 | 28.02.2029 (не високосный) | ✅ PASS |
| 31.12.2024 | 31.03.2025 | ✅ PASS |
| 15.06.2026 | 30.09.2026 | ✅ PASS |
| 01.10.2026 | 31.01.2027 | ✅ PASS |

**Итог:** ✅ Все 10/10 пройдено

---

### 2.13 CORS и заголовки безопасности

| Проверка | Результат | Вердикт |
|----------|-----------|---------|
| `Access-Control-Allow-Origin` | `http://example.com` (echo back) | ✅ |
| `Access-Control-Allow-Methods` | `DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT` | ✅ |
| `Access-Control-Allow-Credentials` | `true` | ✅ |

**Итог:** ✅ CORS настроен. Примечание: `allow_origins=["*"]` — разрешены все источники (ожидаемо для локального сервиса)

---

### 2.14 Производительность (время ответа)

| Эндпоинт | Время ответа |
|----------|-------------|
| `GET /health` | 3 мс |
| `GET /api/finance/limit` | 7 мс |
| `GET /api/finance/periods` | 6 мс |
| `GET /api/investment/portfolio` | 7 мс |
| `GET /api/investment/ldv` | 7 мс |
| `GET /api/investment/liquidation` | 10 мс |
| `GET /api/finance/summary` | 30 мс |

**Итог:** ✅ Все эндпоинты отвечают < 50 мс

---

## 3. Обнаруженные баги

### 🔴 BUG-01 — КРИТИЧЕСКИЙ: Celery не коммитит сессию БД

**Где:** `backend/app/tasks/celery_worker.py`, функция `process_sms_task` (и все другие Celery-задачи)

**Описание:**  
В Celery-задачах используется `async with AsyncORM.get_session()() as session:` без явного `await session.commit()`. SQLAlchemy не делает автоматический коммит при выходе из контекстного менеджера сессии — данные flush'атся в память, но в БД не записываются. При закрытии сессии выполняется ROLLBACK.

**Подтверждение из логов:**
```
[20:33:04] Parsed: 7600 (credit) | 99.0₽ | cat=Комиссия | expense=True  ← LLM отработал
[20:33:04] GRACE UNSAFE! Льготный период под угрозой!                    ← логика отработала
[20:33:05] ROLLBACK                                                        ← данные откатились!
```

**Подтверждение из БД:**  
Транзакции 14–19 присланы и прочитаны LLM (`is_parsed` должен быть `true`), но в БД:
```sql
id | is_parsed | account_type | amount
14 | f         | (null)       | (null)   ← данные не сохранились
...
19 | f         | (null)       | (null)
```

**Влияние:**  
- Все входящие СМС парсятся, но результаты парсинга **не сохраняются** в БД
- `billing_period.total_spent` не обновляется через Celery (работает только при прямых запросах через FastAPI)
- Grace-unsafe алармы генерируются корректно (в логах), но данные о транзакциях теряются
- Статистика расходов на основе SMS не аккумулируется

**Исправление:**  
Добавить `await session.commit()` в каждую Celery-задачу перед закрытием сессии:

```python
# В process_sms_task и других задачах:
async with AsyncORM.get_session()() as session:
    try:
        # ... логика ...
        await session.commit()   # ← ДОБАВИТЬ
    except Exception:
        await session.rollback()
        raise
```

---

### 🟡 BUG-02 — СРЕДНИЙ: ЦБ РФ API возвращает null для ключевой ставки и RUONIA

**Где:** `backend/app/services/cbr_client.py`

**Описание:**  
Оба эндпоинта ЦБ — `GET /api/investment/cbr` и `GET /api/investment/compare` — возвращают `key_rate: null` и `ruonia: null`.  
Клиент пробует два URL:
1. `https://www.cbr.ru/api/v1/CbRates/key_rate`
2. Fallback: HTML-страница ЦБ

Оба недоступны или возвращают неожиданный формат из Docker-контейнера.

**Влияние:**  
- Сравнение SBMM vs накопительный счёт работает только частично (победитель не определяется)
- Инвестиционный дайджест не содержит актуальных данных по ставке

**Рекомендация:**  
Добавить хардкод последней известной ставки как fallback-значение (например, `21.0%`) или использовать альтернативный источник данных ЦБ.

---

### 🟡 BUG-03 — СРЕДНИЙ: `FinancialSummaryResponse` не содержит `total_unpaid` и `net_worth`

**Где:** `backend/app/schemas.py`, класс `FinancialSummaryResponse`

**Описание:**  
Метод `CreditCardService.get_financial_summary()` возвращает поля `total_unpaid`, `credit_usage_percent`, `latest_savings_balance`, `debit_monthly_limit`, `net_worth`, но они **отсутствуют в Pydantic-схеме** `FinancialSummaryResponse`. FastAPI отфильтровывает их при сериализации.

**Подтверждение:**
```python
# schemas.py — FinancialSummaryResponse не содержит:
# total_unpaid, credit_usage_percent, latest_savings_balance, net_worth
```

**Влияние:**  
Клиенты не получают данные о чистом капитале (`net_worth`) и общей задолженности (`total_unpaid`) через `/api/finance/summary`, хотя бизнес-логика их вычисляет.

**Исправление:**  
Дополнить схему:
```python
class FinancialSummaryResponse(BaseModel):
    available_limit: float
    credit_limit: float
    credit_usage_percent: float
    total_unpaid: float
    bonus_status: BonusStatusResponse
    open_periods: list[dict]
    spending_stats: SpendingStatsResponse
    latest_savings_balance: float
    debit_monthly_limit: float
    net_worth: dict
```

---

### 🟢 INFO-01: Время обработки SMS через Ollama (~3 минуты)

**Где:** Celery worker + Ollama (qwen2.5:1.5b)

**Описание:**  
Каждая SMS-задача занимает ~190 секунд (3+ минуты) на модели `qwen2.5:1.5b`. При очереди из 6 задач суммарное время обработки ~20 минут.

**Влияние:**  
При высоком потоке SMS финансовые данные будут существенно запаздывать. Для реального использования рекомендуется:
- Рассмотреть более быстрые модели (phi3:mini, gemma:2b)
- Добавить timeout на Ollama-запросы
- Предусмотреть мониторинг длины очереди Celery

---

## 4. Итоговая таблица

| Группа тестов | Тестов | Прошли | Баги | Статус |
|---------------|--------|--------|------|--------|
| Health Checks | 3 | 3 | 0 | ✅ |
| Finance GET | 6 | 6 | 0 | ✅ |
| Budget Management | 3 | 3 | 0 | ✅ |
| Webhook (приём SMS) | 6 | 6 | 0 | ✅ |
| Webhook (валидация) | 5 | 5 | 0 | ✅ |
| Investment (портфель) | 6 | 6 | 0 | ✅ |
| Investment (ЛДВ) | 2 | 2 | 0 | ✅ |
| Investment (ликвидность) | 4 | 4 | 0 | ✅ |
| Investment (ЦБ/RUONIA) | 4 | 2 | 2 | ⚠️ |
| Валидация ввода | 8 | 8 | 0 | ✅ |
| Grace deadline (юнит) | 10 | 10 | 0 | ✅ |
| Celery → БД | 6 | 0 | 6 | 🔴 |
| **Итого** | **63** | **55** | **8** | **⚠️** |

---

## 5. Приоритеты исправлений

| # | Баг | Приоритет | Трудозатраты |
|---|-----|-----------|--------------|
| 1 | BUG-01: Celery не коммитит сессию | 🔴 Критический | ~15 мин |
| 2 | BUG-03: Неполная Pydantic-схема summary | 🟡 Средний | ~5 мин |
| 3 | BUG-02: ЦБ API null | 🟡 Средний | ~30 мин |

---

*Отчёт сгенерирован автоматически. Все тесты выполнены против живого окружения Docker Compose.*
