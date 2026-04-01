# Планировщик задач (Celery Beat)

Документация фоновых задач, которые выполняются по расписанию через Celery Beat.

---

## Архитектура

Система использует:
- **Celery** — фоновый обработчик задач
- **Celery Beat** — планировщик (scheduler) для выполнения по расписанию
- **Redis** — брокер сообщений и хранилище результатов

### Конфигурация

Расписание определено в `app/tasks/celery_worker.py`:

```python
celery_app.conf.beat_schedule = {
    "daily-yield-task": {
        "task": "app.tasks.celery_worker.calculate_and_store_yield",
        "schedule": crontab(minute=55, hour=23),
    },
    "weekly-budget-report": {
        "task": "app.tasks.celery_worker.send_weekly_budget_report",
        "schedule": crontab(minute=0, hour=20, day_of_week=6),
    }
}
celery_app.conf.timezone = "Europe/Moscow"
```

---

## Задача: `calculate_and_store_yield`

### Описание

Ежедневный расчёт и сохранение доходов по накопительному счёту (*1837).

### Расписание

**Время:** каждый день в **23:55 Московского времени**

**Cron:** `55 23 * * *`

### Логика

1. **Инициализация БД** → `await AsyncORM.init()`
2. **Получить последний баланс** → запрос последней транзакции по account_type="savings" с `balance_after != NULL`
3. **Если баланса нет** → return (ничего не делать)
4. **Получить ставку** → вызов `get_current_rate()` для текущего месяца
5. **Вычислить доход** → применить формулу: `баланс × (ставка / 100) / 365`
6. **Сохранить в БД** → upsert в таблицу `daily_yields` (PRIMARY KEY = date)
7. **Отправить VK** — если `vk_bot_token` и `vk_user_id` настроены
8. **Закрыть соединения** → `await AsyncORM.close()`

### Подробный код

```python
@celery_app.task(name="app.tasks.celery_worker.calculate_and_store_yield")
def calculate_and_store_yield():
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                # 1. Получить последний баланс по savings счету
                stmt = (
                    select(Transaction)
                    .where(Transaction.account_type == "savings")
                    .order_by(Transaction.created_at.desc())
                    .limit(1)
                )
                result = await session.execute(stmt)
                tx = result.scalar_one_or_none()
                
                if not tx or tx.balance_after is None:
                    return  # Нечего делать
                
                balance = tx.balance_after
                rate = get_current_rate()
                earned = DailyYield.calculate_daily_yield(balance)  # Вычисление
                
                today = date.today()
                
                # 2. Upsert в daily_yields
                existing = await session.get(DailyYield, today)
                if existing:
                    existing.end_of_day_balance = balance
                    existing.applied_rate = rate
                    existing.earned_amount = earned
                else:
                    session.add(DailyYield(
                        date=today,
                        account_tail="1837",
                        end_of_day_balance=balance,
                        applied_rate=rate,
                        earned_amount=earned,
                    ))
                
                await session.commit()
                
                # 3. Отправить VK если настроено
                if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                    from app.services.vk_client import VkBotClient
                    
                    msg = (
                        f"ФИНАНСОВЫЙ ДАЙДЖЕСТ: НАКОПИТЕЛЬНЫЙ СЧЕТ\n"
                        f"Счет: *1837\n"
                        f"Примененная ставка: {rate}% годовых\n\n"
                        f"Доход за сегодня: +{earned} руб.\n"
                        f"Текущий баланс: {balance} руб."
                    )
                    
                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(msg)
                    except Exception as e:
                        logging.getLogger(__name__).error(f"VK Celery Error: {e}")
                    finally:
                        await vk.close()
        finally:
            await AsyncORM.close()
    
    asyncio.run(_inner())
```

### VK Уведомление

Пример сообщения:

```
ФИНАНСОВЫЙ ДАЙДЖЕСТ: НАКОПИТЕЛЬНЫЙ СЧЕТ
Счет: *1837
Примененная ставка: 11.5% годовых

Доход за сегодня: +78.77 руб.
Текущий баланс: 250000 руб.
```

### Настройка

#### Требуемые переменные окружения

- `REDIS_HOST` — хост Redis (default: `redis`)
- `REDIS_PORT` — порт Redis (default: `6379`)
- `REDIS_DB` — БД в Redis (default: `0`)
- `POSTGRES_*` — параметры БД
- `VK_BOT_TOKEN` — токен VK Bot API (опционально)
- `VK_USER_ID` — ID пользователя в VK (опционально)
- `VK_API_VERSION` — версия VK API (default: `5.199`)

#### Отключение VK уведомлений

Если `VK_BOT_TOKEN == "YOUR_VK_BOT_TOKEN"` или `VK_USER_ID == 0`, VK уведомление не отправляется.

### Обработка ошибок

- Если последней транзакции нет или баланс NULL → ничего не делать (return)
- Если ошибка при отправке VK → логируется на уровне ERROR, но задача не падает
- Все исключения при работе с БД поднимаются наверх (Celery их обработает)

### Примечания

- Задача использует **локальное время хоста** (`date.today()`), а не UTC
- Если таблица `daily_yields` пуста за день, она будет создана; если уже есть — обновлена
- Точность вычисления: 2 знака после запятой (копейки)

---

## Задача: `send_weekly_budget_report`

### Описание

Еженедельный отчёт о состоянии категорийных бюджетов по дебетовой карте.

### Расписание

**Время:** каждое **воскресенье в 20:00 Московского времени**

**Cron:** `0 20 * * 6` (day_of_week: 6 = воскресенье)

### Логика

1. **Инициализация БД** → `await AsyncORM.init()`
2. **Получить все активные бюджеты** → запрос `BudgetLimit` где `is_active == True`
3. **Если бюджетов нет** → return (ничего не делать)
4. **Для каждого бюджета:**
   - Получить траты за текущий месяц по категории
   - Вычислить остаток (лимит - траты)
   - Добавить строку в отчёт
5. **Отправить VK** — если настроено
6. **Закрыть соединения** → `await AsyncORM.close()`

### Подробный код

```python
@celery_app.task(name="app.tasks.celery_worker.send_weekly_budget_report")
def send_weekly_budget_report():
    """Weekly digest of budget limits and spending by category."""
    async def _inner():
        await AsyncORM.init()
        try:
            async with AsyncORM.get_session()() as session:
                # 1. Получить все активные бюджеты
                from sqlalchemy import select
                
                stmt = select(BudgetLimit).where(BudgetLimit.is_active == True)
                result = await session.execute(stmt)
                budgets = result.scalars().all()
                
                if not budgets:
                    return  # Нечего делать
                
                # 2. Построить отчёт
                today = date.today()
                current_month = today.strftime("%Y-%m")
                
                lines = [
                    "BUDGET REPORT (CURRENT MONTH)",
                    ""
                ]
                
                for budget in budgets:
                    spent = await AsyncORM.get_month_category_expenses(
                        session, budget.category, current_month
                    )
                    spent_float = float(spent) if spent else 0.0
                    remaining = float(budget.monthly_limit) - spent_float
                    
                    lines.append(f"Category: {budget.category}")
                    lines.append(f"Limit: {int(budget.monthly_limit)} RUB")
                    lines.append(f"Spent: {int(spent_float)} RUB")
                    lines.append(f"Remaining: {int(remaining)} RUB")
                    lines.append("-----------------------------------")
                
                msg = "\n".join(lines)
                
                # 3. Отправить VK если настроено
                if settings.vk_bot_token and settings.vk_bot_token != "YOUR_VK_BOT_TOKEN" and settings.vk_user_id:
                    from app.services.vk_client import VkBotClient
                    import logging
                    
                    vk = VkBotClient(settings.vk_bot_token, settings.vk_user_id, settings.vk_api_version)
                    try:
                        await vk.send_message(msg)
                    except Exception as e:
                        logging.getLogger(__name__).error(f"VK Budget Report Error: {e}")
                    finally:
                        await vk.close()
        finally:
            await AsyncORM.close()
    
    asyncio.run(_inner())
```

### VK Уведомление

Пример сообщения (для 2 категорий):

```
BUDGET REPORT (CURRENT MONTH)

Category: Продукты
Limit: 20000 RUB
Spent: 8500 RUB
Remaining: 11500 RUB
-----------------------------------
Category: Транспорт
Limit: 10000 RUB
Spent: 3200 RUB
Remaining: 6800 RUB
-----------------------------------
```

Если бюджетов нет → сообщение не отправляется.

### Настройка

#### Требуемые переменные окружения

- `REDIS_HOST` — хост Redis (default: `redis`)
- `REDIS_PORT` — порт Redis (default: `6379`)
- `REDIS_DB` — БД в Redis (default: `0`)
- `POSTGRES_*` — параметры БД
- `VK_BOT_TOKEN` — токен VK Bot API (опционально)
- `VK_USER_ID` — ID пользователя в VK (опционально)
- `VK_API_VERSION` — версия VK API (default: `5.199`)

#### Определение бюджетов

Бюджеты создаются/обновляются через API:

```bash
POST /api/finance/budgets/Продукты?limit=20000
```

Все бюджеты с `is_active = True` будут включены в еженедельный отчёт.

### Обработка ошибок

- Если нет активных бюджетов → ничего не делать (return)
- Если ошибка при отправке VK → логируется на ERROR, но задача не падает
- Если траты не найдены по категории → используется 0

### Примечания

- Только активные бюджеты (`is_active = True`) включаются в отчёт
- Суммы округляются до целых рублей (использует `int()`)
- Отчёт формируется для **текущего календарного месяца** (не скользящего периода)
- Задача работает в московском временном поясе (UTC+3)

---

## Управление задачами

### Просмотр расписания

```bash
# Посмотреть текущее расписание
docker compose exec celery celery -A app.tasks.celery_worker inspect scheduled
```

### Запуск задачи вручную

```bash
# Из контейнера
docker compose exec celery celery -A app.tasks.celery_worker call app.tasks.celery_worker.calculate_and_store_yield

# Или вызвать напрямую (из Python)
from app.tasks.celery_worker import calculate_and_store_yield
calculate_and_store_yield.delay()
```

### Просмотр логов

```bash
# Логи Celery Worker
docker compose logs celery

# Логи Celery Beat (планировщик)
docker compose logs celery_beat  # если контейнер отдельный

# Или вместе с backend
make logs
```

### Отключение задачи

Чтобы отключить задачу, удалите её из `celery_app.conf.beat_schedule`:

```python
celery_app.conf.beat_schedule = {
    # "daily-yield-task": { ... }  # закомментировано
    "weekly-budget-report": { ... }
}
```

Затем пересоберите контейнеры:

```bash
make rebuild
```

---

## Архитектура выполнения

### Инициализация

```
Celery Worker запущен
↓
Читает beat_schedule из конфига
↓
Создаёт планировщик (Celery Beat)
↓
Ждёт момента выполнения каждой задачи
```

### При срабатывании

```
Момент выполнения достигнут (e.g. 23:55)
↓
Celery Beat отправляет сообщение в Redis: "execute task X"
↓
Celery Worker получает сообщение из Redis
↓
Worker запускает функцию задачи в отдельном потоке
↓
asyncio.run(_inner()) — инициализация async цикла
↓
Выполнение async кода (БД, VK, и т.д.)
↓
Возврат результата в Redis
↓
Логирование успеха/ошибки
```

### Обработка ошибок

```
Если исключение в задаче:
↓
Celery логирует на уровне ERROR
↓
Задача помечается как failed в Redis
↓
Celery может автоматически перезапустить (если настроено)
```

---

## Мониторинг

### Проверка здоровья Celery

```bash
# Посмотреть активные воркеры
docker compose exec celery celery -A app.tasks.celery_worker inspect active

# Посмотреть зарегистрированные задачи
docker compose exec celery celery -A app.tasks.celery_worker inspect registered

# Посмотреть статистику воркеров
docker compose exec celery celery -A app.tasks.celery_worker inspect stats
```

### Проверка расписания

```bash
# Посмотреть запланированные задачи
docker compose exec celery celery -A app.tasks.celery_worker inspect scheduled
```

Пример вывода:
```json
{
  "celery@container_name": {
    "scheduled": [
      {
        "request": {
          "name": "app.tasks.celery_worker.calculate_and_store_yield",
          "id": "...",
          "args": [],
          "kwargs": {},
          "options": {},
          "is_eager": false
        },
        "eta": "2026-04-01T23:55:00+03:00"
      }
    ]
  }
}
```

---

## Типичные проблемы

### Задача не выполняется

1. Проверить, запущен ли Celery Beat:
   ```bash
   docker compose logs celery | grep "beat"
   ```

2. Проверить, доступен ли Redis:
   ```bash
   docker compose exec redis redis-cli ping
   ```

3. Проверить расписание:
   ```bash
   docker compose exec celery celery -A app.tasks.celery_worker inspect scheduled
   ```

### VK сообщение не отправляется

1. Проверить, настроен ли токен:
   ```bash
   echo $VK_BOT_TOKEN  # должно быть не "YOUR_VK_BOT_TOKEN"
   echo $VK_USER_ID    # должно быть > 0
   ```

2. Проверить логи:
   ```bash
   docker compose logs celery | grep "VK"
   ```

3. Проверить токен вручную:
   ```bash
   curl "https://api.vk.com/method/messages.send?peer_id=YOUR_ID&message=test&access_token=YOUR_TOKEN&v=5.199"
   ```

### БД недоступна из задачи

1. Проверить переменные окружения:
   ```bash
   docker compose exec celery env | grep POSTGRES
   ```

2. Проверить подключение вручную:
   ```bash
   docker compose exec celery python -c "from app.db import AsyncORM; import asyncio; asyncio.run(AsyncORM.init())"
   ```

---

## Будущие расширения

- [ ] Телеграм-уведомления (параллельно с VK)
- [ ] Кастомное расписание (настройка из API)
- [ ] Ретрай-логика для failed задач
- [ ] WebHook вместо VK (для интеграции с внешними сервисами)
