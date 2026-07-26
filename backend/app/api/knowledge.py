"""Эндпоинты базы знаний проекта (P1): выжимка о репо + персонализация «что я уже знаю».

`GET .../summary` — lazy-генерация: если сохранённая выжимка устарела (head_sha разошёлся с
`projects.head_sha` после reindex) или её ещё нет, ставит фоновую генерацию под guard'ом и
отдаёт `{"status": "generating"}` — фронт поллит. `POST .../read` — авто-пометка известных
концептов при открытии панели выжимки (идемпотентно). `GET /concepts` — глобальный каталог
«что я знаю» (опциональная панель).

Генерация — 1 вызов LLM + немного embed_query (лёгкая операция), НЕ участвует в
`Semaphore(1)` пайплайна индексации — не блокируем реиндексацию других проектов. KB строго
вне grounded code-Q&A пути: этот роутер не читается из `app/api/chat.py`.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app import db
from app.knowledge import generator, render

logger = logging.getLogger("jworkplace.knowledge")

router = APIRouter(prefix="/api/knowledge")

# Паттерн _pr_in_flight (app/api/projects.py): не гоняем генерацию параллельно для одного
# проекта. _gen_errors — причина последней неудачной попытки, в памяти процесса (не персистится
# в БД — это лёгкая генерация, а не индексация). GET после ошибки ОТДАЁТ и ОЧИЩАЕТ её: следующий
# запрос (повторное открытие панели фронтом, который не поллит на статусе "error") сам запустит
# новую попытку — self-heal на транзиентных сбоях LLM без тихого бесконечного ретрая на каждый тик.
_gen_in_flight: set[str] = set()
_gen_errors: dict[str, str] = {}
_bg_tasks: set[asyncio.Task] = set()


async def _run_generate(project_id: str) -> None:
    try:
        result = await generator.generate(project_id)
        if result.get("ok"):
            _gen_errors.pop(project_id, None)  # defense-in-depth: не оставлять стухший error
        else:
            _gen_errors[project_id] = result.get("reason", "generation_failed")
    except Exception:
        # Непредвиденный сбой (не доменный fail-closed generator.generate) — тоже не роняем
        # процесс; причина в логе, клиенту — обобщённо (без repr/секретов).
        logger.exception("сбой генерации базы знаний project_id=%s", project_id)
        _gen_errors[project_id] = "internal_error"
    finally:
        _gen_in_flight.discard(project_id)


@router.get("/projects/{project_id}/summary")
async def get_summary(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    summary = await asyncio.to_thread(db.get_summary, project_id)
    # Коэрсия None->"" на обе стороны — согласовано с generator.generate (тоже приводит
    # row["head_sha"] or "" перед save_summary), иначе пустой head_sha никогда бы не совпал
    # с самим собой и генерация перезапускалась бы на каждый поллинг.
    if summary is not None and (summary["head_sha"] or "") == (row["head_sha"] or ""):
        return await asyncio.to_thread(render.render, project_id)

    if project_id in _gen_in_flight:
        return {"status": "generating"}

    reason = _gen_errors.pop(project_id, None)
    if reason is not None:
        return {"status": "error", "reason": reason}

    _gen_in_flight.add(project_id)
    task = asyncio.create_task(_run_generate(project_id))
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"status": "generating"}


@router.post("/projects/{project_id}/read")
async def mark_read(project_id: str) -> dict:
    """Пометить концепты этого проекта известными — идемпотентно, безопасно вызывать повторно."""
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    await asyncio.to_thread(db.mark_concepts_known, project_id)
    return {"ok": True}


@router.get("/concepts")
async def list_concepts() -> list[dict]:
    """Глобальный каталог «что я знаю» — bare-массив (см. конвенцию GET /api/projects)."""
    rows = await asyncio.to_thread(db.list_known_concepts)
    return [
        {
            "slug": r["slug"], "name": r["name"], "category": r["category"],
            "description": r["description"], "known_at": r["known_at"],
        }
        for r in rows
    ]
