from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Конфигурация приложения из переменных окружения."""

    # PostgreSQL
    postgres_user: str = "sber_user"
    postgres_password: str = "sber_secret_password"
    postgres_db: str = "sber_grace"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # Составной URL (строится из компонентов)
    database_url: str = ""

    # Ollama
    ollama_host: str = "ollama"
    ollama_port: int = 11434
    ollama_model: str = "qwen2.5:1.5b"

    # Составной URL
    ollama_base_url: str = ""

    # Redis
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # Составной URL
    redis_url: str = ""

    # Backend
    backend_port: int = 8000

    # App
    app_debug: bool = False

    # ─── Финансовые константы (Кредитная карусель) ───────────────
    credit_limit: float = 150_000.0
    target_spend_for_bonus: float = 50_000.0

    def model_post_init(self, __context) -> None:
        """Собираем составные URL из компонентов, если не заданы явно."""
        if not self.database_url:
            self.database_url = (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        if not self.ollama_base_url:
            self.ollama_base_url = f"http://{self.ollama_host}:{self.ollama_port}"
        if not self.redis_url:
            self.redis_url = f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    """Кешированный синглтон настроек."""
    return Settings()
