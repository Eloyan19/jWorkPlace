---
name: jWorkPlace — backend (Python/FastAPI)
globs: backend/**
description: Построчные конвенции Python/FastAPI (дистиллят backend/CLAUDE.md).
---

# Backend (Python/FastAPI)

- Импорты — **абсолютные** `from app...`, никогда `from .`/`from ..`.
- Роутер модуля — один `APIRouter(prefix="/api/...")` с именем `router`; Pydantic-DTO — рядом с эндпоинтом.
- **Блокирующее** (git/subprocess, FAISS, embed к Ollama, `hybrid_search`) в async — только через
  `await asyncio.to_thread(...)`.
- Конфиг/секреты — через `Settings(BaseSettings)` + `get_settings()` (`@lru_cache`), не `os.environ` напрямую.
- **SQLite** — raw `sqlite3`, `?`-плейсхолдеры. **Никаких f-string/конкатенации значений в SQL.**
  Имя таблицы динамически — только после regex-guard формата.
- Ожидаемый отказ → `{"ok": false, "reason": ...}` (200), `HTTPException` — только протокольные сбои (404/400/500).
- **Секрет не попадает в лог/ответ/промпт** — логируем код статуса, не тело ответа LLM/API.
- `except: pass` запрещён — либо `raise ... from None` с контекстом, либо fail-closed + `logger.exception`.
- Тесты — `tests/test_<module>.py`, офлайн (monkeypatch), синтетические секреты не формата провайдеров.
