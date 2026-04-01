# Конфигурация

Полный справочник переменных окружения (.env) и их значений.

---

## Основные сведения

- **Конфигурационный файл:** `.env` (создаётся из `.env.example`)
- **Загрузка:** Через Pydantic `BaseSettings` в `app/config.py`
- **Область видимости:** Глобальные переменные окружения контейнеров Docker Compose
- **Переприменение:** После изменения `.env` требуется `make rebuild`

---

## Подключение к базе данных (PostgreSQL)

Группа переменных для подключения к PostgreSQL.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `POSTGRES_USER` | `sber_user` | string | Пользователь БД |
| `POSTGRES_PASSWORD` | `sber_secret_password` | string | Пароль БД **ИЗМЕНИТЕ на уникальный!** |
| `POSTGRES_DB` | `sber_grace` | string | Имя базы данных |
| `POSTGRES_HOST` | `postgres` | string | Имя хоста (Docker сервис) |
| `POSTGRES_PORT` | `5432` | integer | Порт PostgreSQL |

### Составная переменная (в коде)

Из этих переменных собирается:

```
DATABASE_URL = postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}
```

### Пример

```bash
POSTGRES_USER=my_user
POSTGRES_PASSWORD=SecureP@ssw0rd
POSTGRES_DB=my_finance_db
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Результат:
# DATABASE_URL = postgresql+asyncpg://my_user:SecureP@ssw0rd@postgres:5432/my_finance_db
```

---

## Ollama (LLM для парсинга СМС)

Группа переменных для подключения к локальному Ollama сервису.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `OLLAMA_HOST` | `ollama` | string | Имя хоста (Docker сервис) |
| `OLLAMA_PORT` | `11434` | integer | Порт Ollama API |
| `OLLAMA_MODEL` | `qwen2.5:1.5b` | string | Модель LLM для парсинга |

### Составная переменная (в коде)

```
OLLAMA_BASE_URL = http://{host}:{port}
```

### Модели

- `qwen2.5:0.5b` — облегчённая версия, ~0.5GB на диске, быстрая
- `qwen2.5:1.5b` — средняя версия, ~1.5GB, более точная
- `mistral:latest` — альтернатива, хорошее качество

Скачивание моделей:
```bash
make ollama-pull  # Скачает модель из OLLAMA_MODEL
```

### Пример

```bash
OLLAMA_HOST=ollama
OLLAMA_PORT=11434
OLLAMA_MODEL=qwen2.5:1.5b

# Результат:
# OLLAMA_BASE_URL = http://ollama:11434
```

---

## VK Интеграция (Уведомления в ВКонтакте)

Группа переменных для отправки уведомлений в ВКонтакте.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `VK_BOT_TOKEN` | `YOUR_VK_BOT_TOKEN` | string | Токен VK Bot API |
| `VK_USER_ID` | `0` | integer | ID пользователя в VK |
| `VK_API_VERSION` | `5.199` | string | Версия VK API |

### Отключение VK

Если:
- `VK_BOT_TOKEN == "YOUR_VK_BOT_TOKEN"` (значение по умолчанию), или
- `VK_USER_ID == 0`

То все VK функции отключены (уведомления не отправляются).

### Получение токена и ID

1. **Создать VK Workplace приложение**
   - Перейти в VK Developer
   - Создать приложение типа "Standalone"
   - Получить токен в разделе "Access Token"

2. **Получить User ID**
   - Открыть `https://vk.com/id1` (замените 1 на ваш ID)
   - Число в URL — это `VK_USER_ID`
   - Или найти в профиле VK Workspace

### Пример

```bash
VK_BOT_TOKEN=vk1.a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
VK_USER_ID=123456789
VK_API_VERSION=5.199

# Результат: интеграция активна, уведомления будут отправляться
```

### Тестирование

```bash
# Проверить токен вручную
curl "https://api.vk.com/method/users.get?access_token=YOUR_TOKEN&v=5.199"

# Должен вернуть:
# {"response": [{"id": 123456789, "first_name": "...", "last_name": "..."}]}
```

---

## Redis (Брокер для Celery)

Группа переменных для подключения к Redis.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `REDIS_HOST` | `redis` | string | Имя хоста (Docker сервис) |
| `REDIS_PORT` | `6379` | integer | Порт Redis |
| `REDIS_DB` | `0` | integer | Номер БД в Redis (0-15) |

### Составная переменная (в коде)

```
REDIS_URL = redis://{host}:{port}/{db}
```

### Использование

Redis используется как:
- Брокер сообщений Celery (хранение задач)
- Backend результатов Celery (сохранение результатов)
- (Опционально) слой кэширования

### Пример

```bash
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Результат:
# REDIS_URL = redis://redis:6379/0
```

### Мониторинг

```bash
# Проверить соединение
docker compose exec redis redis-cli ping

# Просмотреть количество задач в очереди
docker compose exec redis redis-cli LLEN celery
```

---

## Backend (FastAPI)

Группа переменных для конфигурации FastAPI приложения.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `BACKEND_PORT` | `8000` | integer | Порт внутри контейнера (для Uvicorn) |
| `NGINX_PORT` | `80` | integer | Порт снаружи на хосте (для Nginx) |
| `APP_DEBUG` | `False` | boolean | Режим отладки (echo SQL в логах) |

### Примечания

- `BACKEND_PORT` — порт **внутри контейнера**, Nginx проксирует сюда
- `NGINX_PORT` — порт **на хосте**, этот порт доступен снаружи
- Если хотите доступ к API снаружи → используйте `NGINX_PORT`

### Пример

```bash
BACKEND_PORT=8000
NGINX_PORT=80
APP_DEBUG=False

# Результат:
# http://localhost:80/docs — доступна Swagger документация
# Внутри контейнера: http://backend:8000/health
```

### Отладка

Для вывода SQL запросов в логи:

```bash
APP_DEBUG=True
make rebuild
make logs-backend  # Будут видны все SQL команды
```

---

## Финансовые параметры

Группа переменных, определяющих финансовую логику.

| Переменная | По умолчанию | Тип | Описание |
|------------|--------------|-----|---------|
| `CREDIT_LIMIT` | `150000.0` | float | Кредитный лимит карты МИР7600 (₽) |
| `DEBIT_MONTHLY_LIMIT` | `50000.0` | float | Информационный параметр дебетового лимита (₽) |
| `SAVINGS_MIN_BALANCE` | `10000.0` | float | Минимальный баланс накопительного счёта (₽) **не используется** |
| `TARGET_SPEND_FOR_BONUS` | `100000.0` | float | Целевая сумма трат для бонуса (₽) |

### `CREDIT_LIMIT`

Полный лимит кредитной карты МИР7600.

**Формула доступного лимита:**
```
доступный = CREDIT_LIMIT - сумма_неоплаченных_трат
```

Если попытаться потратить больше, чем доступно → операция отклоняется.

### `DEBIT_MONTHLY_LIMIT`

Информационный параметр (используется только для вывода в `/api/finance/summary`). Не влияет на обработку операций.

Если нужны **реальные** лимиты по категориям дебетовой карты → используйте API:
```bash
POST /api/finance/budgets/Категория?limit=20000
```

### `SAVINGS_MIN_BALANCE`

**Зарезервирован** для будущей функции. Сейчас не используется в коде.

Планируется применять для:
- Блокировки снятий, если баланс < минимума
- Автоматического разделения доходов (часть в ликвидное, часть в сбережения)

### `TARGET_SPEND_FOR_BONUS`

Целевая сумма трат, при которой Сбербанк добавляет бонус к проценту по накопительному счёту.

**Используется для:**
- Расчёта прогресса: `progress_percent = (total_spent / TARGET_SPEND_FOR_BONUS) * 100`
- Вывода в `/api/finance/bonus`

**Пример:**
```bash
TARGET_SPEND_FOR_BONUS=100000.0

# Если потрачено 45 000 ₽:
# progress_percent = 45%
# remaining = 55 000 ₽
```

### Пример конфигурации

```bash
CREDIT_LIMIT=150000.0
DEBIT_MONTHLY_LIMIT=50000.0
SAVINGS_MIN_BALANCE=10000.0
TARGET_SPEND_FOR_BONUS=100000.0
```

---

## Процентные ставки по месяцам

**Внимание:** эта конфигурация находится в коде, не в `.env`.

Файл: `app/config.py`

```python
SAVINGS_RATES = {
    "2026-04": 11.5,   # апрель: 11.5% годовых
    "2026-05": 10.0,   # май: 10.0% годовых
    "2026-06": 9.5,    # июнь: 9.5% годовых
    # ...
    "default": 7.0,    # остальные месяцы: 7.0% годовых
}
```

**Для изменения:**
1. Отредактируйте `SAVINGS_RATES` в `app/config.py`
2. Запустите `make rebuild`

---

## Полный пример `.env`

```bash
# ========== PostgreSQL ==========
POSTGRES_USER=sber_user
POSTGRES_PASSWORD=MySecurePassword123!
POSTGRES_DB=sber_grace
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# ========== Ollama ==========
OLLAMA_HOST=ollama
OLLAMA_PORT=11434
OLLAMA_MODEL=qwen2.5:1.5b

# ========== VK Интеграция ==========
VK_BOT_TOKEN=vk1.a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
VK_USER_ID=123456789
VK_API_VERSION=5.199

# ========== Redis ==========
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# ========== Backend ==========
BACKEND_PORT=8000
NGINX_PORT=80
APP_DEBUG=False

# ========== Финансовые параметры ==========
CREDIT_LIMIT=150000.0
DEBIT_MONTHLY_LIMIT=50000.0
SAVINGS_MIN_BALANCE=10000.0
TARGET_SPEND_FOR_BONUS=100000.0
```

---

## Развёртывание

### Локальное (для разработки)

```bash
cp .env.example .env
# Используйте значения по умолчанию (Docker сервисы внутри Docker Compose)
make up
make ollama-pull
make health
```

### На удалённом сервере

```bash
# 1. Измените хосты на IP удалённого сервера
POSTGRES_HOST=192.168.1.100
OLLAMA_HOST=192.168.1.100
REDIS_HOST=192.168.1.100

# 2. Измените пароль БД на сильный
POSTGRES_PASSWORD=VeryLongRandomPassword123!@#

# 3. Настройте VK интеграцию (если нужна)
VK_BOT_TOKEN=...
VK_USER_ID=...

# 4. Может потребоваться открыть порты на файрволе
# - 80 (Nginx, API)
# - 5432 (PostgreSQL, если доступ снаружи)
# - 11434 (Ollama, если доступ снаружи)
```

### В продакшене (рекомендации)

- **Пароль БД:** используйте `openssl rand -base64 32` для генерации
- **Аутентификация API:** добавьте API ключ (требует кода)
- **HTTPS:** настройте Nginx с сертификатом (Let's Encrypt)
- **Лимиты:** установите rate limits в Nginx
- **Логирование:** интегрируйте с ELK или Splunk
- **Мониторинг:** Prometheus + Grafana для метрик

---

## Переопределение переменных

### Через командную строку

```bash
# При запуске
POSTGRES_PASSWORD=NewPassword docker compose up

# Или через --env-file
docker compose --env-file .env.prod up
```

### Через Docker Compose override

Файл `docker-compose.override.yml`:

```yaml
version: '3.8'
services:
  postgres:
    environment:
      POSTGRES_PASSWORD: LocalDevPassword123
```

---

## Проверка конфигурации

```bash
# Просмотреть все переменные в контейнере
docker compose exec backend env | sort

# Просмотреть только нужные переменные
docker compose exec backend bash -c 'echo $POSTGRES_HOST && echo $OLLAMA_HOST'

# Проверить подключения
docker compose exec backend python -c "from app.config import get_settings; s = get_settings(); print(f'DB URL: {s.database_url[:50]}...')"
```

---

## Проблемы и решения

### Ошибка: "PostgreSQL connection refused"

Проверьте:
1. `POSTGRES_HOST` правильный (в Docker: `postgres`)
2. `POSTGRES_PORT` правильный (по умолчанию `5432`)
3. Контейнер PostgreSQL запущен: `docker compose ps postgres`

### Ошибка: "Ollama connection refused"

Проверьте:
1. `OLLAMA_HOST` правильный (в Docker: `ollama`)
2. `OLLAMA_PORT` правильный (по умолчанию `11434`)
3. Модель загружена: `docker compose exec ollama ollama list`

### Ошибка: "Invalid VK token"

Проверьте:
1. `VK_BOT_TOKEN` скопирован без пробелов
2. Токен ещё валиден (не истёк срок в VK Developer)
3. Приложение имеет права на отправку сообщений

### Ошибка: "Redis connection lost"

Проверьте:
1. `REDIS_HOST` правильный (в Docker: `redis`)
2. `REDIS_PORT` правильный (по умолчанию `6379`)
3. Контейнер Redis запущен: `docker compose ps redis`

---

## Безопасность

### Пароли

- **Никогда** не коммитьте `.env` в Git
- Используйте `.gitignore`:
  ```
  .env
  .env.*.local
  ```
- На сервере используйте **очень сильные пароли**

### Токены

- Храните `VK_BOT_TOKEN` в безопасном хранилище (vault, secrets manager)
- Ротируйте токены регулярно
- Если токен скомпрометирован → переcrear новый в VK Developer

### Сетевая безопасность

- PostgreSQL должен быть доступен **только** из приложения
- Redis должен быть доступен **только** из контейнеров
- Nginx может быть открыт на 80/443 для пользователей

