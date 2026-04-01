# API Эндпоинты

Полная документация всех REST API эндпоинтов системы Sber Grace Assistant.

Интерактивная документация Swagger доступна по адресу `http://localhost/docs` после запуска системы.

---

## Быстрый обзор

| HTTP | Путь | Описание |
|------|------|---------|
| **GET** | `/health` | Проверка отзывчивости бэкенда |
| **GET** | `/health/db` | Проверка подключения к PostgreSQL |
| **GET** | `/health/ollama` | Проверка доступности Ollama LLM |
| **POST** | `/api/sber-webhook` | Принять СМС и запустить полный цикл обработки |
| **GET** | `/api/finance/summary` | Полная финансовая сводка |
| **GET** | `/api/finance/limit` | Доступный кредитный лимит |
| **GET** | `/api/finance/bonus` | Прогресс целевых трат |
| **GET** | `/api/finance/periods` | Открытые отчётные периоды |
| **GET** | `/api/finance/budgets` | Список категорийных лимитов |
| **POST** | `/api/finance/budgets/{category}` | Создать/обновить бюджет |
| **GET** | `/api/finance/spending/by-category` | Расходы по категориям |

---

## Health Check Эндпоинты

### `GET /health`

Простая проверка отзывчивости бэкенда.

**Ответ:**
```json
{
  "status": "ok",
  "detail": null
}
```

**Коды ответов:**
- `200 OK` — бэкенд работает

---

### `GET /health/db`

Проверка подключения к PostgreSQL базе данных.

**Ответ:**
```json
{
  "status": "ok",
  "detail": "PostgreSQL connected"
}
```

или при ошибке:

```json
{
  "status": "error",
  "detail": "PostgreSQL unreachable"
}
```

**Коды ответов:**
- `200 OK` — БД подключена
- `500 Internal Server Error` — ошибка подключения

---

### `GET /health/ollama`

Проверка доступности локального Ollama LLM сервера.

**Ответ:**
```json
{
  "status": "ok",
  "detail": "Ollama connected"
}
```

или при ошибке:

```json
{
  "status": "error",
  "detail": "Ollama unreachable"
}
```

**Коды ответов:**
- `200 OK` — Ollama доступен
- `500 Internal Server Error` — ошибка подключения

---

## Webhook — Парсинг СМС

### `POST /api/sber-webhook`

Принять сырой текст СМС от банка, запустить полный цикл обработки: парсинг, привязка к отчётному периоду, проверка лимитов, отправка VK уведомления.

**Запрос:**

```json
{
  "sms_text": "Покупка 5000р ПЯТЕРОЧКА. Баланс: 50000р"
}
```

**Параметры:**
- `sms_text` (string, required, 1-2000 символов) — текст СМС

**Ответ (200 OK):**

```json
{
  "status": "received",
  "transaction_id": 123,
  "parsed_data": {
    "card_tail": "7600",
    "account_type": "credit",
    "amount": 5000.00,
    "merchant": "ПЯТЕРОЧКА",
    "category": "Продукты",
    "is_expense": true,
    "balance_after": 50000.00,
    "type": "purchase"
  },
  "grace_deadline": "2026-08-31",
  "billing_month": "2026-05"
}
```

**Ответ при ошибке парсинга (200 OK):**

```json
{
  "status": "parse_error",
  "transaction_id": 124,
  "parsed_data": null,
  "grace_deadline": null,
  "billing_month": null
}
```

**Поля ответа:**
- `status` — `"received"` если парсинг успешен, `"parse_error"` если нет
- `transaction_id` — ID созданной записи в БД
- `parsed_data` — результаты парсинга (null при ошибке)
  - `card_tail` — последние 4 цифры карты (7600, 6517, 1837)
  - `account_type` — тип счёта (credit, debit, savings)
  - `amount` — сумма операции в руб.
  - `merchant` — название мерчанта
  - `category` — категория расходов
  - `is_expense` — это расход (true) или доход (false)
  - `balance_after` — баланс после операции
  - `type` — тип операции (purchase, payment, transfer, deposit, withdrawal, fee)
- `grace_deadline` — дедлайн льготного периода (ISO формат)
- `billing_month` — отчётный месяц (YYYY-MM)

**Что происходит:**
1. СМС сохраняется в БД
2. Отправляется в Ollama для парсинга
3. Результаты парсинга сохраняются в транзакцию
4. Запускается финансовая логика (привязка к периоду, проверка лимитов)
5. Если настроена VK интеграция, отправляется уведомление

---

## Финансовые эндпоинты

### `GET /api/finance/summary`

Получить полную финансовую сводку: лимиты, долги, бонусы, периоды, статистика расходов.

**Параметры:** нет

**Ответ (200 OK):**

```json
{
  "available_limit": 95000.00,
  "credit_limit": 150000.00,
  "credit_usage_percent": 36.67,
  "total_unpaid": 55000.00,
  "bonus_status": {
    "month": "2026-04",
    "total_spent": 45000.00,
    "target": 100000.00,
    "remaining": 55000.00,
    "is_target_reached": false,
    "progress_percent": 45.0
  },
  "open_periods": [
    {
      "id": 1,
      "month": "2026-04",
      "total_spent": 55000.00,
      "grace_deadline": "2026-07-31",
      "days_left": 91,
      "is_closed": false
    }
  ],
  "spending_stats": {
    "daily": 5000.00,
    "weekly": 15000.00,
    "monthly": 45000.00
  },
  "latest_savings_balance": 250000.00,
  "debit_monthly_limit": 50000.00
}
```

**Описание полей:**
- `available_limit` (float) — доступная часть кредитного лимита
- `credit_limit` (float) — полный кредитный лимит
- `credit_usage_percent` (float) — процент использования лимита
- `total_unpaid` (float) — сумма всех неоплаченных трат
- `bonus_status` (object) — статус прогресса целевых трат
  - `month` — текущий месяц
  - `total_spent` — потрачено в месяце
  - `target` — целевая сумма
  - `remaining` — осталось до цели
  - `is_target_reached` — достигнута ли цель
  - `progress_percent` — процент прогресса
- `open_periods` (array) — список открытых отчётных периодов
  - `id` — ID периода
  - `month` — месяц (YYYY-MM)
  - `total_spent` — траты за период
  - `grace_deadline` — дедлайн льготы
  - `days_left` — дней до дедлайна
  - `is_closed` — закрыт ли период
- `spending_stats` (object) — статистика расходов
  - `daily` — потрачено сегодня
  - `weekly` — потрачено на этой неделе
  - `monthly` — потрачено в этом месяце
- `latest_savings_balance` (float) — последний известный баланс накопительного счёта
- `debit_monthly_limit` (float) — информационный параметр дебетового лимита

---

### `GET /api/finance/limit`

Получить информацию о кредитном лимите и использованной части.

**Параметры:** нет

**Ответ (200 OK):**

```json
{
  "available_limit": 95000.00,
  "credit_limit": 150000.00,
  "total_unpaid": 55000.00,
  "usage_percent": 36.7
}
```

**Описание полей:**
- `available_limit` — доступный остаток лимита
- `credit_limit` — полный лимит
- `total_unpaid` — неоплаченные траты
- `usage_percent` — процент использования (одна цифра после запятой)

---

### `GET /api/finance/bonus`

Получить прогресс достижения целевой суммы трат для бонуса.

**Параметры:** нет

**Ответ (200 OK):**

```json
{
  "month": "2026-04",
  "total_spent": 45000.00,
  "target": 100000.00,
  "remaining": 55000.00,
  "is_target_reached": false,
  "progress_percent": 45.0
}
```

**Описание полей:**
- `month` — текущий месяц (YYYY-MM)
- `total_spent` — уже потрачено в месяце
- `target` — целевая сумма (по умолчанию 100 000 ₽)
- `remaining` — осталось потратить
- `is_target_reached` — достигнута ли цель
- `progress_percent` — процент выполнения (0–100)

---

### `GET /api/finance/periods`

Получить список всех открытых отчётных периодов с деталями по льготным дедлайнам.

**Параметры:** нет

**Ответ (200 OK):**

```json
[
  {
    "id": 1,
    "month": "2026-04",
    "total_spent": 55000.00,
    "grace_deadline": "2026-07-31",
    "days_left": 91,
    "is_closed": false
  },
  {
    "id": 2,
    "month": "2026-03",
    "total_spent": 30000.00,
    "grace_deadline": "2026-06-30",
    "days_left": 60,
    "is_closed": false
  }
]
```

**Описание полей каждого периода:**
- `id` — ID периода в БД
- `month` — месяц (YYYY-MM)
- `total_spent` — сумма трат в период
- `grace_deadline` — дата окончания льготного периода (ISO)
- `days_left` — количество дней до дедлайна
- `is_closed` — закрыт ли период (все долги погашены)

---

### `GET /api/finance/budgets`

Получить список всех активных категорийных лимитов по дебетовой карте.

**Параметры:** нет

**Ответ (200 OK):**

```json
[
  {
    "id": 1,
    "category": "Продукты",
    "monthly_limit": 20000.00
  },
  {
    "id": 2,
    "category": "Транспорт",
    "monthly_limit": 10000.00
  }
]
```

**Пустой список:**
```json
[]
```

**Описание полей:**
- `id` — ID бюджета в БД
- `category` — название категории
- `monthly_limit` — месячный лимит в рублях

---

### `POST /api/finance/budgets/{category}`

Создать новый бюджет по категории или обновить существующий.

**Параметры:**
- `category` (path, required, string) — название категории (например, `Продукты`)
- `limit` (query, required, float) — месячный лимит в рублях

**Примеры вызова:**
```
POST /api/finance/budgets/Продукты?limit=20000
POST /api/finance/budgets/Транспорт?limit=10000
```

**Ответ (200 OK):**

```json
{
  "success": true,
  "category": "Продукты",
  "monthly_limit": 20000.00
}
```

**Описание полей:**
- `success` — успешно ли была операция
- `category` — категория
- `monthly_limit` — установленный лимит

---

### `GET /api/finance/spending/by-category`

Получить расходы по дебетовой карте в текущем месяце, сгруппированные по категориям.

**Параметры:** нет

**Ответ (200 OK):**

```json
[
  {
    "category": "Продукты",
    "spent": 8500.00
  },
  {
    "category": "Транспорт",
    "spent": 3200.00
  },
  {
    "category": "Другое",
    "spent": 1500.00
  }
]
```

**Пустой список (если нет расходов):**
```json
[]
```

**Описание полей:**
- `category` — название категории (или "Неизвестно" если не заполнена)
- `spent` — сумма расходов в рублях

---

## Примеры использования

### cURL

```bash
# Проверка здоровья системы
curl http://localhost/health

# Отправка СМС
curl -X POST http://localhost/api/sber-webhook \
  -H "Content-Type: application/json" \
  -d '{"sms_text":"Покупка 5000р ПЯТЕРОЧКА. Баланс: 50000р"}'

# Получить финансовую сводку
curl http://localhost/api/finance/summary | jq

# Установить бюджет на категорию
curl -X POST "http://localhost/api/finance/budgets/Продукты?limit=20000"

# Получить расходы по категориям
curl http://localhost/api/finance/spending/by-category | jq
```

### Python

```python
import httpx
import json

# Создать клиент
client = httpx.Client(base_url="http://localhost")

# Отправить СМС
response = client.post("/api/sber-webhook", json={
    "sms_text": "Покупка 5000р ПЯТЕРОЧКА. Баланс: 50000р"
})
print(json.dumps(response.json(), indent=2))

# Получить сводку
summary = client.get("/api/finance/summary").json()
print(f"Доступный лимит: {summary['available_limit']} ₽")

# Установить бюджет
response = client.post("/api/finance/budgets/Продукты", params={"limit": 20000})
print(response.json())
```

### JavaScript/Fetch

```javascript
// Отправить СМС
const response = await fetch("http://localhost/api/sber-webhook", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ sms_text: "Покупка 5000р ПЯТЕРОЧКА" })
});
const data = await response.json();
console.log(data);

// Получить финансовую сводку
const summary = await fetch("http://localhost/api/finance/summary")
  .then(r => r.json());
console.log(`Доступный лимит: ${summary.available_limit} ₽`);
```

---

## Коды ошибок

| Код | Описание |
|-----|---------|
| `200` | OK — запрос успешен |
| `400` | Bad Request — неверные параметры |
| `404` | Not Found — ресурс не найден |
| `500` | Internal Server Error — ошибка сервера |

## Примечания

- Все финансовые суммы в рублях (₽)
- Все даты в ISO формате (YYYY-MM-DD или YYYY-MM)
- Нет аутентификации — все эндпоинты публичны
- Рекомендуется использовать Swagger UI (`http://localhost/docs`) для интерактивного тестирования
