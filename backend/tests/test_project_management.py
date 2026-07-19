"""Тесты управления проектами: удаление + переиндексация (Этап управления репо).

Охватывает:
- db.delete_project: очистка БД, сохранение embed_cache, изоляция проектов
- _safe_project_dir: guard от traversal/чужих путей
- DELETE /api/projects/{id}: 200/404/409, удаление каталогов и FTS
- POST /api/projects/{id}/rebuild: 200/404/409, вызов pipeline.schedule с reindex=False
"""
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app


@pytest.fixture
def test_client(data_dir):
    """TestClient для API-тестов."""
    return TestClient(create_app())


# --- db.delete_project ---


def test_delete_project_cleans_db(data_dir):
    """delete_project удаляет проект и его файлы/чанки из БД."""
    # Создать проект с файлами и чанками (project_id должен быть [0-9a-f]{1,32})
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_READY)
    db.replace_files(pid, [
        {"path": "a.py", "blob_sha": "s1", "lang": "python", "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    chunk_ids = db.insert_chunks([
        {"project_id": pid, "file": "a.py", "lang": "python", "symbol": "f",
         "symbol_kind": "function_definition", "start_line": 1, "end_line": 3,
         "blob_sha": "s1", "text": "def f(): pass"},
    ])
    assert db.chunk_count(pid) == 1

    # Удалить
    db.delete_project(pid)

    # Проверить, что проект исчез
    assert db.get_project(pid) is None

    # Проверить, что файлы исчезли
    assert len(db.indexable_files(pid)) == 0

    # Проверить, что чанки исчезли
    assert db.chunk_count(pid) == 0


def test_delete_project_preserves_embed_cache(data_dir):
    """delete_project НЕ трогает embed_cache (глобальный, между проектами)."""
    # Создать два проекта с shared эмбеддингом
    p1, p2 = "abc123def456", "def456abc123"
    db.create_project(p1, "u", "n", db.STATUS_READY)
    db.create_project(p2, "u", "n", db.STATUS_READY)

    # Вставить чанки с одинаковым blob_sha
    db.insert_chunks([
        {"project_id": p1, "file": "a.py", "lang": "python", "symbol": None,
         "symbol_kind": None, "start_line": 1, "end_line": 3,
         "blob_sha": "shared_blob", "text": "def f(): pass"},
    ])
    db.insert_chunks([
        {"project_id": p2, "file": "b.py", "lang": "python", "symbol": None,
         "symbol_kind": None, "start_line": 1, "end_line": 3,
         "blob_sha": "shared_blob", "text": "def f(): pass"},
    ])

    # Вставить в кэш
    db.cache_put("shared_blob", "hash1", b"\x00\x01\x02")

    # Удалить p1
    db.delete_project(p1)

    # Кэш остался (глобальный)
    assert db.cache_get("shared_blob", "hash1") == b"\x00\x01\x02"

    # p2 не затронут
    assert db.chunk_count(p2) == 1


def test_delete_project_isolated_from_other(data_dir):
    """delete_project НЕ влияет на другие проекты."""
    p1, p2 = "abc123def456", "def456abc123"
    db.create_project(p1, "u", "n", db.STATUS_READY)
    db.create_project(p2, "u", "n", db.STATUS_READY)

    # Добавить файлы обоим
    db.replace_files(p1, [
        {"path": "a.py", "blob_sha": "s1", "lang": "python", "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    db.replace_files(p2, [
        {"path": "b.py", "blob_sha": "s2", "lang": "python", "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])

    # Удалить p1
    db.delete_project(p1)

    # p1 исчез, p2 остался
    assert db.get_project(p1) is None
    assert db.get_project(p2) is not None
    assert len(db.indexable_files(p2)) == 1


# --- _safe_project_dir ---


def test_safe_project_dir_valid_hex(data_dir):
    """Valid hex project_id → путь внутри base."""
    from app.api.projects import _safe_project_dir

    base = data_dir / "repos"
    base.mkdir(parents=True, exist_ok=True)

    result = _safe_project_dir(base, "abc123def456")
    assert result is not None
    assert result.parent == base.resolve()


def test_safe_project_dir_relative_to_check(data_dir):
    """_safe_project_dir проверяет, что путь внутри base (resolve + relative_to)."""
    from app.api.projects import _safe_project_dir

    base = data_dir / "repos"
    base.mkdir(parents=True, exist_ok=True)

    result = _safe_project_dir(base, "abc123")
    assert result is not None
    # Путь должен быть разрешён внутри base
    assert result.is_relative_to(base.resolve())


def test_safe_project_dir_rejects_double_dot(data_dir):
    """_safe_project_dir отвергает .. → None."""
    from app.api.projects import _safe_project_dir

    base = data_dir / "repos"
    base.mkdir(parents=True, exist_ok=True)

    # Попытка traversal
    result = _safe_project_dir(base, "..")
    assert result is None


def test_safe_project_dir_rejects_invalid_hex(data_dir):
    """_safe_project_dir отвергает non-hex (большие буквы, спецсимволы) → None."""
    from app.api.projects import _safe_project_dir

    base = data_dir / "repos"

    # Заглавные буквы не в диапазоне [0-9a-f]
    assert _safe_project_dir(base, "ABC123") is None

    # Спецсимволы
    assert _safe_project_dir(base, "abc@123") is None

    # Слэш (попытка подтпапки)
    assert _safe_project_dir(base, "abc/123") is None


def test_safe_project_dir_rejects_empty(data_dir):
    """_safe_project_dir отвергает пусто → None."""
    from app.api.projects import _safe_project_dir

    base = data_dir / "repos"
    result = _safe_project_dir(base, "")
    assert result is None


# --- DELETE /api/projects/{id} ---


def test_delete_project_endpoint_missing_project(test_client, data_dir):
    """DELETE на несуществующий проект → 404."""
    response = test_client.delete("/api/projects/abc123")
    assert response.status_code == 404
    assert "не найден" in response.json()["detail"].lower()


def test_delete_project_endpoint_in_progress(test_client, data_dir):
    """DELETE на проект в процессе → 409."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_CLONING)

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 409
    assert "обрабатывается" in response.json()["detail"].lower()


def test_delete_project_endpoint_success(test_client, data_dir):
    """DELETE готового проекта → 200 {deleted: true}."""
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}

    # Проект исчез из БД
    assert db.get_project(pid) is None


def test_delete_project_endpoint_cleans_fts(test_client, data_dir):
    """DELETE удаляет FTS-таблицу проекта."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_READY)
    db.create_fts(pid)
    assert db.fts_exists(pid)

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200

    # FTS таблица удалена
    assert not db.fts_exists(pid)


def test_delete_project_endpoint_cleans_faiss(test_client, data_dir):
    """DELETE удаляет FAISS-индекс (каталог indexes/<id>/)."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_READY)

    # Создать индекс (dummy)
    settings = get_settings()
    index_dir = settings.indexes_dir / pid
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "index.faiss").write_bytes(b"dummy")
    assert index_dir.exists()

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200

    # Индекс удалён
    assert not index_dir.exists()


def test_delete_project_endpoint_cleans_repos_dir(test_client, data_dir):
    """DELETE удаляет каталог repos/<id>/."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_READY)

    settings = get_settings()
    repo_dir = settings.repos_dir / pid
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "file.txt").write_text("content")
    assert repo_dir.exists()

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200

    # repos/<id> удалён
    assert not repo_dir.exists()


def test_delete_project_endpoint_cleans_worktrees_dir(test_client, data_dir):
    """DELETE удаляет каталог worktrees/<id>/."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_READY)

    settings = get_settings()
    wt_dir = settings.worktrees_dir / pid
    wt_dir.mkdir(parents=True, exist_ok=True)
    (wt_dir / "file.txt").write_text("content")
    assert wt_dir.exists()

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200

    # worktrees/<id> удалён
    assert not wt_dir.exists()


def test_delete_project_endpoint_isolation(test_client, data_dir):
    """DELETE не трогает другие проекты (БД, индексы, каталоги)."""
    p1, p2 = "abc123def456", "def456abc123"
    db.create_project(p1, "u", "n", db.STATUS_READY)
    db.create_project(p2, "u", "n", db.STATUS_READY)

    settings = get_settings()

    # Создать структуру для обоих
    for pid in [p1, p2]:
        repo_dir = settings.repos_dir / pid
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "file.txt").write_bytes(b"data")

    # Удалить p1
    response = test_client.delete(f"/api/projects/{p1}")
    assert response.status_code == 200

    # p1 исчез, p2 остался
    assert db.get_project(p1) is None
    assert db.get_project(p2) is not None
    assert not (settings.repos_dir / p1).exists()
    assert (settings.repos_dir / p2).exists()


def test_delete_project_endpoint_idempotent_missing_dirs(test_client, data_dir):
    """DELETE не падает, если каталоги уже удалены (ignore_errors=True)."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_READY)
    # Не создаём каталоги — они не существуют

    response = test_client.delete(f"/api/projects/{pid}")
    assert response.status_code == 200  # Не падает
    assert response.json() == {"deleted": True}


# --- POST /api/projects/{id}/rebuild ---


def test_rebuild_project_endpoint_missing_project(test_client, data_dir):
    """POST /rebuild на несуществующий проект → 404."""
    response = test_client.post("/api/projects/abc123/rebuild")
    assert response.status_code == 404


def test_rebuild_project_endpoint_in_progress(test_client, data_dir):
    """POST /rebuild на проект в процессе → 409."""
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_INDEXING)

    response = test_client.post(f"/api/projects/{pid}/rebuild")
    assert response.status_code == 409
    assert "обрабатывается" in response.json()["detail"].lower()


def test_rebuild_project_endpoint_success_calls_schedule(test_client, data_dir):
    """POST /rebuild → 200 {status: cloning} + pipeline.schedule(id, url, reindex=False)."""
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    with patch("app.api.projects.pipeline.schedule") as mock_schedule:
        response = test_client.post(f"/api/projects/{pid}/rebuild")

    assert response.status_code == 200
    assert response.json() == {"status": db.STATUS_CLONING}

    # Проверить, что pipeline.schedule вызван с reindex=False
    mock_schedule.assert_called_once_with(pid, "https://github.com/o/r.git", reindex=False)

    # Статус в БД → CLONING
    row = db.get_project(pid)
    assert row["status"] == db.STATUS_CLONING


def test_rebuild_project_endpoint_vs_reindex(test_client, data_dir):
    """POST /rebuild отличается от /reindex: reindex=False vs reindex=True."""
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    # /rebuild → reindex=False
    with patch("app.api.projects.pipeline.schedule") as mock_rebuild:
        response = test_client.post(f"/api/projects/{pid}/rebuild")
    assert response.status_code == 200
    mock_rebuild.assert_called_once_with(pid, "https://github.com/o/r.git", reindex=False)

    # Сбросить статус
    db.set_status(pid, db.STATUS_READY)

    # /reindex → reindex=True
    with patch("app.api.projects.pipeline.schedule") as mock_reindex:
        response = test_client.post(f"/api/projects/{pid}/reindex")
    assert response.status_code == 200
    mock_reindex.assert_called_once_with(pid, "https://github.com/o/r.git", reindex=True)


def test_rebuild_project_endpoint_scanning_status_409(test_client, data_dir):
    """POST /rebuild на проект в scanning-статусе → 409."""
    pid = "abc123def456"
    db.create_project(pid, "u", "n", db.STATUS_SCANNING)

    response = test_client.post(f"/api/projects/{pid}/rebuild")
    assert response.status_code == 409


def test_rebuild_project_endpoint_error_status_allowed(test_client, data_dir):
    """POST /rebuild на error-проект → 200 (не in-progress)."""
    pid = "abc123def456"
    db.create_project(pid, "https://github.com/o/r.git", "o/r", db.STATUS_ERROR)

    with patch("app.api.projects.pipeline.schedule") as mock_schedule:
        response = test_client.post(f"/api/projects/{pid}/rebuild")

    assert response.status_code == 200
    mock_schedule.assert_called_once()
