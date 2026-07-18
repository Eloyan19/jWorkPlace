"""Hybrid search: слияние dense (FAISS/nomic) и лексического (FTS5/bm25) каналов через RRF.

Дизайн rag-indexing-engineer:
- Каналы объединяем по chunk_id (dense отдаёт faiss_id, lex — chunk_id; оба резолвим в chunks).
- RRF (Reciprocal Rank Fusion): rrf(d) = Σ_канал 1/(k + rank), k=60 — устойчиво сливает
  разномасштабные скоры (cosine vs bm25) без калибровки.
- Гейт «не знаю» — по СЫРЫМ скорам каналов, НЕ по RRF (RRF зависит только от рангов и для
  порога неинформативен). API отдаёт сырые скоры наружу, чтобы Этап 2b применил свой порог.
- Без FTS (проекты до Этапа 2a) → dense-only fallback, не падаем.
"""
from app import db
from app.indexing import embeddings, faiss_store, lexical

# RRF-константа: сглаживает хвост рангов, стандарт (Cormack et al.).
_RRF_K = 60
# Пороги гейта abstain (калибровка на eval/golden_markupsafe.json — стартовые, пересматриваем на
# новых репо). nomic со search_query/search_document даёт «пол» косинуса ~0.55 даже off-topic,
# поэтому dense-порог высокий. Позитивы golden: cosine 0.65–0.80; negatives: 0.54–0.59.
DENSE_ABSTAIN_THRESHOLD = 0.62
# Лексический канал «спасает» ответ (при слабом dense) только УВЕРЕННЫМ хитом — точный идентификатор,
# а не общее английское слово (bm25 меньше = сильнее). Позитивы: bm25 ≤ −5.9; ложный хит на «state» = −2.7.
BM25_CONFIDENT_THRESHOLD = -4.0


def _citation(file: str, symbol: str | None, start_line: int, end_line: int) -> str:
    """Источник в формате CLAUDE.md: file::symbol::Lstart-Lend."""
    return f"{file}::{symbol or '—'}::L{start_line}-{end_line}"


def _hit_from_row(row) -> dict:
    return {
        "chunk_id": row["chunk_id"],
        "faiss_id": row["faiss_id"],
        "file": row["file"],
        "lang": row["lang"],
        "symbol": row["symbol"],
        "symbol_kind": row["symbol_kind"],
        "start_line": row["start_line"],
        "end_line": row["end_line"],
        "blob_sha": row["blob_sha"],
        "text": row["text"],
        "citation": _citation(row["file"], row["symbol"], row["start_line"], row["end_line"]),
        "dense_score": None,
        "bm25_score": None,
        "dense_rank": None,
        "lex_rank": None,
        "rrf_score": 0.0,
    }


def hybrid_search(
    project_id: str,
    query: str,
    k: int = 8,
    *,
    k_dense: int = 50,
    k_lex: int = 50,
) -> list[dict]:
    """top-k чанков активного проекта по слиянию dense+lex. Отсортировано по rrf_score (убыв.).

    Каждый Hit несёт сырые dense_score (cosine) / bm25_score (или None, если чанк не из канала),
    ранги каналов и rrf_score. Пустой индекс/нет кандидатов → пустой список.
    """
    hits: dict[int, dict] = {}  # chunk_id -> Hit

    # --- dense-канал ---
    qvec = embeddings.embed_query(query)
    dense_hits = faiss_store.search(project_id, qvec, k_dense)  # [(faiss_id, cosine)]
    dense_rows = db.chunks_by_faiss_ids(project_id, [fid for fid, _ in dense_hits])
    for rank, (faiss_id, cosine) in enumerate(dense_hits, start=1):
        row = dense_rows.get(faiss_id)
        if row is None:
            continue
        cid = row["chunk_id"]
        hit = hits.setdefault(cid, _hit_from_row(row))
        hit["dense_score"] = cosine
        hit["dense_rank"] = rank
        hit["rrf_score"] += 1.0 / (_RRF_K + rank)

    # --- лексический канал (FTS5), если таблица существует ---
    if db.fts_exists(project_id):
        match_query = lexical.build_match_query(query)
        if match_query:
            lex_hits = db.fts_search(project_id, match_query, k_lex)  # [(chunk_id, bm25)]
            lex_rows = db.chunks_by_ids(project_id, [cid for cid, _ in lex_hits])
            for rank, (cid, bm25) in enumerate(lex_hits, start=1):
                row = lex_rows.get(cid)
                if row is None:
                    continue
                hit = hits.setdefault(cid, _hit_from_row(row))
                hit["bm25_score"] = bm25
                hit["lex_rank"] = rank
                hit["rrf_score"] += 1.0 / (_RRF_K + rank)

    ranked = sorted(hits.values(), key=lambda h: h["rrf_score"], reverse=True)
    return ranked[:k]


def should_abstain(hits: list[dict]) -> tuple[bool, str | None]:
    """Гейт «не знаю» по сырым скорам. abstain ⟺ dense семантически далёк И нет УВЕРЕННОГО
    лексического хита (общее слово вроде «state» не считается — иначе off-topic-запрос протекает)."""
    if not hits:
        return True, "нет кандидатов"
    dense_scores = [h["dense_score"] for h in hits if h["dense_score"] is not None]
    max_dense = max(dense_scores) if dense_scores else None
    bm25_scores = [h["bm25_score"] for h in hits if h["bm25_score"] is not None]
    best_bm25 = min(bm25_scores) if bm25_scores else None  # bm25 отрицателен, меньше = сильнее
    dense_weak = max_dense is None or max_dense < DENSE_ABSTAIN_THRESHOLD
    lex_weak = best_bm25 is None or best_bm25 > BM25_CONFIDENT_THRESHOLD
    if dense_weak and lex_weak:
        return True, "ничего релевантного не найдено"
    return False, None
