# CLAUDE.md — backend/ (Python/FastAPI, L3)

> Продуктовые инварианты (grounding, безопасность, слои агентов) — в `../CLAUDE.md`. Здесь —
> построчные конвенции: как писать Python/FastAPI-код именно в этом каталоге.

## Naming & conventions

- Импорты — только абсолютные `from app...` (никогда `from .`/`from ..`).
- Роутер модуля — один `APIRouter(prefix="/api/...")`, объект `router`; в `main.py` импорт с алиасом `router as <area>_router`.
- Pydantic `BaseModel` запроса/ответа — **в модуле роутера**, рядом с эндпоинтом, не в `models.py`.
- Логгер — `logger = logging.getLogger("jworkplace.<area>")`, сразу после импортов; `<area>` = имя модуля.
- Конфиг и секреты (`DEEPSEEK_API_KEY`, GitHub PAT, `JWP_SECRET_KEY`, `GATE_TOKEN`) — только через `Settings(BaseSettings)` + `get_settings()@lru_cache`, никогда хардкод/прямой `os.environ`. Производные пути — `@property` на `Settings`.
- Константы модуля — `UPPER_SNAKE` (`_K`, `_MAX_TOKENS`). Приватные — с `_`.
- Docstring модуля — на русском, первым: что делает файл + какой инвариант CLAUDE.md держит («почему так», не пересказ кода).
- SQLite — raw `sqlite3`, без ORM; каждая операция — короткая функция в `db.py` с явным SQL.
- Блокирующее (git/subprocess, faiss, embed-запросы к Ollama, `hybrid_search`) в async-коде — только через `await asyncio.to_thread(...)`. Тяжёлая последовательная работа (полная индексация) — `asyncio.Semaphore(1)` на уровне модуля-оркестратора (`pipeline.py`).
- Тесты — `tests/test_<module>.py`, один файл на модуль/фичу; секреты в тестах — только синтетические.
- Пины версий для чувствительных зависимостей: `tree_sitter*`, `faiss-cpu`, `numpy`, `cryptography`, `mcp`.

## Хороший код — примеры из реального кода проекта

**(а) Роутер: fail-closed доменный отказ `{ok:false}`, не exception**
```python
# app/api/edit.py :: _cannot
def _cannot(reason: str = CANNOT_EDIT) -> dict:
    return {"ok": False, "reason": reason}
```
Ожидаемый отказ бизнес-логики (abstain-гейт, грязный патч) — 200 `ok:false`; `HTTPException` — только протокольные сбои (404/400/500).

**(б) `get_settings()` под `@lru_cache` + производный путь через `@property`**
```python
# app/config.py :: Settings.worktrees_dir + get_settings
@property
def worktrees_dir(self) -> Path:
    return self.data_dir / "worktrees"

@lru_cache
def get_settings() -> Settings:
    return Settings()
```
Один разбор env на процесс; пути собираются из `jwp_data_dir` в одном месте, не строкой в модулях.

**(в) Блокирующее — через `to_thread`, гейт до вызова LLM**
```python
# app/api/chat.py :: chat
hits = await asyncio.to_thread(hybrid.hybrid_search, req.project_id, query, _K)
abstain, _reason = hybrid.should_abstain(hits)
if abstain:
    return {"answer": ABSTAIN_REPLY, "abstain": True, "sources": []}
```
`hybrid_search` синхронный (FAISS/SQLite) — держал бы event loop без `to_thread`. Абстейн проверяется **до** LLM — экономит вызов, держит инвариант «никакого отката на общие знания».

**(г) Динамическое имя таблицы — только после regex-guard**
```python
# app/db.py :: _SAFE_PROJECT_ID + _fts_table
_SAFE_PROJECT_ID = re.compile(r"^[0-9a-f]{1,32}$")

def _fts_table(project_id: str) -> str:
    if not _SAFE_PROJECT_ID.match(project_id):
        raise ValueError(f"недопустимый project_id: {project_id!r}")
    return f"fts_{project_id}"
```
Имя таблицы нельзя параметризовать `?` (только значения, не идентификаторы) — валидация формата ПЕРЕД f-string, только после неё строка годится для интерполяции.

**(д) `LlmError` — без `repr(exc)`/URL/тела ответа**
```python
# app/llm/deepseek.py :: _request (except-ветка)
except httpx.HTTPStatusError as exc:
    logger.error("DeepSeek API вернул ошибку: HTTP %d", exc.response.status_code)
    raise LlmError(f"DeepSeek API ошибка: HTTP {exc.response.status_code}") from None
```
Логируем только код статуса, не `resp.text` (может эхом вернуть ключ из запроса) — `LlmError` несёт заранее составленную причину, никогда исходное исключение/тело ответа.

## Антипаттерны (запрещено)

- **f-string/конкатенация значений в SQL.** Только `?`-плейсхолдеры; исключение — имя таблицы/колонки, и только whitelisted-строка после regex-guard (пример г).
- **`except: pass` / проглатывание ошибки.** Либо переброс с контекстом (`raise ... from None`), либо fail-closed результат + `logger.exception(...)` — молчаливый `pass` прячет баги и ломает fail-closed-инвариант индексации/PR-флоу.
- **Секрет в лог/ответ/промпт LLM — даже частично.** Ключи не попадают ни в `logger.*`, ни в `HTTPException.detail`, ни в `llm.chat(...)`; пример (д) — код ошибки, не тело ответа.
- **Блокирующий вызов в `async def` без `to_thread`.** git/subprocess/FAISS/httpx-к-Ollama/`hybrid_search` роняют параллелизм остальных эндпоинтов без обёртки.
- **DeepSeek-специфика вне `app/llm/deepseek.py`.** Модель, `response_format`, `finish_reason`/`tool_calls` — остальной код видит только `LlmService`/`get_llm()` из `llm/base.py`; разбор сырого DeepSeek JSON вне адаптера — утечка абстракции.
- **Голый `dict`/`Any` на границе API вместо Pydantic-DTO.** Тело запроса — `BaseModel` с валидацией (`Field(max_length=...)`); внутренний `dict`-результат бизнес-функции — ок, это не граница API, а фиксированный контракт (`{ok, ...}`).

## Шаблон типового файла

```python
"""Эндпоинт <что> (Этап N): <METHOD> /api/<path> — поток retrieve→гейт→генерация→валидация."""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.indexing import hybrid
from app.llm.deepseek import get_llm

logger = logging.getLogger("jworkplace.<area>")
router = APIRouter(prefix="/api/<area>")

_K = 8  # кандидатов от hybrid_search — почему именно столько

class SomeRequest(BaseModel):
    field: str = Field(max_length=2000)

def _fail(reason: str) -> dict:
    return {"ok": False, "reason": reason}  # fail-closed доменный отказ, не exception

@router.post("/{project_id}/action")
async def action(project_id: str, req: SomeRequest) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")  # протокольный сбой
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов.")

    hits = await asyncio.to_thread(hybrid.hybrid_search, project_id, req.field, _K)  # блокирующее → to_thread
    abstain, _reason = hybrid.should_abstain(hits)
    if abstain:
        return _fail("Не могу выполнить по контексту проекта.")  # гейт ДО вызова LLM

    try:
        llm = get_llm(get_settings())  # только через LlmService, не httpx к DeepSeek напрямую
        result = await llm.chat([...], response_format={"type": "json_object"})
    except Exception:
        logger.exception("сбой генерации /action project_id=%s", project_id)  # без repr/тела ответа
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    return {"ok": True, "result": result}
```
