"""Эндпоинт grounded-чата (Этап 2b): POST /api/chat.

retrieve (hybrid_search) → гейт should_abstain (без вызова LLM при abstain) → build_context →
DeepSeek JSON-режим → line-based валидация цитат. Инвариант CLAUDE.md: ответ без валидных
источников — потенциальная галлюцинация → downgrade в предзаданное «не знаю». История чата
(takeLast/summary) — отложена до Этапа 2b+: retrieve всегда по последнему user-сообщению.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.chat import grounding
from app.config import get_settings
from app.indexing import hybrid
from app.llm.deepseek import LlmError, get_llm

logger = logging.getLogger("jworkplace.chat")

router = APIRouter(prefix="/api/chat")

_K = 8              # кандидатов от hybrid_search
_CONTEXT_N = 6       # из них — в промпт (экономия контекста)
_MAX_HISTORY = 12    # потолок ходов диалога в промпт (стоимость/латентность; takeLast)
# Потолок токенов ответа. Grounded-ответ несёт дословные цитаты кода — на широких вопросах
# («что делает проект») дефолтных 1024 (retry→2048) не хватало → обрезка → LlmError. max_tokens —
# лишь ceiling (короткие ответы не дорожают), поэтому берём щедро: 4096, адаптер удвоит на retry
# до 8192 (предел deepseek-chat).
_MAX_TOKENS = 4096

ABSTAIN_REPLY = "Не знаю по этому проекту, уточните вопрос."
# Мягкий ответ, когда провайдер не смог сгенерировать (напр. слишком объёмный ответ обрезан по длине).
GENERATION_FAILED_REPLY = "Не удалось сформировать ответ — попробуйте задать вопрос точнее или короче."

# Роли, которым доверяем в диалоге. Всё остальное (в т.ч. подсунутый клиентом "system")
# приводим к "user" — чужой текст НЕ должен попасть в system и переопределить grounding.
_ALLOWED_ROLES = ("user", "assistant")


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    project_id: str
    messages: list[ChatMessage]


def _last_user_content(messages: list[ChatMessage]) -> str:
    for m in reversed(messages):
        if m.role == "user":
            return m.content.strip()
    return ""


async def _generate(project_id: str, messages: list[ChatMessage], hits: list[dict]) -> tuple[str, list[dict]]:
    """JSON-генерация + валидация с одним retry на пустых валидных цитатах."""
    llm = get_llm(get_settings())
    context = grounding.build_context(hits, n=_CONTEXT_N)
    system = {"role": "system", "content": f"{grounding.SYSTEM_PROMPT}\n\nФрагменты:\n{context}"}
    # takeLast + приведение роли: единственный system-месседж — наш (см. _ALLOWED_ROLES).
    dialog = [
        {"role": m.role if m.role in _ALLOWED_ROLES else "user", "content": m.content}
        for m in messages[-_MAX_HISTORY:]
    ]

    raw = await llm.chat([system, *dialog], response_format={"type": "json_object"}, max_tokens=_MAX_TOKENS)
    answer, sources, _dropped = grounding.parse_and_validate(raw, hits, project_id)
    if sources:
        return answer, sources

    nudge = {"role": "system", "content": grounding.QUOTE_RETRY_NUDGE}
    raw = await llm.chat([system, nudge, *dialog], response_format={"type": "json_object"}, max_tokens=_MAX_TOKENS)
    answer, sources, _dropped = grounding.parse_and_validate(raw, hits, project_id)
    return answer, sources


@router.post("")
async def chat(req: ChatRequest) -> dict:
    query = _last_user_content(req.messages)
    if not query:
        raise HTTPException(status_code=400, detail="Пустой запрос.")

    row = db.get_project(req.project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    try:
        hits = await asyncio.to_thread(hybrid.hybrid_search, req.project_id, query, _K)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    abstain, _reason = hybrid.should_abstain(hits)
    if abstain:
        # Гейт «не знаю» — предзаданный ответ БЕЗ вызова генерации (CLAUDE.md: никакого
        # отката на общие знания модели).
        return {"answer": ABSTAIN_REPLY, "abstain": True, "sources": []}

    context_hits = hits[:_CONTEXT_N]
    try:
        answer, sources = await _generate(req.project_id, req.messages, context_hits)
    except LlmError:
        # Провайдер не смог сгенерировать (напр. объёмный ответ обрезан по длине даже после retry).
        # Это не «внутренняя ошибка» сервиса — отдаём мягкий ответ, без 500 и без отката на знания модели.
        logger.warning("генерация /api/chat не удалась (LLM) project_id=%s", req.project_id)
        return {"answer": GENERATION_FAILED_REPLY, "abstain": True, "sources": []}
    except Exception:
        logger.exception("сбой генерации /api/chat project_id=%s", req.project_id)
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    if not sources:
        # Ответ без единой валидной цитаты (в т.ч. пустой answer) — не отличим от галлюцинации,
        # downgrade в abstain. Никакого отката на общие знания модели (CLAUDE.md).
        return {"answer": ABSTAIN_REPLY, "abstain": True, "sources": []}

    return {"answer": answer, "abstain": False, "sources": sources}
