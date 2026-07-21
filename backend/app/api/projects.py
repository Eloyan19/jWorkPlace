"""Эндпоинты проектов: подключение репо, список, детали, reindex, per-project PAT + PR (Этап 3b).

Токен-барьер публичного URL делает nginx (кроме /api/health) — backend его не дублирует.
Индексация — фоновая (pipeline.schedule); статусы отдаём поллингом GET /api/projects/{id}.

PAT-инварианты (must-fix security-auditor, см. PLAN.md): токен шифруется at rest (Fernet),
никогда не возвращается клиенту (`_project_dto` — allowlist, только bool `can_edit`), сверка
diff перед PR — сервер не доверяет клиенту, а сверяет со СВОЕЙ свежей регенерацией.
"""
import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import db
from app.api.edit import generate_validated_edit
from app.config import SecretKeyError, get_settings
from app.edit import github
from app.indexing import faiss_store, pipeline
from app.indexing.validation import ValidationError, parse_github_url, precheck_repo

logger = logging.getLogger("jworkplace.projects")

router = APIRouter(prefix="/api/projects")

# project_id = uuid4().hex[:12] (только [0-9a-f]). Валидируем перед rmtree каталогов проекта —
# защита от traversal/подмены, чтобы удаление не ушло за пределы $JWP_DATA_DIR.
_SAFE_PROJECT_ID = re.compile(r"^[0-9a-f]{1,32}$")


def _safe_project_dir(base: Path, project_id: str) -> Path | None:
    """Каталог проекта внутри `base` ($JWP_DATA_DIR/{repos,worktrees}) с guard'ом: валидный
    project_id И путь не выходит за `base` (resolve + relative_to). None — если небезопасно."""
    if not _SAFE_PROJECT_ID.match(project_id):
        return None
    base = base.resolve()
    target = (base / project_id).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target

# Проекты с уже идущим /pr — писабельный клон worktrees/<pid> общий, второй параллельный
# запрос снёс бы rmtree каталог первого (см. github.open_pr). Single-user, но защищаемся явно.
_pr_in_flight: set[str] = set()


class CreateProjectRequest(BaseModel):
    url: str


class TokenRequest(BaseModel):
    # PAT'ы GitHub (fine-grained) длиной обычно ~90-100 символов; 255 — щедрый, но конечный потолок.
    token: str = Field(min_length=1, max_length=255)


class PrRequest(BaseModel):
    confirm: bool
    instruction: str = Field(max_length=2000)
    expected_diff: str


def _project_dto(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "name": row["name"],
        "status": row["status"],
        "error": row["error"],
        "indexed_at": row["indexed_at"],
        "head_sha": row["head_sha"],
        "can_edit": bool(row["github_token_enc"]),
        "progress_done": row["progress_done"],
        "progress_total": row["progress_total"],
    }


@router.post("")
async def create_project(req: CreateProjectRequest) -> dict:
    try:
        ref = parse_github_url(req.url)
        await precheck_repo(ref)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    pipeline.schedule(project_id, ref.url)
    return {"project_id": project_id, "status": db.STATUS_CLONING}


@router.get("")
def list_projects() -> list[dict]:
    return [_project_dto(r) for r in db.list_projects()]


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    return _project_dto(row)


@router.post("/{project_id}/reindex")
async def reindex_project(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] in db.IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=409, detail="Проект уже обрабатывается.")
    db.set_status(project_id, db.STATUS_CLONING)
    pipeline.schedule(project_id, row["url"], reindex=True)
    return {"status": db.STATUS_CLONING}


@router.post("/{project_id}/rebuild")
async def rebuild_project(project_id: str) -> dict:
    """Полная переиндексация «с нуля»: свежий re-clone (rmtree старого клона + clone_repo) +
    полная пересборка chunks/FTS/FAISS. Для сильно разошедшихся репо, где инкрементальный
    reindex (fetch+reset) недостаточен. Отличие от /reindex — reindex=False (clone вместо pull)."""
    if not _SAFE_PROJECT_ID.match(project_id):
        raise HTTPException(status_code=404, detail="Проект не найден.")
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] in db.IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=409, detail="Проект уже обрабатывается.")
    if project_id in _pr_in_flight:
        # rebuild сделает rmtree repos/<id> под ногами активного open_pr (тот читает индекс/файлы).
        raise HTTPException(status_code=409, detail="Идёт создание PR — дождитесь завершения.")
    db.set_status(project_id, db.STATUS_CLONING)
    pipeline.schedule(project_id, row["url"], reindex=False)
    return {"status": db.STATUS_CLONING}


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    """Полностью удалить проект: строки БД (projects/files/chunks), FTS-таблицу, FAISS-индекс и
    каталоги клона/writable-клона. embed_cache НЕ трогаем — он глобальный (shared по blob_sha
    между проектами). Проект в процессе индексации → 409 (гонка с фоновой задачей pipeline)."""
    # Ранний fail-closed: невалидный id → 404 до любых операций (согласованность с _safe_project_dir;
    # иначе db.drop_fts кинул бы ValueError→500). На практике id из БД всегда валиден — defense-in-depth.
    if not _SAFE_PROJECT_ID.match(project_id):
        raise HTTPException(status_code=404, detail="Проект не найден.")
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] in db.IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=409, detail="Проект обрабатывается — дождитесь завершения.")
    if project_id in _pr_in_flight:
        # Активный open_pr держит worktrees/<id> — не удаляем каталог из-под git/gh push.
        raise HTTPException(status_code=409, detail="Идёт создание PR — дождитесь завершения.")

    settings = get_settings()
    db.delete_project(project_id)      # projects + files + chunks (embed_cache сохраняем)
    db.drop_fts(project_id)            # per-project FTS5-таблица
    faiss_store.delete_index(project_id)  # indexes/<id>/ + инвалидация LRU-кэша
    for base in (settings.repos_dir, settings.worktrees_dir):
        target = _safe_project_dir(base, project_id)
        if target is not None and target.exists():
            shutil.rmtree(target, ignore_errors=True)
    logger.info("проект удалён: %s", project_id)
    return {"deleted": True}


@router.put("/{project_id}/token")
async def set_token(project_id: str, req: TokenRequest) -> dict:
    """Привязать fine-grained PAT к проекту — включает «правки» (реальный PR). Валидируем ПРОТИВ
    репозитория ИМЕННО этого проекта (`ref` из `projects.url`, не из ввода) — не любой валидный
    GitHub-токен подходит, только с `push` на этот конкретный репо. Провал → 400 без деталей токена."""
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    ref = parse_github_url(row["url"])
    ok = await github.validate_token(ref, req.token)
    if not ok:
        raise HTTPException(status_code=400, detail="Токен не подходит: нет доступа на запись к этому репозиторию.")

    try:
        enc = github.encrypt_token(get_settings(), req.token)
    except SecretKeyError:
        # Fail-closed (must-fix #1): без валидного JWP_SECRET_KEY функции токена недоступны —
        # не сохраняем токен нешифрованным и не глотаем ошибку молча.
        raise HTTPException(status_code=503, detail="Функции правок временно недоступны (нет ключа шифрования).")

    db.set_project_token(project_id, enc)
    return {"can_edit": True}


@router.delete("/{project_id}/token")
def delete_token(project_id: str) -> dict:
    """Отвязать PAT — проект возвращается в read-only (клон/индекс/чат/предпросмотр остаются)."""
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    db.clear_project_token(project_id)
    return {"can_edit": False}


@router.post("/{project_id}/pr")
async def create_pr(project_id: str, req: PrRequest) -> dict:
    """Human-in-the-loop PR (Этап 3b): подтверждение обязательно, сервер не доверяет diff от
    клиента — перегенерирует и сверяет (must-fix «регенерация + сверка»). Любой сбой fail-closed;
    PAT живёт только в памяти этого обработчика, наружу (лог/ответ) не течёт."""
    if req.confirm is not True:
        raise HTTPException(status_code=400, detail="Требуется подтверждение (confirm=true).")

    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    enc = row["github_token_enc"]
    if not enc:
        raise HTTPException(status_code=403, detail="Для этого проекта правки отключены (нет привязанного токена).")

    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Пустая инструкция.")

    try:
        result = await generate_validated_edit(project_id, instruction)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        logger.exception("сбой генерации /pr project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    if not result.get("ok"):
        return result

    if result["diff"] != req.expected_diff:
        # Превью, которое видел пользователь, устарело (проект переиндексирован/файлы изменились
        # между генерацией предпросмотра и подтверждением) — не открываем PR по чужому diff.
        return JSONResponse(status_code=409, content={"ok": False, "reason": "превью устарело, обновите"})

    try:
        settings = get_settings()
        token = github.decrypt_token(settings, enc)
    except SecretKeyError:
        raise HTTPException(status_code=503, detail="Функции правок временно недоступны (нет ключа шифрования).")

    if project_id in _pr_in_flight:
        return JSONResponse(status_code=409, content={"ok": False, "reason": "PR по проекту уже открывается"})

    ref = parse_github_url(row["url"])
    _pr_in_flight.add(project_id)
    try:
        pr_url = await asyncio.to_thread(
            github.open_pr, project_id, ref, token, result["diff"], result["summary"], instruction
        )
    except github.GithubError as exc:
        logger.warning("сбой открытия PR project_id=%s: %s", project_id, exc)
        return {"ok": False, "reason": str(exc)}
    except Exception:
        # Непредвиденный сбой (OSError и т.п.) — тоже fail-closed. Generic reason без деталей;
        # токен — локальная переменная, в текст/трейсбек по значению не попадает.
        logger.exception("непредвиденный сбой PR project_id=%s", project_id)
        return {"ok": False, "reason": "не удалось открыть PR"}
    finally:
        _pr_in_flight.discard(project_id)

    return {"ok": True, "pr_url": pr_url}
