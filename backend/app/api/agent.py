"""Эндпоинт файлового tool-агента (Задание 3): POST /api/projects/{id}/agent.

Два режима одним обработчиком:
- ПРОГОН (без confirm): agent.loop.run_agent крутит инструменты под цель → превью. Задача-изменение
  с применимым diff → отдаём diff + run_id (сервер хранит diff у себя, клиенту НЕ доверяет обратно).
  Задача-чтение → отдаём result_text, PR не предполагается.
- ПОДТВЕРЖДЕНИЕ (confirm=true + run_id): human-in-the-loop. Требует привязанного PAT (can_edit).
  Сервер берёт СВОЙ сохранённый diff по run_id, заново проверяет `git apply --check` (проект мог
  переиндексироваться) и вызывает open_pr (паттерн /pr Этапа 3b). Агент недетерминирован, поэтому
  сверяем применимость diff, а не бит-в-бит регенерацию.

Секреты (PAT/ключ LLM) в модель/лог/ответ не попадают; любой сбой fail-closed.
"""
import asyncio
import logging
import time
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import db
from app.agent import loop
from app.api.projects import _pr_in_flight
from app.config import SecretKeyError, get_settings
from app.db import _SAFE_PROJECT_ID
from app.edit import github, patcher
from app.indexing.validation import parse_github_url

logger = logging.getLogger("jworkplace.agent")

router = APIRouter(prefix="/api/projects")

# Превью прогонов агента: run_id -> {project_id, diff, summary, goal, ts}. Сервер хранит diff у себя
# (клиент подтверждает по run_id, не присылает diff обратно — не доверяем чужому патчу). Кэп + TTL,
# чтобы словарь не рос: single-user MVP, объём мал.
_agent_runs: dict[str, dict] = {}
_RUNS_MAX = 32
_RUNS_TTL_S = 1800


class AgentRequest(BaseModel):
    goal: str = Field(default="", max_length=2000)
    confirm: bool = False
    run_id: str | None = None


def _evict_runs() -> None:
    now = time.time()
    stale = [rid for rid, r in _agent_runs.items() if now - r["ts"] > _RUNS_TTL_S]
    for rid in stale:
        _agent_runs.pop(rid, None)
    while len(_agent_runs) > _RUNS_MAX:
        oldest = min(_agent_runs, key=lambda k: _agent_runs[k]["ts"])
        _agent_runs.pop(oldest, None)


@router.post("/{project_id}/agent")
async def run_agent_endpoint(project_id: str, req: AgentRequest) -> dict:
    if not _SAFE_PROJECT_ID.match(project_id):
        raise HTTPException(status_code=404, detail="Проект не найден.")
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    if req.confirm:
        return await _confirm_pr(project_id, row, req)
    return await _run(project_id, req)


async def _run(project_id: str, req: AgentRequest) -> dict:
    goal = req.goal.strip()
    if not goal:
        raise HTTPException(status_code=400, detail="Пустая цель.")
    try:
        result = await loop.run_agent(project_id, goal)
    except Exception:
        logger.exception("сбой прогона агента project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    if result.get("needs_pr") and result.get("diff"):
        _evict_runs()
        run_id = uuid.uuid4().hex
        _agent_runs[run_id] = {
            "project_id": project_id, "diff": result["diff"],
            "summary": result["summary"], "goal": goal, "ts": time.time(),
        }
        can_edit = db.get_project_token_enc(project_id) is not None
        return {
            "ok": True, "needs_pr": True, "run_id": run_id, "can_edit": can_edit,
            "diff": result["diff"], "result_text": result["result_text"],
            "sources": result["sources"],
        }
    return {
        "ok": True, "needs_pr": False,
        "result_text": result["result_text"], "sources": result.get("sources", []),
    }


async def _confirm_pr(project_id: str, row, req: AgentRequest):
    run = _agent_runs.get(req.run_id or "")
    if run is None or run["project_id"] != project_id:
        raise HTTPException(status_code=409, detail="Превью устарело — повторите запрос агенту.")

    enc = row["github_token_enc"]
    if not enc:
        raise HTTPException(status_code=403, detail="Для этого проекта правки отключены (нет привязанного токена).")

    diff = run["diff"]
    # Проект мог переиндексироваться между прогоном и подтверждением → патч мог перестать применяться.
    if not patcher.check_apply(project_id, diff):
        _agent_runs.pop(req.run_id, None)
        return JSONResponse(status_code=409, content={"ok": False, "reason": "превью устарело, повторите запрос"})

    try:
        token = github.decrypt_token(get_settings(), enc)
    except SecretKeyError:
        raise HTTPException(status_code=503, detail="Функции правок временно недоступны (нет ключа шифрования).")

    if project_id in _pr_in_flight:
        return JSONResponse(status_code=409, content={"ok": False, "reason": "PR по проекту уже открывается"})

    ref = parse_github_url(row["url"])
    _pr_in_flight.add(project_id)
    try:
        pr_url = await asyncio.to_thread(
            github.open_pr, project_id, ref, token, diff, run["summary"], run["goal"]
        )
    except github.GithubError as exc:
        logger.warning("сбой открытия PR (агент) project_id=%s: %s", project_id, exc)
        return {"ok": False, "reason": str(exc)}
    except Exception:
        logger.exception("непредвиденный сбой PR (агент) project_id=%s", project_id)
        return {"ok": False, "reason": "не удалось открыть PR"}
    finally:
        _pr_in_flight.discard(project_id)

    _agent_runs.pop(req.run_id, None)
    return {"ok": True, "pr_url": pr_url}
