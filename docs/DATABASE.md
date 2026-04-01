# База данных

Полное описание схемы базы данных PostgreSQL и всех таблиц.

---

## Общая информация

- **СУБД:** PostgreSQL 16
- **Драйвер:** asyncpg (асинхронный)
- **Миграции:** auto-create on init (SQLAlchemy создаёт таблицы при старте)
- **ORM:** SQLAlchemy 2.0+ с async поддержкой

---

## Таблица: `transactions`

Хранит все операции (СМС от банка), как сырые, так и распарсенные.

### Поля

| Поле | Тип | NULL | Default | Описание |
|------|-----|------|---------|---------|
| `id` | INTEGER | NO | autoincrement | Первичный ключ |
| `billing_period_id` | INTEGER FK | YES | NULL | Ссылка на отчётный период |
| `sms_text` | TEXT | NO | — | Оригинальный текст СМС |
| `card_tail` | VARCHAR(4) | YES | NULL | Последние 4 цифры карты (7600, 6517, 1837) |
| `account_type` | ENUM | YES | NULL | Тип счёта: credit / debit / savings |
| `amount` | NUMERIC(12,2) | YES | NULL | Сумма в рублях |
| `transaction_type` | ENUM | NO | 'unknown' | Тип: purchase, payment, transfer, deposit, withdrawal, fee, unknown |
| `merchant` | VARCHAR(255) | YES | NULL | Название мерчанта (магазина, сервиса) |
| `category` | VARCHAR(100) | YES | NULL | Категория (Продукты, Транспорт и т.д.) |
| `is_grace_safe` | BOOLEAN | YES | NULL | Нарушает ли операция грейс-период |
| `is_expense` | BOOLEAN | YES | NULL | Расход (TRUE) или доход (FALSE) |
| `balance_after` | NUMERIC(12,2) | YES | NULL | Баланс счёта после операции |
| `card` | VARCHAR(50) | YES | NULL | Маска карты (ECMC6517, МИР7600, *1837) |
| `grace_deadline` | DATE | YES | NULL | Дедлайн льготного периода (кэш) |
| `raw_llm_response` | TEXT | YES | NULL | Сырой JSON ответ от Ollama |
| `is_parsed` | BOOLEAN | NO | FALSE | Успешно ли парсилась СМС |
| `created_at` | TIMESTAMP | NO | now() | Время создания записи |

### Индексы

- `billing_period_id` — для быстрого поиска транзакций по периоду
- `account_type` — для фильтрации по типу счёта
- `created_at` — для сортировки и диапазонных запросов
- `is_parsed` — для фильтрации парсенных/не парсенных

### Связи

- `billing_period` → ForeignKey к `billing_periods.id` (nullable)

### Примеры

**Покупка в магазине:**
```json
{
  "id": 1,
  "billing_period_id": 5,
  "sms_text": "Покупка 5000р ПЯТЕРОЧКА. Баланс: 50000р",
  "card_tail": "7600",
  "account_type": "credit",
  "amount": 5000.00,
  "transaction_type": "purchase",
  "merchant": "ПЯТЕРОЧКА",
  "category": "Продукты",
  "is_grace_safe": true,
  "is_expense": true,
  "balance_after": 50000.00,
  "card": "МИР7600",
  "grace_deadline": "2026-08-31",
  "raw_llm_response": "{...}",
  "is_parsed": true,
  "created_at": "2026-04-15T10:30:00+03:00"
}
```

**Платёж:**
```json
{
  "id": 2,
  "billing_period_id": 5,
  "sms_text": "Платёж 30000р принят. Баланс: 80000р",
  "card_tail": "7600",
  "account_type": "credit",
  "amount": 30000.00,
  "transaction_type": "payment",
  "merchant": "Сберобслуживание",
  "category": "Платёж",
  "is_grace_safe": true,
  "is_expense": false,
  "balance_after": 80000.00,
  "card": "МИР7600",
  "grace_deadline": null,
  "raw_llm_response": "{...}",
  "is_parsed": true,
  "created_at": "2026-04-16T15:45:00+03:00"
}
```

---

## Таблица: `billing_periods`

Отчётные периоды — по одному на каждый календарный месяц с тратами.

### Поля

| Поле | Тип | NULL | Default | Описание |
|------|-----|------|---------|---------|
| `id` | INTEGER | NO | autoincrement | Первичный ключ |
| `month` | DATE | NO | — | Первый день месяца (UNIQUE ключ) |
| `total_spent` | NUMERIC(12,2) | NO | 0.00 | Сумма трат в период |
| `grace_deadline` | DATE | NO | — | Дедлайн льготного периода |
| `is_closed` | BOOLEAN | NO | FALSE | Закрыт ли период (долг погашен) |
| `created_at` | TIMESTAMP | NO | now() | Время создания |

### Индексы

- `month` — UNIQUE, для быстрого поиска периода по месяцу
- `is_closed` — для поиска открытых периодов

### Связи

- `transactions` → обратная ссылка из `transactions.billing_period_id` (selectin lazy load)
- `credit_payments` → обратная ссылка из `credit_payments.billing_period_id`

### Примеры

**Апрель 2026 (открытый период):**
```json
{
  "id": 5,
  "month": "2026-04-01",
  "total_spent": 55000.00,
  "grace_deadline": "2026-07-31",
  "is_closed": false,
  "created_at": "2026-04-01T00:00:00+03:00"
}
```

**Март 2026 (закрытый период):**
```json
{
  "id": 4,
  "month": "2026-03-01",
  "total_spent": 30000.00,
  "grace_deadline": "2026-06-30",
  "is_closed": true,
  "created_at": "2026-03-01T00:00:00+03:00"
}
```

---

## Таблица: `daily_yields`

Дневные доходы по накопительному счёту (*1837).

### Поля

| Поле | Тип | NULL | Default | Описание |
|------|-----|------|---------|---------|
| `date` | DATE | NO | — | День расчёта (PRIMARY KEY) |
| `account_tail` | VARCHAR(4) | NO | "1837" | Номер счёта |
| `end_of_day_balance` | NUMERIC(12,2) | NO | — | Баланс на конец дня |
| `applied_rate` | FLOAT | NO | — | Применённая процентная ставка (%) |
| `earned_amount` | NUMERIC(12,2) | NO | — | Дневной доход |
| `created_at` | TIMESTAMP | NO | now() | Время записи |

### Индексы

- `date` — PRIMARY KEY, для быстрого поиска по дате

### Примеры

**День 2026-04-15:**
```json
{
  "date": "2026-04-15",
  "account_tail": "1837",
  "end_of_day_balance": 250000.00,
  "applied_rate": 11.5,
  "earned_amount": 78.77,
  "created_at": "2026-04-15T23:55:00+03:00"
}
```

**День 2026-04-16:**
```json
{
  "date": "2026-04-16",
  "account_tail": "1837",
  "end_of_day_balance": 250095.30,
  "applied_rate": 11.5,
  "earned_amount": 78.83,
  "created_at": "2026-04-16T23:55:00+03:00"
}
```

---

## Таблица: `budget_limits`

Месячные лимиты по категориям расходов для дебетовой карты.

### Поля

| Поле | Тип | NULL | Default | Описание |
|------|-----|------|---------|---------|
| `id` | INTEGER | NO | autoincrement | Первичный ключ |
| `category` | VARCHAR(100) | NO | — | Название категории (UNIQUE) |
| `monthly_limit` | NUMERIC(12,2) | NO | — | Месячный лимит в рублях |
| `is_active` | BOOLEAN | NO | TRUE | Активен ли лимит (для мягкого удаления) |
| `created_at` | TIMESTAMP | NO | now() | Дата создания |
| `updated_at` | TIMESTAMP | NO | now() | Дата последнего обновления |

### Индексы

- `category` — UNIQUE, для быстрого поиска лимита по категории
- `is_active` — для фильтрации активных лимитов

### Примеры

**Продукты - 20 000 ₽/месяц:**
```json
{
  "id": 1,
  "category": "Продукты",
  "monthly_limit": 20000.00,
  "is_active": true,
  "created_at": "2026-03-01T10:00:00+03:00",
  "updated_at": "2026-03-01T10:00:00+03:00"
}
```

**Транспорт - 10 000 ₽/месяц:**
```json
{
  "id": 2,
  "category": "Транспорт",
  "monthly_limit": 10000.00,
  "is_active": true,
  "created_at": "2026-03-05T14:30:00+03:00",
  "updated_at": "2026-03-05T14:30:00+03:00"
}
```

**Удалённый лимит (мягкое удаление):**
```json
{
  "id": 3,
  "category": "Рестораны",
  "monthly_limit": 15000.00,
  "is_active": false,
  "created_at": "2026-02-01T10:00:00+03:00",
  "updated_at": "2026-04-01T12:00:00+03:00"
}
```

---

## Таблица: `credit_payments`

История платежей по кредитной карте, сгруппированная по отчётным периодам.

### Поля

| Поле | Тип | NULL | Default | Описание |
|------|-----|------|---------|---------|
| `id` | INTEGER | NO | autoincrement | Первичный ключ |
| `billing_period_id` | INTEGER FK | NO | — | Ссылка на отчётный период |
| `transaction_id` | INTEGER FK | YES | NULL | Ссылка на транзакцию платежа |
| `amount` | NUMERIC(12,2) | NO | — | Сумма платежа в рублях |
| `payment_date` | DATE | NO | — | День платежа |
| `created_at` | TIMESTAMP | NO | now() | Время записи |

### Индексы

- `billing_period_id` — для быстрого поиска платежей по периоду
- `transaction_id` — для связи с оригинальной транзакцией

### Связи

- `billing_period` → ForeignKey к `billing_periods.id` (обязательный)
- `transaction` → ForeignKey к `transactions.id` (nullable)

### Примеры

**Платёж за апрель 2026:**
```json
{
  "id": 10,
  "billing_period_id": 5,
  "transaction_id": 2,
  "amount": 30000.00,
  "payment_date": "2026-04-16",
  "created_at": "2026-04-16T15:45:00+03:00"
}
```

**Второй платёж за апрель (доплата):**
```json
{
  "id": 11,
  "billing_period_id": 5,
  "transaction_id": 15,
  "amount": 25000.00,
  "payment_date": "2026-04-20",
  "created_at": "2026-04-20T18:30:00+03:00"
}
```

---

## Перечисления (Enums)

### `TransactionType`

```python
class TransactionType(str, Enum):
    PURCHASE = "purchase"      # Покупка
    PAYMENT = "payment"        # Платёж
    TRANSFER = "transfer"      # Перевод
    DEPOSIT = "deposit"        # Зачисление
    WITHDRAWAL = "withdrawal"  # Снятие
    FEE = "fee"               # Комиссия
    UNKNOWN = "unknown"       # Неизвестный тип
```

### `AccountType`

```python
class AccountType(str, Enum):
    CREDIT = "credit"    # Кредитная карта МИР7600
    DEBIT = "debit"      # Дебетовая карта ECMC6517
    SAVINGS = "savings"  # Накопительный счёт *1837
```

---

## Примеры SQL запросов

### Получить всех открытые отчётные периоды

```sql
SELECT * FROM billing_periods
WHERE is_closed = false
ORDER BY grace_deadline ASC;
```

### Суммировать траты в открытых периодах

```sql
SELECT 
    SUM(amount) as total_unpaid
FROM transactions
WHERE billing_period_id IN (
    SELECT id FROM billing_periods WHERE is_closed = false
)
AND is_expense = true;
```

### Получить дневной доход за месяц

```sql
SELECT 
    DATE_TRUNC('month', date) as month,
    SUM(earned_amount) as total_earned
FROM daily_yields
WHERE DATE_TRUNC('month', date) = '2026-04-01'
GROUP BY DATE_TRUNC('month', date);
```

### Получить траты по категории за месяц

```sql
SELECT 
    category,
    SUM(amount) as spent
FROM transactions
WHERE account_type = 'debit'
    AND is_expense = true
    AND is_parsed = true
    AND DATE_TRUNC('month', created_at) = '2026-04-01'
GROUP BY category
ORDER BY spent DESC;
```

### Проверить переплату по периоду

```sql
SELECT 
    bp.id,
    bp.month,
    bp.total_spent,
    COALESCE(SUM(cp.amount), 0) as total_paid,
    (COALESCE(SUM(cp.amount), 0) - bp.total_spent) as overpaid
FROM billing_periods bp
LEFT JOIN credit_payments cp ON bp.id = cp.billing_period_id
WHERE bp.id = 5
GROUP BY bp.id, bp.month, bp.total_spent;
```

---

## Типичные операции (через ORM)

### Создать отчётный период

```python
from app.db import AsyncORM
from datetime import date

month = date(2026, 4, 1)
grace_deadline = date(2026, 7, 31)

period = await AsyncORM.get_or_create_billing_period(
    session, month, grace_deadline
)
```

### Добавить транзакцию

```python
transaction = await AsyncORM.create_transaction(
    session, 
    sms_text="Покупка 5000р ПЯТЕРОЧКА"
)

await AsyncORM.update_transaction_parsed(
    session,
    transaction,
    card_tail="7600",
    account_type="credit",
    amount=5000.0,
    transaction_type=TransactionType.PURCHASE,
    merchant="ПЯТЕРОЧКА",
    category="Продукты",
    is_grace_safe=True,
    is_expense=True,
    balance_after=50000.0,
    card="МИР7600",
    raw_llm_response="{...}",
    is_parsed=True,
)

await session.flush()
```

### Получить сумму неоплаченных трат

```python
total_unpaid = await AsyncORM.get_total_unpaid_expenses(session)
print(f"Всего неоплачено: {total_unpaid} ₽")
```

### Закрыть отчётный период

```python
await AsyncORM.close_billing_period(session, period_id=5)
await session.commit()
```

### Сохранить дневной доход

```python
from app.db.models import DailyYield
from datetime import date

today = date.today()
existing = await session.get(DailyYield, today)

if existing:
    existing.end_of_day_balance = 250000.0
    existing.applied_rate = 11.5
    existing.earned_amount = 78.77
else:
    session.add(DailyYield(
        date=today,
        account_tail="1837",
        end_of_day_balance=250000.0,
        applied_rate=11.5,
        earned_amount=78.77,
    ))

await session.commit()
```

---

## Backup и восстановление

### Создать резервную копию

```bash
docker compose exec postgres pg_dump -U sber_user sber_grace > backup.sql
```

### Восстановить из резервной копии

```bash
docker compose exec -T postgres psql -U sber_user sber_grace < backup.sql
```

### Экспорт данных (CSV)

```bash
docker compose exec postgres psql -U sber_user sber_grace -c "
  COPY transactions TO STDOUT WITH CSV HEADER
" > transactions.csv
```

---

## Миграции (будущее)

Текущая система использует SQLAlchemy auto-create при инициализации. В будущем рекомендуется перейти на Alembic для управления миграциями:

```bash
# Инициализировать Alembic
alembic init alembic

# Создать миграцию при изменении моделей
alembic revision --autogenerate -m "add new column"

# Применить миграции
alembic upgrade head
```
