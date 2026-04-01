.PHONY: up down rebuild logs logs-backend ps ollama-pull health psql test-webhook

# Запуск всех контейнеров
up:
	docker compose up -d

# Остановка всех контейнеров
down:
	docker compose down

# Пересборка и запуск
rebuild:
	docker compose up -d --build

# Логи всех контейнеров
logs:
	docker compose logs -f

# Логи только backend
logs-backend:
	docker compose logs -f backend

# Статус контейнеров
ps:
	docker compose ps

# Скачать модель Ollama
ollama-pull:
	docker compose exec ollama ollama pull $${OLLAMA_MODEL:-qwen2.5:1.5b}

# Проверка здоровья
health:
	@echo "=== Backend ===" && curl -s http://localhost:$${NGINX_PORT:-80}/health | python3 -m json.tool
	@echo "\n=== Database ===" && curl -s http://localhost:$${NGINX_PORT:-80}/health/db | python3 -m json.tool
	@echo "\n=== Ollama ===" && curl -s http://localhost:$${NGINX_PORT:-80}/health/ollama | python3 -m json.tool

# Подключиться к БД
psql:
	docker compose exec postgres psql -U $${POSTGRES_USER:-sber_user} -d $${POSTGRES_DB:-sber_grace}

# Тестовый вебхук
test-webhook:
	curl -X POST http://localhost:$${NGINX_PORT:-80}/api/sber-webhook \
		-H "Content-Type: application/json" \
		-d '{"sms_text": "Покупка 1500р ПЯТЕРОЧКА"}'
