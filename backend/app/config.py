"""Конфигурация backend'а jWorkPlace.

Все значения читаются из окружения (или backend/.env) через pydantic-settings.
Секреты (DEEPSEEK_API_KEY, GitHub PAT) — только так; никогда не хардкодим,
не логируем, не пробрасываем в контекст LLM.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Ищем .env и по cwd=backend/ (запуск uvicorn/pytest из backend/), и по cwd=repo-root
    # (запуск из корня монорепо) — чтобы поведение не зависело от рабочей директории.
    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")

    port: int = 8200
    jwp_data_dir: str = "/var/lib/jworkplace"
    llm_provider: str = "deepseek"
    deepseek_api_key: str = ""
    cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS как список хостов (разделитель — запятая), пустых записей нет."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
