"""Эмбеддинги через Ollama nomic-embed-text + кэш по (blob_sha, chunk_hash).

Дизайн rag-indexing-engineer: обязательные префиксы `search_document:` (индексация) и
`search_query:` (поиск) — без них retrieval у nomic заметно деградирует. Кэш глобальный
(db.embed_cache): при reindex/форках повторные чанки не гоняем через Ollama.
"""
import hashlib
import logging

import httpx
import numpy as np

from app import db
from app.config import get_settings

logger = logging.getLogger("jworkplace.embeddings")

EMBED_DIM = 768
_DOC_PREFIX = "search_document: "
_QUERY_PREFIX = "search_query: "
# Потолок символов на эмбеддинг: nomic-embed-text по умолчанию ~2048 токенов контекста;
# длинный чанк (плотный код/минифицированная строка) иначе роняет Ollama в 500. ~1 токен ≈ 4 симв.
_MAX_EMBED_CHARS = 7000


def chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _embed_call(client: httpx.Client, text: str) -> np.ndarray | None:
    """Один эмбеддинг. None → чанк слишком длинный даже после обрезки (пропускаем)."""
    settings = get_settings()
    resp = client.post(
        f"{settings.ollama_url}/api/embeddings",
        # num_ctx поднимаем до предела nomic (8192) — иначе токен-плотные чанки бьют контекст.
        json={"model": settings.embed_model, "prompt": text[:_MAX_EMBED_CHARS],
              "options": {"num_ctx": 8192}},
        timeout=120,
    )
    if resp.status_code == 500 and "context length" in resp.text.lower():
        return None  # пропускаем патологический чанк, не роняем всю индексацию
    resp.raise_for_status()
    vec = np.asarray(resp.json()["embedding"], dtype="float32")
    if vec.shape[0] != EMBED_DIM:
        raise ValueError(f"Ожидали {EMBED_DIM}-dim эмбеддинг, получили {vec.shape[0]}")
    return vec


def embed_documents(
    blob_shas: list[str], texts: list[str], progress_cb=None
) -> tuple[np.ndarray, list[int]]:
    """Заэмбеддить чанки. Возвращает (матрица (M,768) L2-норм., индексы успешных в исходном списке).

    Кэш: ключ (blob_sha, sha256(text)). Промах → Ollama, затем в кэш. Чанки, не влезшие в контекст,
    ПРОПУСКАЕМ (kept их не содержит) — индексацию не роняем. Синхронный httpx (вызов из to_thread).

    `progress_cb(done)` — опциональный колбэк прогресса: вызывается с числом обработанных чанков
    (включая кэш-хиты и пропуски). Пайплайн через него throttled-обновляет progress в БД.
    """
    kept: list[int] = []
    rows: list[np.ndarray] = []
    with httpx.Client() as client:
        for i, (blob_sha, text) in enumerate(zip(blob_shas, texts)):
            if progress_cb is not None:
                progress_cb(i)          # i чанков уже обработано до текущего
            h = chunk_hash(text)
            cached = db.cache_get(blob_sha, h) if blob_sha else None
            if cached is not None:
                rows.append(np.frombuffer(cached, dtype="float32").copy())
                kept.append(i)
                continue
            vec = _embed_call(client, _DOC_PREFIX + text)
            if vec is None:
                logger.warning("чанк пропущен (превышает контекст эмбеддера), blob=%s", blob_sha[:8])
                continue
            rows.append(vec)
            kept.append(i)
            if blob_sha:
                db.cache_put(blob_sha, h, vec.tobytes())
    if progress_cb is not None:
        progress_cb(len(texts))         # финальный тик: все чанки обработаны
    if not rows:
        return np.zeros((0, EMBED_DIM), dtype="float32"), []
    vectors = np.vstack(rows).astype("float32")
    _l2_normalize(vectors)
    return vectors, kept


def embed_query(text: str) -> np.ndarray:
    """Эмбеддинг запроса (префикс query + L2-норма). Для Этапа 2 (поиск)."""
    with httpx.Client() as client:
        vec = _embed_call(client, _QUERY_PREFIX + text)
    if vec is None:
        raise ValueError("Запрос слишком длинный для эмбеддера.")
    vec = vec.reshape(1, -1)
    _l2_normalize(vec)
    return vec[0]


def _l2_normalize(mat: np.ndarray) -> None:
    """In-place L2-нормализация строк (IndexFlatIP на норм. векторах = косинусная близость)."""
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat /= norms
