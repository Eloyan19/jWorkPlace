"""Эндпоинт retrieval без LLM (Этап 2a): POST /api/search → hybrid search по активному проекту.

Токен-барьер публичного URL — на nginx (кроме /api/health), backend его не дублирует.
Отдаём сырые скоры каналов (dense_score/bm25_score) — Этап 2b применит по ним свой порог.
"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.chat.grounding import redact
from app.indexing import hybrid

router = APIRouter(prefix="/api/search")

_MAX_K = 20


class SearchRequest(BaseModel):
    project_id: str
    query: str
    k: int = Field(default=8, ge=1, le=_MAX_K)


def _hit_dto(hit: dict) -> dict:
    return {
        "file": hit["file"],
        "symbol": hit["symbol"],
        "symbol_kind": hit["symbol_kind"],
        "lang": hit["lang"],
        "start_line": hit["start_line"],
        "end_line": hit["end_line"],
        "citation": hit["citation"],
        "dense_score": hit["dense_score"],
        "bm25_score": hit["bm25_score"],
        "rrf_score": hit["rrf_score"],
        "text": redact(hit["text"]),
    }


@router.post("")
async def search(req: SearchRequest) -> dict:
    query = req.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Пустой запрос.")

    row = db.get_project(req.project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    try:
        # Блокирующие шаги (Ollama-эмбеддинг запроса + FAISS + SQLite) — в отдельный поток.
        hits = await asyncio.to_thread(hybrid.hybrid_search, req.project_id, query, req.k)
    except ValueError as exc:
        # напр. запрос длиннее контекста эмбеддера
        raise HTTPException(status_code=400, detail=str(exc))

    abstain, reason = hybrid.should_abstain(hits)
    return {
        "project_id": req.project_id,
        "query": query,
        "k": req.k,
        "abstain": abstain,
        "abstain_reason": reason,
        "hits": [] if abstain else [_hit_dto(h) for h in hits],
    }
