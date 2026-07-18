"""Оркестрация индексации: фоновая state-machine cloning→scanning→indexing→ready/error.

Сериализуем индексацию через Semaphore(1) — потолок 3.8 ГБ RAM не позволяет гнать несколько
репо параллельно (клон + эмбеддинги + FAISS). Блокирующие шаги (git/scan/embed/faiss) уходят в
to_thread, статусы пишутся в SQLite — фронт поллит GET /api/projects/{id}.

Инвариант: секреты гейтятся до эмбеддинга (scan.secret_ranges → chunker пропускает строки).
Инвариант порядка: rows[i] ↔ vectors[i] ↔ chunk_ids[i] ↔ faiss_id=i.
"""
import asyncio
import logging

import numpy as np

from app import db
from app.config import get_settings
from app.indexing import clone, embeddings, faiss_store, lexical, scan
from app.indexing.chunker import chunk_file

logger = logging.getLogger("jworkplace.pipeline")

# Одна индексация за раз — защита памяти на 3.8 ГБ.
_semaphore = asyncio.Semaphore(1)
# Сильные ссылки на фоновые задачи: asyncio держит лишь weak ref, иначе долгую индексацию
# может собрать GC и молча отменить. Снимаем ссылку по завершении.
_tasks: set[asyncio.Task] = set()


def schedule(project_id: str, url: str, reindex: bool = False) -> None:
    """Поставить проект в фоновую обработку (не блокирует эндпоинт).

    Вызывать ТОЛЬКО из async-контекста (running loop есть) — оба вызывающих эндпоинта async.
    """
    task = asyncio.create_task(_dispatch(project_id, url, reindex))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _dispatch(project_id: str, url: str, reindex: bool) -> None:
    async with _semaphore:
        try:
            await asyncio.to_thread(_run_full, project_id, url, reindex)
        except Exception as exc:  # noqa: BLE001 — любой сбой → статус error, не роняем сервис
            # Детали (в т.ч. пути/stderr git) — только в лог; клиенту отдаём обобщённо,
            # чтобы внутренняя инфраструктура не утекала через projects.error.
            logger.exception("pipeline упал для %s", project_id)
            db.set_status(project_id, db.STATUS_ERROR, _user_error(exc))


def _user_error(exc: Exception) -> str:
    """Безопасное сообщение об ошибке для клиента (без внутренних путей/stderr)."""
    from app.indexing.clone import CloneError
    from app.indexing.scan import ScanError
    if isinstance(exc, (CloneError, ScanError)):
        return str(exc)  # эти сообщения уже составлены человеко-читаемо и без секретов
    return "Внутренняя ошибка индексации. Подробности — в журнале сервиса."


def _run_full(project_id: str, url: str, reindex: bool) -> None:
    """Синхронная state-machine (выполняется в отдельном потоке)."""
    settings = get_settings()

    # 1. cloning / pull
    db.set_status(project_id, db.STATUS_CLONING)
    if reindex:
        head_sha = clone.pull_repo(project_id)
        repo_dir = settings.repos_dir / project_id
    else:
        repo_dir, head_sha = clone.clone_repo(url, project_id)
    db.set_head_sha(project_id, head_sha)

    # 2. scanning — обход, фильтры, gitleaks
    db.set_status(project_id, db.STATUS_SCANNING)
    result = scan.scan_repo(repo_dir)
    db.replace_files(project_id, result.file_rows)

    # 3. indexing — чанкинг → эмбеддинги → FAISS
    db.set_status(project_id, db.STATUS_INDEXING)
    _index(project_id, repo_dir, result.secret_ranges)

    # 4. ready
    db.mark_ready(project_id)
    logger.info("проект %s готов: %d чанков", project_id, db.chunk_count(project_id))


def _index(project_id: str, repo_dir, secret_ranges: dict) -> None:
    """Собрать чанки индексируемых файлов, заэмбеддить, построить FAISS, записать chunks."""
    rows: list[dict] = []
    texts: list[str] = []
    blob_shas: list[str] = []

    for f in db.indexable_files(project_id):
        path = f["path"]
        full = repo_dir / path
        try:
            source = full.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks = chunk_file(path, f["lang"], source, secret_ranges.get(path))
        if not chunks:
            continue
        for c in chunks:
            rows.append({
                "project_id": project_id, "file": c.file, "lang": c.lang,
                "symbol": c.symbol, "symbol_kind": c.symbol_kind,
                "start_line": c.start_line, "end_line": c.end_line,
                "blob_sha": f["blob_sha"], "text": c.text,
            })
            texts.append(c.text)
            blob_shas.append(f["blob_sha"])

    db.delete_chunks(project_id)
    if not rows:
        faiss_store.build_index(project_id, np.zeros((0, embeddings.EMBED_DIM), dtype="float32"))
        _rebuild_fts(project_id, [], [])
        return

    # Часть чанков может не влезть в контекст эмбеддера — kept оставляет только успешные,
    # чтобы rows/vectors/faiss_id оставались согласованными по порядку.
    vectors, kept = embeddings.embed_documents(blob_shas, texts)
    rows = [rows[i] for i in kept]
    if not rows:
        faiss_store.build_index(project_id, np.zeros((0, embeddings.EMBED_DIM), dtype="float32"))
        _rebuild_fts(project_id, [], [])
        return
    chunk_ids = db.insert_chunks(rows)           # порядок сохранён = порядок vectors
    faiss_store.build_index(project_id, vectors) # faiss_id = порядок = i
    db.set_faiss_ids([(i, chunk_ids[i]) for i in range(len(chunk_ids))])
    # Лексический индекс (FTS5) — из тех же rows/chunk_ids, симметрично FAISS (полный ребилд).
    _rebuild_fts(project_id, rows, chunk_ids)
    # indexed=1 только у файлов, чьи чанки реально попали в индекс (после фильтра kept).
    db.mark_files_indexed(project_id, sorted({r["file"] for r in rows}))


def _rebuild_fts(project_id: str, rows: list[dict], chunk_ids: list[int]) -> None:
    """Пересобрать per-project FTS5 из чанков (drop→create→insert). Токенизируем тут (не в db)."""
    db.drop_fts(project_id)
    db.create_fts(project_id)
    if not rows:
        return
    fts_rows = [
        (
            chunk_ids[i],
            lexical.code_tokenize(r["text"]),
            lexical.code_tokenize(r["symbol"] or ""),
            lexical.code_tokenize(r["file"]),
        )
        for i, r in enumerate(rows)
    ]
    db.fts_insert(project_id, fts_rows)
