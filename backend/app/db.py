"""SQLite-хранилище jWorkPlace: метаданные проектов, файлов, чанков + кэш эмбеддингов.

Один файл БД в $JWP_DATA_DIR/jworkplace.sqlite (вне git-дерева). Схема forward-совместима:
Этап 1a наполняет projects+files, 1b дозаполняет chunks (chunks.faiss_id ↔ порядок вставки
в FAISS-индекс). embed_cache глобальна (dedup эмбеддингов между проектами/форками).

Секреты чужого репо сюда не попадают: файлы с находками gitleaks/чувствительными именами
помечаются files.excluded=1 и не чанкуются (см. indexing/scan.py, chunker.py).
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from app.config import get_settings

# Статусы проекта — единый источник для backend и (через API) фронта.
STATUS_CLONING = "cloning"
STATUS_SCANNING = "scanning"
STATUS_INDEXING = "indexing"
STATUS_READY = "ready"
STATUS_ERROR = "error"
IN_PROGRESS_STATUSES = (STATUS_CLONING, STATUS_SCANNING, STATUS_INDEXING)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    head_sha    TEXT,
    indexed_at  TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS files (
    project_id  TEXT NOT NULL,
    path        TEXT NOT NULL,
    blob_sha    TEXT NOT NULL,
    lang        TEXT,
    size        INTEGER,
    is_binary   INTEGER NOT NULL DEFAULT 0,
    is_vendored INTEGER NOT NULL DEFAULT 0,
    excluded    INTEGER NOT NULL DEFAULT 0,
    indexed     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    file        TEXT NOT NULL,
    lang        TEXT,
    symbol      TEXT,
    symbol_kind TEXT,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    blob_sha    TEXT NOT NULL,
    faiss_id    INTEGER,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS embed_cache (
    blob_sha    TEXT NOT NULL,
    chunk_hash  TEXT NOT NULL,
    vector      BLOB NOT NULL,
    PRIMARY KEY (blob_sha, chunk_hash)
);

CREATE INDEX IF NOT EXISTS idx_chunks_project_faiss ON chunks(project_id, faiss_id);
CREATE INDEX IF NOT EXISTS idx_chunks_blob ON chunks(blob_sha);
CREATE INDEX IF NOT EXISTS idx_files_project_blob ON files(project_id, blob_sha);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Создать data-dir и схему, если их нет. Идемпотентно."""
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    settings.indexes_dir.mkdir(parents=True, exist_ok=True)
    with _connect(settings.db_path) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Соединение на операцию (SQLite дёшев на открытие; WAL допускает конкуррентность)."""
    conn = _connect(get_settings().db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# --- projects ---

def create_project(project_id: str, url: str, name: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO projects (id, url, name, status) VALUES (?, ?, ?, ?)",
            (project_id, url, name, status),
        )


def set_status(project_id: str, status: str, error: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, error = ? WHERE id = ?",
            (status, error, project_id),
        )


def set_head_sha(project_id: str, head_sha: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE projects SET head_sha = ? WHERE id = ?", (head_sha, project_id))


def mark_ready(project_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE projects SET status = ?, indexed_at = datetime('now'), error = NULL WHERE id = ?",
            (STATUS_READY, project_id),
        )


def get_project(project_id: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()


def list_projects() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()


def recover_stuck() -> int:
    """Проекты, застрявшие в in-progress после рестарта сервиса, → error. Возвращает число."""
    placeholders = ",".join("?" * len(IN_PROGRESS_STATUSES))
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE projects SET status = ?, error = ? "
            f"WHERE status IN ({placeholders})",
            (STATUS_ERROR, "прервано рестартом сервиса", *IN_PROGRESS_STATUSES),
        )
        return cur.rowcount


# --- files ---

def replace_files(project_id: str, rows: list[dict]) -> None:
    """Перезаписать список файлов проекта (полный скан). rows: dict с ключами схемы files."""
    with get_conn() as conn:
        conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))
        conn.executemany(
            "INSERT INTO files (project_id, path, blob_sha, lang, size, is_binary, is_vendored, excluded) "
            "VALUES (:project_id, :path, :blob_sha, :lang, :size, :is_binary, :is_vendored, :excluded)",
            [{"project_id": project_id, **r} for r in rows],
        )


def indexable_files(project_id: str) -> list[sqlite3.Row]:
    """Файлы, пригодные к индексации: не бинарные, не vendored, не исключённые."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM files WHERE project_id = ? AND is_binary = 0 AND is_vendored = 0 AND excluded = 0",
            (project_id,),
        ).fetchall()


def mark_files_indexed(project_id: str, paths: list[str]) -> None:
    with get_conn() as conn:
        conn.executemany(
            "UPDATE files SET indexed = 1 WHERE project_id = ? AND path = ?",
            [(project_id, p) for p in paths],
        )


# --- chunks ---

def delete_chunks(project_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))


def insert_chunks(rows: list[dict]) -> list[int]:
    """Вставить чанки (faiss_id проставляется позже через set_faiss_ids). Возвращает chunk_id по порядку."""
    ids: list[int] = []
    with get_conn() as conn:
        for r in rows:
            cur = conn.execute(
                "INSERT INTO chunks (project_id, file, lang, symbol, symbol_kind, start_line, end_line, blob_sha, text) "
                "VALUES (:project_id, :file, :lang, :symbol, :symbol_kind, :start_line, :end_line, :blob_sha, :text)",
                r,
            )
            ids.append(cur.lastrowid)
    return ids


def set_faiss_ids(pairs: list[tuple[int, int]]) -> None:
    """pairs: (faiss_id, chunk_id). Инвариант: faiss_id = порядок вектора в индексе."""
    with get_conn() as conn:
        conn.executemany("UPDATE chunks SET faiss_id = ? WHERE chunk_id = ?", pairs)


def chunk_count(project_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE project_id = ?", (project_id,)
        ).fetchone()[0]


# --- embed_cache ---

def cache_get(blob_sha: str, chunk_hash: str) -> Optional[bytes]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT vector FROM embed_cache WHERE blob_sha = ? AND chunk_hash = ?",
            (blob_sha, chunk_hash),
        ).fetchone()
        return row[0] if row else None


def cache_put(blob_sha: str, chunk_hash: str, vector: bytes) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO embed_cache (blob_sha, chunk_hash, vector) VALUES (?, ?, ?)",
            (blob_sha, chunk_hash, vector),
        )
