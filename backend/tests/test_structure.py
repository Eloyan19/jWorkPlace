"""Тесты структуры проекта (Задание 1): db.project_tree + GET /api/projects/{id}/structure.

Дерево строится из БД (files + chunks), без обхода клона и без LLM. Проверяем: группировку
символов по файлам, отсев бинарных/vendored, флаг excluded, гейты статуса эндпоинта.
"""
from starlette.testclient import TestClient

from app import db
from app.main import create_app


def _seed_ready_project(pid: str = "p1") -> None:
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_INDEXING)
    db.replace_files(pid, [
        {"path": "a.py", "blob_sha": "s1", "lang": "python", "size": 40,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
        {"path": ".env", "blob_sha": "s2", "lang": None, "size": 5,
         "is_binary": 0, "is_vendored": 0, "excluded": 1},
        {"path": "img.png", "blob_sha": "s3", "lang": None, "size": 99,
         "is_binary": 1, "is_vendored": 0, "excluded": 0},
        {"path": "vendor/lib.js", "blob_sha": "s4", "lang": "javascript", "size": 20,
         "is_binary": 0, "is_vendored": 1, "excluded": 0},
    ])
    db.insert_chunks([
        {"project_id": pid, "file": "a.py", "lang": "python", "symbol": "foo",
         "symbol_kind": "function_definition", "start_line": 1, "end_line": 3,
         "blob_sha": "s1", "text": "def foo(): ..."},
        {"project_id": pid, "file": "a.py", "lang": "python", "symbol": "Bar",
         "symbol_kind": "class_definition", "start_line": 5, "end_line": 9,
         "blob_sha": "s1", "text": "class Bar: ..."},
    ])
    db.mark_ready(pid)


def test_project_tree_groups_symbols_and_filters(data_dir):
    _seed_ready_project()
    tree = db.project_tree("p1")

    paths = [f["path"] for f in tree]
    # Бинарные и vendored отсеяны; excluded (.env) остаётся в дереве.
    assert paths == [".env", "a.py"]

    a = next(f for f in tree if f["path"] == "a.py")
    assert [s["symbol"] for s in a["symbols"]] == ["foo", "Bar"]   # порядок по start_line
    assert a["symbols"][0]["kind"] == "function_definition"
    assert a["symbols"][0]["start_line"] == 1

    env = next(f for f in tree if f["path"] == ".env")
    assert env["excluded"] is True
    assert env["symbols"] == []


def test_structure_endpoint_ok(data_dir):
    _seed_ready_project()
    client = TestClient(create_app())
    res = client.get("/api/projects/p1/structure")

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "o/r"
    assert body["file_count"] == 2
    assert body["symbol_count"] == 2
    assert {f["path"] for f in body["files"]} == {".env", "a.py"}


def test_structure_endpoint_missing_project(data_dir):
    client = TestClient(create_app())
    assert client.get("/api/projects/nope/structure").status_code == 404


def test_structure_endpoint_not_ready(data_dir):
    db.create_project("p2", "u", "n", db.STATUS_INDEXING)
    client = TestClient(create_app())
    assert client.get("/api/projects/p2/structure").status_code == 409
