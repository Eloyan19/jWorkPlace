"""Тесты hybrid search: RRF-слияние, dense-only fallback без FTS, гейт abstain.

Dense-канал мокаем (embed_query + faiss_store.search), чтобы не ходить в Ollama/FAISS;
лексический канал — настоящий FTS5 в изолированной БД.
"""
import numpy as np

from app import db
from app.indexing import embeddings, faiss_store, hybrid, lexical

PID = "abc123def456"


def _seed():
    db.create_project(PID, "u", "n", db.STATUS_READY)
    ids = db.insert_chunks([
        {"project_id": PID, "file": "src/__init__.py", "lang": "python", "symbol": "escape",
         "symbol_kind": "function_definition", "start_line": 1, "end_line": 5,
         "blob_sha": "s1", "text": "def escape(s): return markup"},
        {"project_id": PID, "file": "src/util.py", "lang": "python", "symbol": "striptags",
         "symbol_kind": "function_definition", "start_line": 10, "end_line": 20,
         "blob_sha": "s2", "text": "def striptags(s): return stripped"},
    ])
    db.set_faiss_ids([(0, ids[0]), (1, ids[1])])
    db.create_fts(PID)
    rows = [
        ("def escape(s): return markup", "escape", "src/__init__.py"),
        ("def striptags(s): return stripped", "striptags", "src/util.py"),
    ]
    db.fts_insert(PID, [
        (ids[i], lexical.code_tokenize(t), lexical.code_tokenize(sym), lexical.code_tokenize(f))
        for i, (t, sym, f) in enumerate(rows)
    ])
    return ids


def _mock_dense(monkeypatch, hits):
    """faiss возвращает hits=[(faiss_id, cosine)]; embed_query — заглушка (сеть не трогаем)."""
    monkeypatch.setattr(embeddings, "embed_query", lambda q: np.zeros(768, dtype="float32"))
    monkeypatch.setattr(faiss_store, "search", lambda pid, qv, k: hits)


def test_lexical_channel_rescues_exact_identifier(data_dir, monkeypatch):
    ids = _seed()
    # dense ошибочно ставит escape (faiss_id 0) выше striptags (faiss_id 1).
    _mock_dense(monkeypatch, [(0, 0.60), (1, 0.50)])
    result = hybrid.hybrid_search(PID, "striptags", k=8)
    # RRF: striptags получает вклад от лексического канала (точный идентификатор) → выходит вперёд.
    assert result[0]["symbol"] == "striptags"
    top = result[0]
    assert top["bm25_score"] is not None and top["dense_score"] is not None
    assert top["citation"] == "src/util.py::striptags::L10-20"


def test_fallback_dense_only_without_fts(data_dir, monkeypatch):
    _seed()
    db.drop_fts(PID)  # проект «до Этапа 2a» — FTS нет
    _mock_dense(monkeypatch, [(0, 0.70), (1, 0.40)])
    result = hybrid.hybrid_search(PID, "striptags", k=8)
    assert len(result) == 2
    assert all(h["bm25_score"] is None for h in result)  # лексического канала нет
    assert result[0]["dense_score"] == 0.70


def test_abstain_when_dense_weak_and_no_lexical():
    hits = [{"dense_score": 0.20, "bm25_score": None}]
    abstain, reason = hybrid.should_abstain(hits)
    assert abstain is True and reason


def test_no_abstain_when_confident_lexical_hit():
    # сильный bm25 (точный идентификатор) спасает ответ даже при слабом dense
    hits = [{"dense_score": 0.20, "bm25_score": -8.0}]
    assert hybrid.should_abstain(hits)[0] is False


def test_abstain_when_lexical_hit_is_weak():
    # слабый bm25 на общем слове (напр. «state») НЕ спасает off-topic-запрос
    hits = [{"dense_score": 0.30, "bm25_score": -2.7}]
    assert hybrid.should_abstain(hits)[0] is True


def test_no_abstain_when_dense_strong():
    hits = [{"dense_score": 0.70, "bm25_score": None}]
    assert hybrid.should_abstain(hits)[0] is False


def test_abstain_on_empty():
    assert hybrid.should_abstain([])[0] is True
