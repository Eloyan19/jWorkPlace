"""Тесты эндпоинта POST /api/search: коды ошибок + happy-path (hybrid_search замокан)."""
from starlette.testclient import TestClient

from app import db
from app.api import search as search_api
from app.main import create_app

PID = "abc123def456"


def _client():
    return TestClient(create_app())


def test_empty_query_400(data_dir):
    r = _client().post("/api/search", json={"project_id": PID, "query": "   "})
    assert r.status_code == 400


def test_unknown_project_404(data_dir):
    r = _client().post("/api/search", json={"project_id": PID, "query": "escape"})
    assert r.status_code == 404


def test_project_not_ready_409(data_dir):
    db.create_project(PID, "u", "n", db.STATUS_INDEXING)
    r = _client().post("/api/search", json={"project_id": PID, "query": "escape"})
    assert r.status_code == 409


def test_happy_path_returns_hits(data_dir, monkeypatch):
    db.create_project(PID, "u", "n", db.STATUS_READY)
    fake_hit = {
        "file": "src/util.py", "symbol": "striptags", "symbol_kind": "function_definition",
        "lang": "python", "start_line": 10, "end_line": 20,
        "citation": "src/util.py::striptags::L10-20",
        "dense_score": 0.7, "bm25_score": -8.0, "rrf_score": 0.032,
        "text": "def striptags(s): ...",
    }
    monkeypatch.setattr(search_api.hybrid, "hybrid_search", lambda pid, q, k: [fake_hit])
    r = _client().post("/api/search", json={"project_id": PID, "query": "striptags"})
    assert r.status_code == 200
    body = r.json()
    assert body["abstain"] is False
    assert body["hits"][0]["citation"] == "src/util.py::striptags::L10-20"
    assert body["hits"][0]["dense_score"] == 0.7


def test_abstain_hides_hits(data_dir, monkeypatch):
    db.create_project(PID, "u", "n", db.STATUS_READY)
    monkeypatch.setattr(search_api.hybrid, "hybrid_search", lambda pid, q, k: [])
    r = _client().post("/api/search", json={"project_id": PID, "query": "off-topic gibberish"})
    assert r.status_code == 200
    body = r.json()
    assert body["abstain"] is True
    assert body["hits"] == []
