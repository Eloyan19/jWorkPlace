"""Эндпоинт AI-ревью Pull Request (Этап 3c): POST /api/projects/{id}/review.

Дизайн (llm-engineer + architect + security-auditor, `velvety-finding-zephyr.md`): GitHub Action
на `pull_request` шлёт сюда diff + метаданные PR, backend генерирует один структурированный
markdown-комментарий (баги / архитектурные проблемы / рекомендации) через тот же RAG+DeepSeek
стек, что `/edit`. Ревью — не блокирующий check: сервис только комментирует, approve/вердикт-поля
нет. `hybrid.should_abstain` здесь НЕ вызывается (в отличие от `/edit`) — ревью самого diff должно
случиться независимо от того, нашёлся ли уверенный сосед в индексе (RAG — вспомогательный контекст).

Fail-closed: любой сбой → 500 без утечки сырого diff в лог/ответ (diff — недоверенные данные PR,
могут содержать секреты чужой ветки). Логируем только project_id, размер diff и число замечаний.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.config import get_settings
from app.llm.deepseek import get_llm
from app.review import reviewer

logger = logging.getLogger("jworkplace.review")

router = APIRouter(prefix="/api/projects")

_K = 6                # hybrid_search-кандидатов на запрос
_CONTEXT_CAP = 12      # суммарный кап RAG-чанков в промпте
_MAX_TOKENS = 2048     # бюджет на ответ (адаптер удвоит на retry при обрезке finish_reason=length)
_DIFF_LIMIT = 100_000  # символов — совпадает с Field(max_length) ниже (труcate_diff — вторая линия обороны)


class ReviewRequest(BaseModel):
    # Потолок совпадает с truncate_diff — 422 раньше, чем сервер начнёт работу с гигантским diff.
    diff: str = Field(max_length=_DIFF_LIMIT)
    changed_files: list[str] = Field(default_factory=list, max_length=500)
    pr_number: int
    pr_title: str = Field(default="", max_length=1000)
    pr_body: str | None = Field(default=None, max_length=10_000)


@router.post("/{project_id}/review")
async def review_pull_request(project_id: str, req: ReviewRequest) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    try:
        result = await generate_review(
            project_id,
            diff=req.diff,
            changed_files=req.changed_files,
            pr_title=req.pr_title,
            pr_body=req.pr_body,
        )
    except Exception:
        # Сырой diff/тело PR в лог не попадают — только размеры (могут нести секреты чужой ветки).
        logger.exception(
            "сбой генерации /review project_id=%s pr_number=%d diff_len=%d",
            project_id, req.pr_number, len(req.diff),
        )
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    return result


async def generate_review(
    project_id: str,
    *,
    diff: str,
    changed_files: list[str],
    pr_title: str,
    pr_body: str | None,
) -> dict:
    """Единый серверный путь генерации ревью: truncate → parse_diff → построить RAG-запросы →
    retrieve (без гейта should_abstain — ревью самого diff случается всегда) → генерация JSON →
    parse_review → render_markdown (redact на выходе, комментарий публичен)."""
    truncated_diff, was_truncated = reviewer.truncate_diff(diff, _DIFF_LIMIT)
    hunks = reviewer.parse_diff(truncated_diff)
    queries = reviewer.build_review_queries(changed_files, hunks)
    hits = await asyncio.to_thread(reviewer.retrieve_context, project_id, queries, _K, _CONTEXT_CAP)

    raw = await _generate(project_id, hits, hunks, pr_title, pr_body)
    review = reviewer.parse_review(raw)
    markdown = reviewer.render_markdown(review)
    if was_truncated:
        markdown = markdown.replace(
            reviewer.REVIEW_MARKER,
            f"{reviewer.REVIEW_MARKER}\n\n_⚠️ diff обрезан до {_DIFF_LIMIT} символов — показаны "
            "замечания только по видимой части._",
            1,
        )

    n_findings = len(review["bugs"]) + len(review["architecture"]) + len(review["recommendations"])
    logger.info(
        "review сгенерирован project_id=%s diff_len=%d truncated=%s hunks=%d findings=%d",
        project_id, len(truncated_diff), was_truncated, len(hunks), n_findings,
    )

    return {
        "ok": True,
        "review_markdown": markdown,
        "sources": [{"citation": h["citation"]} for h in hits],
    }


async def _generate(
    project_id: str, hits: list[dict], hunks: list[reviewer.Hunk], pr_title: str, pr_body: str | None
) -> str:
    llm = get_llm(get_settings())
    messages = reviewer.build_review_prompt(hits, hunks, pr_title, pr_body)
    return await llm.chat(
        messages,
        response_format={"type": "json_object"},
        temperature=0.0,
        max_tokens=_MAX_TOKENS,
    )
