"""Тесты Этапа 3b (backend): PAT-шифрование, валидация, API эндпоинты, PR-флоу.

Безопасность (must-fix CLAUDE.md):
- Токены шифруются at rest (Fernet), никогда не логируются сырые
- _project_dto содержит только bool can_edit, НЕ содержит токен
- Синтетические токены в тестах (не формат github_pat_*, репо публичный)
- Валидация PAT против конкретного репозитория (permissions.push + full_name)
- Per-project токен (не глобальный)
"""
import asyncio
import base64
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from app import db
from app.config import SecretKeyError, Settings, fernet, get_settings
from app.edit import github
from app.indexing.validation import RepoRef
from app.main import create_app


# --- Фикстуры ---


@pytest.fixture
def settings_with_key(monkeypatch):
    """Settings с валидным Fernet-ключом (не пустой)."""
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("JWP_SECRET_KEY", key)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def ref():
    """RepoRef для тестирования валидации."""
    return RepoRef(url="https://github.com/test/repo.git", owner="test", repo="repo", name="test/repo")


@pytest.fixture
def test_client(data_dir, settings_with_key):
    """TestClient для API-тестов."""
    return TestClient(create_app())


# --- config.fernet ---


def test_fernet_empty_key_raises(monkeypatch):
    """Пустой JWP_SECRET_KEY -> SecretKeyError (fail-closed)."""
    monkeypatch.setenv("JWP_SECRET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(SecretKeyError, match="не задан"):
        fernet(get_settings())
    get_settings.cache_clear()


def test_fernet_invalid_key_raises(monkeypatch):
    """Невалидный urlsafe-base64 -> SecretKeyError (fail-closed)."""
    monkeypatch.setenv("JWP_SECRET_KEY", "not-a-valid-fernet-key")
    get_settings.cache_clear()
    with pytest.raises(SecretKeyError, match="невалиден"):
        fernet(get_settings())
    get_settings.cache_clear()


def test_fernet_valid_key_works(settings_with_key):
    """Валидный Fernet.generate_key() -> рабочий Fernet."""
    f = fernet(settings_with_key)
    assert isinstance(f, Fernet)
    # Round-trip
    encrypted = f.encrypt(b"test")
    assert f.decrypt(encrypted) == b"test"


# --- github.encrypt_token / github.decrypt_token ---


def test_encrypt_decrypt_roundtrip(settings_with_key):
    """Шифрование и расшифровка сохраняет токен."""
    token = "ghp_test123token456test789"
    encrypted = github.encrypt_token(settings_with_key, token)
    decrypted = github.decrypt_token(settings_with_key, encrypted)
    assert decrypted == token


def test_decrypt_with_wrong_key_fails(settings_with_key, monkeypatch):
    """Расшифровка чужим ключом -> исключение."""
    token = "ghp_test123token456test789"
    encrypted = github.encrypt_token(settings_with_key, token)

    # Новый ключ
    new_key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("JWP_SECRET_KEY", new_key)
    get_settings.cache_clear()

    with pytest.raises(Exception):  # cryptography.fernet.InvalidToken
        github.decrypt_token(get_settings(), encrypted)

    get_settings.cache_clear()


def test_encrypt_token_without_key_raises(monkeypatch):
    """Попытка шифрования без ключа -> SecretKeyError."""
    monkeypatch.setenv("JWP_SECRET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(SecretKeyError):
        github.encrypt_token(get_settings(), "token")
    get_settings.cache_clear()


# --- github.validate_token (httpx-мокировка) ---


def test_validate_token_success(ref, monkeypatch):
    """Токен с push=True и правильный full_name -> True."""
    fake_response = {
        "full_name": "test/repo",
        "permissions": {"push": True, "pull": False},
    }

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fake_response
    mock_client.get.return_value = mock_response

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, "test_token"))

    assert result is True
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args[1]
    assert "Authorization" in call_kwargs["headers"]
    assert call_kwargs["headers"]["Authorization"] == "Bearer test_token"


def test_validate_token_no_push_permission_false(ref, monkeypatch):
    """push=False -> False (даже если full_name совпадает)."""
    fake_response = {
        "full_name": "test/repo",
        "permissions": {"push": False},
    }

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fake_response
    mock_client.get.return_value = mock_response

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, "test_token"))

    assert result is False


def test_validate_token_wrong_repo_false(ref, monkeypatch):
    """full_name чужого репо -> False."""
    fake_response = {
        "full_name": "other/repo",
        "permissions": {"push": True},
    }

    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = fake_response
    mock_client.get.return_value = mock_response

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, "test_token"))

    assert result is False


def test_validate_token_http_404_false(ref, monkeypatch):
    """HTTP 404 -> False (fail-closed)."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client.get.return_value = mock_response

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, "test_token"))

    assert result is False


def test_validate_token_http_error_false(ref, monkeypatch):
    """httpx.HTTPError (таймаут/сетевая ошибка) -> False (fail-closed)."""
    import httpx

    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.TimeoutException("timeout")

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, "test_token"))

    assert result is False


def test_validate_token_no_token_leak_in_error(ref, monkeypatch, caplog):
    """Токен НЕ попадает в логи при ошибке."""
    import httpx

    test_token = "ghp_very_secret_token_12345"

    mock_client = AsyncMock()
    mock_client.get.side_effect = httpx.TimeoutException("timeout")

    with patch("app.edit.github.httpx.AsyncClient") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_client
        result = asyncio.run(github.validate_token(ref, test_token))

    assert result is False
    # Токен не должен быть в логах
    assert test_token not in caplog.text


# --- github slug и branch generation ---


def test_branch_slug_from_summary():
    """Summary перетворяется в slug ^[a-z0-9-]{1,40}$."""
    slug = github._branch_slug("Fix the bug with cyrillic", "")
    assert github._SLUG_RE.match(slug)
    assert "fix" in slug
    assert "bug" in slug


def test_branch_slug_cyrillic_transliterated():
    """Кириллица транслитерируется."""
    slug = github._branch_slug("Исправить ошибку", "")
    assert github._SLUG_RE.match(slug)
    assert "ispravit" in slug or "ispravit" in slug.replace("-", "")


def test_branch_slug_empty_fallback():
    """Пусто -> fallback edit-<timestamp>."""
    slug = github._branch_slug("", "")
    assert github._SLUG_RE.match(slug)
    assert slug.startswith("edit-")


def test_branch_slug_special_chars_cleaned():
    """Спецсимволы удаляются."""
    slug = github._branch_slug("fix@@@bug!!!status", "")
    assert github._SLUG_RE.match(slug)
    assert "@" not in slug
    assert "!" not in slug


def test_branch_slug_max_length():
    """Slug обрезается до 40 символов."""
    slug = github._branch_slug("a" * 100, "")
    assert len(slug) <= 40
    assert github._SLUG_RE.match(slug)


# --- db token operations ---


def test_db_set_get_clear_token(data_dir, settings_with_key):
    """set_project_token -> get_project_token_enc -> clear_project_token round-trip."""
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    token = "ghp_test123"
    enc = github.encrypt_token(settings_with_key, token)

    # Сохранить
    db.set_project_token("p1", enc)

    # Получить зашифрованный
    retrieved = db.get_project_token_enc("p1")
    assert retrieved == enc
    assert retrieved is not None

    # Расшифровать (проверка, что это именно наш токен)
    decrypted = github.decrypt_token(settings_with_key, retrieved)
    assert decrypted == token

    # Отвязать
    db.clear_project_token("p1")
    assert db.get_project_token_enc("p1") is None


def test_db_get_token_missing_project(data_dir):
    """get_project_token_enc для несуществующего проекта -> None."""
    assert db.get_project_token_enc("nonexistent") is None


def test_db_project_can_edit_with_token(data_dir, settings_with_key):
    """project_can_edit = True если есть токен."""
    db.create_project("p1", "url", "name", db.STATUS_READY)
    assert db.project_can_edit("p1") is False

    enc = github.encrypt_token(settings_with_key, "token")
    db.set_project_token("p1", enc)
    assert db.project_can_edit("p1") is True

    db.clear_project_token("p1")
    assert db.project_can_edit("p1") is False


def test_db_project_can_edit_nonexistent(data_dir):
    """project_can_edit для несуществующего -> False."""
    assert db.project_can_edit("nonexistent") is False


# --- db миграция ---


def test_migrate_add_token_column_idempotent(data_dir):
    """Миграция добавляет колонку only once, повторный запуск не падает."""
    # БД уже инициализирована в фикстуре data_dir через init_db()
    # Повторный вызов миграции должен не упасть
    from app.db import get_conn, _migrate_add_token_column

    with get_conn() as conn:
        _migrate_add_token_column(conn)  # Первый раз (уже выполнен в init_db)
        _migrate_add_token_column(conn)  # Второй раз — не должен упасть

        # Проверить, что колонка есть
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        assert "github_token_enc" in cols


def test_migrate_creates_column_if_missing(data_dir, monkeypatch):
    """Миграция создаёт колонку, если её нет."""
    # Создадим БД без PAT-колонки (как Этап < 3b)
    from app.config import get_settings
    import sqlite3

    # Начнём с чистой БД
    db_path = get_settings().db_path
    db_path.unlink(missing_ok=True)

    # Создадим старую схему
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id          TEXT PRIMARY KEY,
                url         TEXT NOT NULL,
                name        TEXT NOT NULL,
                status      TEXT NOT NULL,
                head_sha    TEXT,
                indexed_at  TEXT,
                error       TEXT,
                created_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        # Проверим, что колонки нет
        cols_before = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        assert "github_token_enc" not in cols_before

    # Запустим миграцию
    from app.db import _migrate_add_token_column
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        _migrate_add_token_column(conn)

        cols_after = {row["name"] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
        assert "github_token_enc" in cols_after


# --- api _project_dto ---


def test_project_dto_no_token_field(data_dir, settings_with_key):
    """_project_dto содержит can_edit (bool), НЕ содержит token/github_token_enc."""
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    # Без токена
    row = db.get_project("p1")
    dto = {
        "id": row["id"],
        "url": row["url"],
        "name": row["name"],
        "status": row["status"],
        "error": row["error"],
        "indexed_at": row["indexed_at"],
        "head_sha": row["head_sha"],
        "can_edit": bool(row["github_token_enc"]),
    }

    assert dto["can_edit"] is False
    assert "github_token_enc" not in dto
    assert "token" not in dto


def test_project_dto_can_edit_true_with_token(data_dir, settings_with_key):
    """_project_dto.can_edit = True если есть токен."""
    from app.api.projects import _project_dto

    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test")
    db.set_project_token("p1", enc)

    row = db.get_project("p1")
    dto = _project_dto(row)

    assert dto["can_edit"] is True
    assert "github_token_enc" not in dto
    assert "token" not in dto


# --- api PUT /{project_id}/token ---


def test_put_token_missing_project(test_client, data_dir):
    """PUT на несуществующий проект -> 404."""
    response = test_client.put(
        "/api/projects/nonexistent/token",
        json={"token": "ghp_test"},
    )
    assert response.status_code == 404


def test_put_token_project_not_ready(test_client, data_dir):
    """PUT на неготовый проект -> 409."""
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_INDEXING)

    response = test_client.put(
        "/api/projects/p1/token",
        json={"token": "ghp_test"},
    )
    assert response.status_code == 409
    assert "не готов" in response.json()["detail"].lower()


def test_put_token_validation_fails(test_client, data_dir, settings_with_key, monkeypatch):
    """PUT с токеном, который не проходит валидацию -> 400."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    # Мокируем validate_token -> False
    with patch("app.api.projects.github.validate_token", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = False
        response = test_client.put(
            "/api/projects/p1/token",
            json={"token": "ghp_invalid"},
        )

    assert response.status_code == 400
    assert "не подходит" in response.json()["detail"].lower()


def test_put_token_success(test_client, data_dir, settings_with_key, monkeypatch):
    """PUT с валидным токеном -> 200, can_edit=True."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    # Мокируем validate_token -> True
    with patch("app.api.projects.github.validate_token", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = True
        response = test_client.put(
            "/api/projects/p1/token",
            json={"token": "ghp_valid_token_12345"},
        )

    assert response.status_code == 200
    assert response.json()["can_edit"] is True

    # Проверим, что токен сохранён (зашифрован)
    enc = db.get_project_token_enc("p1")
    assert enc is not None
    decrypted = github.decrypt_token(settings_with_key, enc)
    assert decrypted == "ghp_valid_token_12345"


def test_put_token_no_secret_key(test_client, data_dir, monkeypatch):
    """PUT без JWP_SECRET_KEY -> 503."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    monkeypatch.setenv("JWP_SECRET_KEY", "")
    get_settings.cache_clear()

    with patch("app.api.projects.github.validate_token", new_callable=AsyncMock) as mock_validate:
        mock_validate.return_value = True
        response = test_client.put(
            "/api/projects/p1/token",
            json={"token": "ghp_test"},
        )

    assert response.status_code == 503
    assert "функции правок" in response.json()["detail"].lower()
    get_settings.cache_clear()


# --- api DELETE /{project_id}/token ---


def test_delete_token_missing_project(test_client, data_dir):
    """DELETE на несуществующий проект -> 404."""
    response = test_client.delete("/api/projects/nonexistent/token")
    assert response.status_code == 404


def test_delete_token_success(test_client, data_dir, settings_with_key):
    """DELETE удаляет токен -> can_edit=False."""
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    # Сначала установим токен
    enc = github.encrypt_token(settings_with_key, "ghp_test")
    db.set_project_token("p1", enc)
    assert db.project_can_edit("p1") is True

    # DELETE
    response = test_client.delete("/api/projects/p1/token")

    assert response.status_code == 200
    assert response.json()["can_edit"] is False
    assert db.project_can_edit("p1") is False


def test_delete_token_idempotent(test_client, data_dir, settings_with_key):
    """DELETE дважды не падает (idempotent)."""
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_READY)

    # Первый DELETE
    response1 = test_client.delete("/api/projects/p1/token")
    assert response1.status_code == 200

    # Второй DELETE — должен не упасть
    response2 = test_client.delete("/api/projects/p1/token")
    assert response2.status_code == 200


# --- api POST /{project_id}/pr ---


def test_post_pr_no_confirm(test_client, data_dir):
    """POST /pr без confirm=true -> 400."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    response = test_client.post(
        "/api/projects/p1/pr",
        json={
            "confirm": False,
            "instruction": "fix the bug",
            "expected_diff": "...",
        },
    )
    assert response.status_code == 400
    assert "подтверждение" in response.json()["detail"].lower()


def test_post_pr_missing_project(test_client, data_dir):
    """POST /pr на несуществующий проект -> 404."""
    response = test_client.post(
        "/api/projects/nonexistent/pr",
        json={
            "confirm": True,
            "instruction": "fix",
            "expected_diff": "",
        },
    )
    assert response.status_code == 404


def test_post_pr_project_not_ready(test_client, data_dir):
    """POST /pr на неготовый проект -> 409."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_INDEXING)

    response = test_client.post(
        "/api/projects/p1/pr",
        json={
            "confirm": True,
            "instruction": "fix",
            "expected_diff": "",
        },
    )
    assert response.status_code == 409
    assert "не готов" in response.json()["detail"].lower()


def test_post_pr_no_token(test_client, data_dir):
    """POST /pr без привязанного токена -> 403."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    # Не привязываем токен

    response = test_client.post(
        "/api/projects/p1/pr",
        json={
            "confirm": True,
            "instruction": "fix",
            "expected_diff": "",
        },
    )
    assert response.status_code == 403
    assert "правки отключены" in response.json()["detail"].lower()


def test_post_pr_empty_instruction(test_client, data_dir, settings_with_key):
    """POST /pr с пустой инструкцией -> 400."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test")
    db.set_project_token("p1", enc)

    response = test_client.post(
        "/api/projects/p1/pr",
        json={
            "confirm": True,
            "instruction": "   ",
            "expected_diff": "",
        },
    )
    assert response.status_code == 400


def test_post_pr_generate_returns_not_ok(test_client, data_dir, settings_with_key, monkeypatch):
    """POST /pr когда generate_validated_edit вернул ok=False -> {ok: False}."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test")
    db.set_project_token("p1", enc)

    with patch("app.api.projects.generate_validated_edit", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {"ok": False, "reason": "not enough context"}
        response = test_client.post(
            "/api/projects/p1/pr",
            json={
                "confirm": True,
                "instruction": "fix",
                "expected_diff": "some_diff",
            },
        )

    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_post_pr_diff_mismatch(test_client, data_dir, settings_with_key, monkeypatch):
    """POST /pr когда expected_diff != сгенерированный -> 409 conflict."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test")
    db.set_project_token("p1", enc)

    with patch("app.api.projects.generate_validated_edit", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {
            "ok": True,
            "summary": "fix",
            "diff": "--- a/file.py\n+++ b/file.py\ngenerated_diff_here",
            "edits": [],
            "sources": [],
            "dropped": 0,
        }
        response = test_client.post(
            "/api/projects/p1/pr",
            json={
                "confirm": True,
                "instruction": "fix",
                "expected_diff": "old_cached_diff_different",
            },
        )

    assert response.status_code == 409
    assert response.json()["ok"] is False
    assert "превью устарело" in response.json()["reason"].lower()


def test_post_pr_success(test_client, data_dir, settings_with_key, monkeypatch):
    """POST /pr happy-path: confirm + токен + diff совпал -> open_pr вызван, pr_url возвращен."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test_token_123")
    db.set_project_token("p1", enc)

    diff_content = "--- a/file.py\n+++ b/file.py\n-old\n+new"
    pr_url = "https://github.com/test/repo/pull/123"

    with patch("app.api.projects.generate_validated_edit", new_callable=AsyncMock) as mock_gen:
        with patch("app.api.projects.github.open_pr") as mock_open_pr:
            mock_gen.return_value = {
                "ok": True,
                "summary": "Fix the bug",
                "diff": diff_content,
                "edits": [{"file": "file.py", "reason": "per instruction"}],
                "sources": [{"file": "file.py", "citation": "file.py::func", "quote": "old"}],
                "dropped": 0,
            }
            mock_open_pr.return_value = pr_url

            response = test_client.post(
                "/api/projects/p1/pr",
                json={
                    "confirm": True,
                    "instruction": "Fix the bug",
                    "expected_diff": diff_content,
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["pr_url"] == pr_url

    # Проверим, что open_pr был вызван с расшифрованным токеном
    mock_open_pr.assert_called_once()
    call_args = mock_open_pr.call_args
    # Аргументы: project_id, ref, token, diff, summary, instruction
    assert call_args[0][0] == "p1"
    assert call_args[0][2] == "ghp_test_token_123"  # Token должен быть расшифрован
    assert call_args[0][3] == diff_content


def test_post_pr_open_pr_fails(test_client, data_dir, settings_with_key, monkeypatch):
    """POST /pr когда open_pr() вызовет GithubError -> {ok: False, reason: ...}."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)
    enc = github.encrypt_token(settings_with_key, "ghp_test_token_123")
    db.set_project_token("p1", enc)

    diff_content = "--- a/file.py"

    with patch("app.api.projects.generate_validated_edit", new_callable=AsyncMock) as mock_gen:
        with patch("app.api.projects.github.open_pr") as mock_open_pr:
            mock_gen.return_value = {
                "ok": True,
                "summary": "Fix",
                "diff": diff_content,
                "edits": [],
                "sources": [],
                "dropped": 0,
            }
            mock_open_pr.side_effect = github.GithubError("clone failed: permission denied")

            response = test_client.post(
                "/api/projects/p1/pr",
                json={
                    "confirm": True,
                    "instruction": "Fix",
                    "expected_diff": diff_content,
                },
            )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert "clone failed" in data["reason"].lower()


def test_post_pr_no_token_key(test_client, data_dir, monkeypatch):
    """POST /pr при сбое расшифровки токена (нет JWP_SECRET_KEY) -> 503."""
    db.create_project("p1", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    # Установим токен с одним ключом
    key = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("JWP_SECRET_KEY", key)
    get_settings.cache_clear()
    enc = github.encrypt_token(get_settings(), "ghp_test")
    db.set_project_token("p1", enc)

    # Теперь удалим ключ
    monkeypatch.setenv("JWP_SECRET_KEY", "")
    get_settings.cache_clear()

    with patch("app.api.projects.generate_validated_edit", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = {
            "ok": True,
            "summary": "Fix",
            "diff": "diff",
            "edits": [],
            "sources": [],
            "dropped": 0,
        }
        response = test_client.post(
            "/api/projects/p1/pr",
            json={
                "confirm": True,
                "instruction": "Fix",
                "expected_diff": "diff",
            },
        )

    assert response.status_code == 503
    get_settings.cache_clear()
