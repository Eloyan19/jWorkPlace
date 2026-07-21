"""Конфигурация backend'а jWorkPlace.

Все значения читаются из окружения (или backend/.env) через pydantic-settings.
Секреты (DEEPSEEK_API_KEY, GitHub PAT, GATE_TOKEN) — только так; никогда не хардкодим,
не логируем, не пробрасываем в контекст LLM.
"""
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet
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

    # Токен-барьер публичного URL (проверяется в nginx; backend его не валидирует, но
    # хранит здесь, чтобы redeploy мог пробросить его во фронт как VITE_API_TOKEN).
    gate_token: str = ""

    # --- Per-project GitHub PAT at rest (Этап 3b) ---
    # Ключ шифрования PAT (Fernet, 32 байта urlsafe-base64 — вывод Fernet.generate_key()).
    # Пустой/невалидный ключ → fail-closed: token/pr-эндпоинты отказывают, а не падают молча.
    jwp_secret_key: str = ""

    # --- Индексация (Этап 1) ---
    //ollama_url: str = "http://127.0.0.1:11434"
    embed_model: str = "nomic-embed-text"
    # Fail-closed: без gitleaks скан секретов по содержимому невозможен → индексацию прерываем
    # (не индексируем чужой репо с одним лишь фильтром имён). Ставится в False только осознанно.
    require_gitleaks: bool = True
    clone_timeout_s: int = 120
    index_timeout_s: int = 300
    max_repo_mb: int = 200          # потолок размера репо (по GitHub API + рабочему дереву)
    max_files: int = 5000           # потолок числа индексируемых файлов
    max_file_bytes: int = 512 * 1024
    max_file_lines: int = 1500

    @property
    def cors_origins_list(self) -> list[str]:
        """CORS_ORIGINS как список хостов (разделитель — запятая), пустых записей нет."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def data_dir(self) -> Path:
        return Path(self.jwp_data_dir)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jworkplace.sqlite"

    @property
    def repos_dir(self) -> Path:
        return self.data_dir / "repos"

    @property
    def indexes_dir(self) -> Path:
        return self.data_dir / "indexes"

    @property
    def worktrees_dir(self) -> Path:
        """Писабельные клоны для PR-флоу (Этап 3b) — отдельно от read-only repos_dir."""
        return self.data_dir / "worktrees"

    @property
    def support_dir(self) -> Path:
        """Данные ассистента поддержки (Задание 2): FAQ-индекс + JSON тикетов/юзеров для MCP."""
        return self.data_dir / "support"


@lru_cache
def get_sett() -> Settings:
    return Settings()


class SecretKeyError(RuntimeError):
    """JWP_SECRET_KEY пуст или не 32-байтный urlsafe-base64 — токен-функции fail-closed."""


def fernet(settings: Settings) -> Fernet:
    """Построить Fernet из JWP_SECRET_KEY. Fail-closed: невалидный/пустой ключ → SecretKeyError,
    а не тихий откат — иначе PAT рисковали бы записаться нешифрованными или с предсказуемым ключом."""
    if not settings.jwp_secret_key:
        raise SecretKeyError("JWP_SECRET_KEY не задан — функции токена отключены (fail-closed).")
    try:
        return Fernet(settings.jwp_secret_key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise SecretKeyError(
            "JWP_SECRET_KEY невалиден (нужен 32-байтный urlsafe-base64, см. Fernet.generate_key())."
        ) from exc
