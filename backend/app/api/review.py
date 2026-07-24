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
from app.indexing.chunker import MAX_CHUNK_LINES
from app.llm.deepseek import get_llm
from app.review import reviewer

logger = logging.getLogger("jworkplace.review")

router = APIRouter(prefix="/api/projects")

_K = 6                # hybrid_search-кандидатов на запрос
_CONTEXT_CAP = 12      # суммарный кап RAG-чанков в промпте
_MAX_TOKENS = 2048     # бюджет на ответ (адаптер удвоит на retry при обрезке finish_reason=length)

# --- бюджет diff для LLM: единый источник правды, а не независимое магическое число ---
#
# _DIFF_LIMIT — мягкий потолок «сколько diff реально уходит в промпт ревью» (используется
# truncate_diff, который отмечает факт обрезки для честного предупреждения в markdown — см.
# generate_review). Выводим его из РЕАЛЬНЫХ констант бюджета, а не гадаем на глаз: если
# _CONTEXT_CAP здесь или MAX_CHUNK_LINES (app/indexing/chunker.py) вырастут, _RAG_CONTEXT_CEILING
# ниже пересчитается сам — не нужно синхронизировать два места руками.
#
# Модель ревью — `deepseek-chat`, контекст 128K токенов на весь запрос (system+user+ответ).
_MODEL_CONTEXT_TOKENS = 128_000
# Код/diff токенизируется плотнее прозы — берём консервативно МЕНЬШЕ symbols/token (меньше =
# меньше кажущегося свободного места), чтобы не переоценить бюджет.
_CHARS_PER_TOKEN = 3
# Единственное недоказуемое числом допущение во всей формуле (нет исходной константы «длина
# строки кода») — средняя длина строки чанка. Раздут специально в БОЛЬШУЮ сторону (запас).
_ASSUMED_CHARS_PER_LINE = 80

# RAG-контекст в промпте: единственный источник правды по объёму — _CONTEXT_CAP (сколько чанков,
# локально выше) × MAX_CHUNK_LINES (максимум строк в чанке, app/indexing/chunker.py).
_RAG_CONTEXT_CEILING_CHARS = _CONTEXT_CAP * MAX_CHUNK_LINES * _ASSUMED_CHARS_PER_LINE

# Заголовок/тело PR и системный промпт — фиксированные величины запроса.
_SYSTEM_PROMPT_CEILING_CHARS = 1_500       # REVIEW_SYSTEM_PROMPT — статический текст
_PR_TITLE_MAX_CHARS = 1_000                # держим в синхроне с Field(max_length) ниже
_PR_BODY_MAX_CHARS = 10_000                # держим в синхроне с Field(max_length) ниже
_META_CEILING_CHARS = _SYSTEM_PROMPT_CEILING_CHARS + _PR_TITLE_MAX_CHARS + _PR_BODY_MAX_CHARS

# Ответ модели: _MAX_TOKENS, адаptер удваивает на retry при finish_reason=length.
_OUTPUT_CEILING_CHARS = (_MAX_TOKENS * 2) * _CHARS_PER_TOKEN

_OVERHEAD_CEILING_CHARS = _RAG_CONTEXT_CEILING_CHARS + _META_CEILING_CHARS + _OUTPUT_CEILING_CHARS
_MAX_SAFE_DIFF_CHARS = _MODEL_CONTEXT_TOKENS * _CHARS_PER_TOKEN - _OVERHEAD_CEILING_CHARS

# Финальное значение — круглое число В ПРЕДЕЛАХ вычисленного бюджета (не сам _MAX_SAFE_DIFF_CHARS
# впрямую: он «дрожит» при мелких правках допущений выше, а нам нужен стабильный, легко узнаваемый
# лимит). 200_000 с запасом перекрывает PR #6 (~187 КБ) целиком, без усечения.
_DIFF_LIMIT = 200_000
assert _DIFF_LIMIT <= _MAX_SAFE_DIFF_CHARS, (
    "_DIFF_LIMIT превышает бюджет контекста deepseek-chat при текущих _CONTEXT_CAP/"
    "MAX_CHUNK_LINES — либо снижай _DIFF_LIMIT, либо снижай _CONTEXT_CAP/MAX_CHUNK_LINES."
)

# _REQUEST_MAX_DIFF_LEN — жёсткий протокольный потолок Field(max_length): защита от абсурдных
# payload (DoS/раздутый бинарный дамп), НЕ бюджет LLM. Специально БОЛЬШЕ _DIFF_LIMIT — иначе
# Pydantic отбрасывал бы diff между _DIFF_LIMIT и этим потолком 422-кой ещё ДО truncate_diff,
# и честное предупреждение об усечении (см. generate_review) никогда бы не срабатывало.
# Совпадает с аварийным потолком `head -c` в ai-review.yml.
_REQUEST_MAX_DIFF_LEN = 2_000_000


class ReviewRequest(BaseModel):
    # Хард-лимит запроса (_REQUEST_MAX_DIFF_LEN), НЕ мягкий бюджет LLM (_DIFF_LIMIT) — иначе
    # graceful truncate_diff() ниже стал бы недостижимым мёртвым кодом (см. комментарий выше).
    diff: str = Field(max_length=_REQUEST_MAX_DIFF_LEN)
    changed_files: list[str] = Field(default_factory=list, max_length=500)
    pr_number: int
    # max_length держим в синхроне с _PR_TITLE_MAX_CHARS/_PR_BODY_MAX_CHARS выше (тот же бюджет).
    pr_title: str = Field(default="", max_length=_PR_TITLE_MAX_CHARS)
    pr_body: str | None = Field(default=None, max_length=_PR_BODY_MAX_CHARS)


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
