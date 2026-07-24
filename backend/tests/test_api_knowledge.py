"""Тесты роутера базы знаний: гейты 404/409, кэш по head_sha, in-flight guard, error-cache
self-heal, /read, /concepts.

Полная асинхронная генерация (LLM + каскад дедупа) тестируется отдельно, напрямую через
`generator.generate()` (см. test_knowledge.py, `asyncio.run`) — реальный event loop TestClient
не гарантированно докручивает fire-and-forget `asyncio.create_task` между запросами, поэтому
здесь фокус на решениях самого роутера. Фоновую задачу подменяем прокси-объектом (паттерн
`patch("app.api.projects.pipeline.schedule")` из test_project_management.py, адаптированный
под `asyncio.create_task`, вызываемый инлайн) — детерминированно, без сети и без гонок.
"""
import asyncio as real_asyncio
import json

import pytest
from starlette.testclient import TestClient

from app import db
from app.api import knowledge as knowledge_api
from app.main import create_app

PID = "abc123def456"


@pytest.fixture(autouse=True)
def _reset_gen_state():
    """`_gen_in_flight`/`_gen_errors` — module-level state, общий на процесс тестов."""
    knowledge_api._gen_in_flight.clear()
    knowledge_api._gen_errors.clear()
    yield
    knowledge_api._gen_in_flight.clear()
    knowledge_api._gen_errors.clear()


class _DummyTask:
    def add_done_callback(self, cb):
        pass


class _FakeAsyncioModule:
    """Прокси над реальным `asyncio`: перехватывает только `create_task` (не запускаем реальную
    фоновую генерацию в этих тестах), остальное (`to_thread` и т.п.) делегирует настоящему модулю."""

    def __init__(self, real, sink: list):
        self._real = real
        self._sink = sink

    def create_task(self, coro):
        self._sink.append(True)
        coro.close()  # не исполняем — иначе реальный generator.generate() пойдёт в сеть/Ollama
        return _DummyTask()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _client():
    return TestClient(create_app())


def _project_ready(head_sha: str = "sha1") -> None:
    db.create_project(PID, "u", "n", db.STATUS_READY)
    db.set_head_sha(PID, head_sha)


def _patch_create_task(monkeypatch) -> list:
    scheduled: list = []
    monkeypatch.setattr(knowledge_api, "asyncio", _FakeAsyncioModule(real_asyncio, scheduled))
    return scheduled


# --- GET .../summary: гейты ---


def test_summary_unknown_project_404(data_dir):
    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 404


def test_summary_not_ready_409(data_dir):
    db.create_project(PID, "u", "n", db.STATUS_INDEXING)
    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 409


# --- GET .../summary: кэш по head_sha ---


def test_summary_cache_hit_returns_ready(data_dir):
    _project_ready(head_sha="sha1")
    cid = db.insert_concept("x", "X", "technology", "d", None, PID)
    db.link_project_concept(PID, cid, "detail", json.dumps([]))
    db.save_summary(PID, "sha1", "overview", json.dumps(["X"]))

    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ready"
    assert body["overview"] == "overview"
    assert body["tech"] == ["X"]
    assert body["concepts"]["new"][0]["name"] == "X"


def test_summary_empty_head_sha_matches_cache_not_stale(data_dir, monkeypatch):
    """Fix D: `row["head_sha"]` может быть None/"" (проект ещё не получил head_sha) — сравнение
    должно коэрсировать None->"" на ОБЕИХ сторонах (симметрично с generator.generate), иначе
    кэш никогда бы не совпадал и генерация запускалась на каждый поллинг."""
    db.create_project(PID, "u", "n", db.STATUS_READY)  # head_sha остаётся NULL/""
    db.save_summary(PID, "", "overview", "[]")
    scheduled = _patch_create_task(monkeypatch)

    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert scheduled == []  # кэш совпал — генерация НЕ ставится в фон


def test_summary_stale_head_sha_triggers_generation(data_dir, monkeypatch):
    """head_sha разошёлся (проект переиндексирован после reindex/rebuild) -> старая выжимка не
    годится, роутер ставит новую генерацию в фон."""
    _project_ready(head_sha="sha2")
    db.save_summary(PID, "sha1", "устаревшая выжимка", "[]")
    scheduled = _patch_create_task(monkeypatch)

    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 200
    assert r.json() == {"status": "generating"}
    assert scheduled == [True]
    assert PID in knowledge_api._gen_in_flight


def test_summary_no_summary_yet_triggers_generation(data_dir, monkeypatch):
    _project_ready()
    scheduled = _patch_create_task(monkeypatch)

    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.json() == {"status": "generating"}
    assert scheduled == [True]


def test_summary_in_flight_does_not_schedule_second_task(data_dir, monkeypatch):
    _project_ready()
    knowledge_api._gen_in_flight.add(PID)
    scheduled = _patch_create_task(monkeypatch)

    r = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r.status_code == 200
    assert r.json() == {"status": "generating"}
    assert scheduled == []  # уже в процессе — дубль-задачу не создаём


def test_summary_cached_error_returned_once_then_self_heals(data_dir, monkeypatch):
    _project_ready()
    knowledge_api._gen_errors[PID] = "invalid_json"
    scheduled = _patch_create_task(monkeypatch)

    r1 = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r1.json() == {"status": "error", "reason": "invalid_json"}
    assert PID not in knowledge_api._gen_errors
    assert scheduled == []  # эта попытка не сразу триггерит новую генерацию

    r2 = _client().get(f"/api/knowledge/projects/{PID}/summary")
    assert r2.json() == {"status": "generating"}  # следующее открытие панели — self-heal ретрай
    assert scheduled == [True]


# --- _run_generate: результат generator.generate() -> состояние _gen_errors/_gen_in_flight ---


def test_run_generate_success_clears_in_flight_and_error(data_dir, monkeypatch):
    _project_ready()
    knowledge_api._gen_in_flight.add(PID)
    knowledge_api._gen_errors[PID] = "stale"

    async def fake_generate(pid):
        return {"ok": True}

    monkeypatch.setattr(knowledge_api.generator, "generate", fake_generate)
    real_asyncio.run(knowledge_api._run_generate(PID))

    assert PID not in knowledge_api._gen_in_flight
    assert PID not in knowledge_api._gen_errors


def test_run_generate_domain_failure_sets_error(data_dir, monkeypatch):
    _project_ready()
    knowledge_api._gen_in_flight.add(PID)

    async def fake_generate(pid):
        return {"ok": False, "reason": "invalid_json"}

    monkeypatch.setattr(knowledge_api.generator, "generate", fake_generate)
    real_asyncio.run(knowledge_api._run_generate(PID))

    assert PID not in knowledge_api._gen_in_flight
    assert knowledge_api._gen_errors[PID] == "invalid_json"


def test_run_generate_unexpected_exception_sets_internal_error_without_leaking(data_dir, monkeypatch):
    _project_ready()
    knowledge_api._gen_in_flight.add(PID)

    async def fake_generate(pid):
        raise RuntimeError("boom с секретом sk-topsecret")

    monkeypatch.setattr(knowledge_api.generator, "generate", fake_generate)
    real_asyncio.run(knowledge_api._run_generate(PID))

    assert knowledge_api._gen_errors[PID] == "internal_error"  # без repr исключения


# --- POST .../read ---


def test_read_unknown_project_404(data_dir):
    r = _client().post(f"/api/knowledge/projects/{PID}/read")
    assert r.status_code == 404


def test_read_marks_concepts_known_and_is_idempotent(data_dir):
    _project_ready()
    cid = db.insert_concept("x", "X", "technology", "d", None, PID)
    db.link_project_concept(PID, cid, "detail", None)

    r1 = _client().post(f"/api/knowledge/projects/{PID}/read")
    assert r1.status_code == 200
    assert r1.json() == {"ok": True}
    assert db.get_concept_by_slug("x")["known"] == 1

    r2 = _client().post(f"/api/knowledge/projects/{PID}/read")
    assert r2.status_code == 200
    assert r2.json() == {"ok": True}


# --- GET /concepts ---


def test_list_concepts_returns_only_known(data_dir):
    a_id = db.insert_concept("a", "A", "technology", "d", None, "p1")
    db.insert_concept("b", "B", "technology", "d", None, "p1")
    db.link_project_concept("p1", a_id, "detail", None)
    db.mark_concepts_known("p1")  # "b" не привязан к project_concepts -> не помечается

    r = _client().get("/api/knowledge/concepts")
    assert r.status_code == 200
    names = {c["name"] for c in r.json()}
    assert names == {"A"}
