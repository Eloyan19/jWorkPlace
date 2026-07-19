"""Эндпоинт предпросмотра правки (Этап 3a): POST /api/projects/{id}/edit.

retrieve (hybrid_search по инструкции) → гейт should_abstain (без вызова LLM при abstain) →
генерация структурированных edits → line-based валидация по файлу на диске → детерминированная
сборка diff → `git apply --check`. Ничего не пишем на диск, не пушим, GitHub-токен не участвует —
это только предпросмотр (реальный PR придёт на Этапе 3b). Любой провал fail-closed → {ok:false}.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.edit import patcher
from app.indexing import hybrid
from app.llm.deepseek import get_llm

logger = logging.getLogger("jworkplace.edit")

router = APIRouter(prefix="/api/projects")

_K = 8               # кандидатов от hybrid_search
_CONTEXT_N = 6        # из них — в промпт
_MAX_TOKENS = 2048    # бюджет на ответ с diff (адаптер удвоит на retry при обрезке)

CANNOT_EDIT = "Не могу выполнить эту правку по коду проекта."
PATCH_DIRTY = "Предложенный патч не применяется чисто — правка отклонена."


class EditRequest(BaseModel):
    # Потолок длины: инструкция целиком уходит в промпт LLM (стоимость/латентность). Как у
    # интерактивных запросов — короткая; огромный ввод отсекаем до вызова DeepSeek (422).
    instruction: str = Field(max_length=2000)


def _cannot(reason: str = CANNOT_EDIT) -> dict:
    return {"ok": False, "reason": reason}


@router.post("/{project_id}/edit")
async def propose_edit(project_id: str, req: EditRequest) -> dict:
    instruction = req.instruction.strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="Пустая инструкция.")

    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    try:
        hits = await asyncio.to_thread(hybrid.hybrid_search, project_id, instruction, _K)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    abstain, _reason = hybrid.should_abstain(hits)
    if abstain:
        # Гейт «не могу»: ни один чанк не прошёл порог — не выдумываем правку (CLAUDE.md: никакого
        # отката на общие знания модели).
        return _cannot()

    context_hits = hits[:_CONTEXT_N]
    try:
        summary, edits, dropped = await _generate(project_id, instruction, context_hits)
    except Exception:
        logger.exception("сбой генерации /edit project_id=%s", project_id)
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    if not edits:
        return _cannot()

    diff = patcher.assemble_diff(edits, project_id)
    if not patcher.check_apply(project_id, diff):
        return _cannot(PATCH_DIRTY)

    return {
        "ok": True,
        "summary": summary,
        "diff": diff,
        "edits": [{"file": e["file"], "reason": e["reason"]} for e in edits],
        "sources": [{"file": e["file"], "citation": e["citation"], "quote": e["old_block"]} for e in edits],
        "dropped": dropped,
    }


async def _generate(project_id: str, instruction: str, hits: list[dict]) -> tuple[str, list[dict], int]:
    llm = get_llm(get_settings())
    context = patcher.build_edit_context(hits, project_id, n=_CONTEXT_N)
    system = {"role": "system", "content": f"{patcher.EDIT_SYSTEM_PROMPT}\n\nФрагменты:\n{context}"}
    user = {"role": "user", "content": instruction}
    raw = await llm.chat(
        [system, user],
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
    )
    return patcher.parse_and_validate_edits(raw, hits, project_id)
