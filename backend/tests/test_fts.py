"""Тесты лексического слоя БД (FTS5): create/insert/search, fts_exists, добор чанков."""
from app import db
from app.indexing import lexical

PID = "abc123def456"  # hex — как реальный uuid4().hex[:12]


def _seed(project_id: str):
    db.create_project(project_id, "u", "n", db.STATUS_INDEXING)
    ids = db.insert_chunks([
        {"project_id": project_id, "file": "src/__init__.py", "lang": "python", "symbol": "escape",
         "symbol_kind": "function_definition", "start_line": 1, "end_line": 5,
         "blob_sha": "s1", "text": "def escape(s): return markup"},
        {"project_id": project_id, "file": "src/util.py", "lang": "python", "symbol": "striptags",
         "symbol_kind": "function_definition", "start_line": 10, "end_line": 20,
         "blob_sha": "s2", "text": "def striptags(s): return stripped"},
    ])
    db.set_faiss_ids([(0, ids[0]), (1, ids[1])])
    db.create_fts(project_id)
    db.fts_insert(project_id, [
        (ids[i], lexical.code_tokenize(r["text"]),
         lexical.code_tokenize(r["symbol"]), lexical.code_tokenize(r["file"]))
        for i, r in enumerate([
            {"text": "def escape(s): return markup", "symbol": "escape", "file": "src/__init__.py"},
            {"text": "def striptags(s): return stripped", "symbol": "striptags", "file": "src/util.py"},
        ])
    ])
    return ids


def test_fts_exists_lifecycle(data_dir):
    assert db.fts_exists(PID) is False
    db.create_fts(PID)
    assert db.fts_exists(PID) is True
    db.drop_fts(PID)
    assert db.fts_exists(PID) is False


def test_fts_search_matches_exact_identifier(data_dir):
    ids = _seed(PID)
    mq = lexical.build_match_query("striptags")
    hits = db.fts_search(PID, mq, limit=10)
    assert hits, "точный идентификатор должен находиться лексически"
    assert hits[0][0] == ids[1]  # чанк striptags — первый


def test_chunks_by_ids_and_faiss_ids(data_dir):
    ids = _seed(PID)
    by_id = db.chunks_by_ids(PID, [ids[1]])
    assert by_id[ids[1]]["symbol"] == "striptags"
    by_faiss = db.chunks_by_faiss_ids(PID, [0])
    assert by_faiss[0]["symbol"] == "escape"
    assert db.chunks_by_ids(PID, []) == {}


def test_invalid_project_id_rejected(data_dir):
    import pytest
    with pytest.raises(ValueError):
        db.create_fts("p1;DROP TABLE chunks")
