FROM python:3.11-slim

WORKDIR /app

# Установка curl для healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Порт берётся из env, по умолчанию 8000
ENV BACKEND_PORT=8000
EXPOSE ${BACKEND_PORT}

# Используем entrypoint-скрипт для подстановки переменных
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${BACKEND_PORT}"]