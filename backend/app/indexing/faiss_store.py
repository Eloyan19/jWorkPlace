"""Per-project FAISS-индекс (IndexFlatIP на L2-нормированных векторах = косинусная близость).

Дизайн rag-indexing-engineer: exact-поиск брутфорсом (репо ≤5k файлов — дёшево, экономит RAM
против IVF). Векторы хранит FAISS; текст чанков — в SQLite (единый источник для валидации цитат
Этапа 2). Инвариант: faiss_id = порядок вставки вектора == chunks.faiss_id. Инкремент = полный
ребилд из кэша (IndexFlatIP не удаляет по id), почти бесплатный.
"""
from pathlib import Path

import faiss
import numpy as np

from app.config import get_settings
from app.indexing.embeddings import EMBED_DIM


def _index_path(project_id: str) -> Path:
    return get_settings().indexes_dir / project_id / "index.faiss"


# Кэш на ОДИН загруженный индекс: load_index раньше читал с диска на каждый поиск. Переключение
# проектов редкое → 1 slot убирает диск-IO без роста RAM (не держим N индексов в памяти).
_cache: tuple[str, faiss.Index] | None = None


def _invalidate(project_id: str) -> None:
    global _cache
    if _cache is not None and _cache[0] == project_id:
        _cache = None


def build_index(project_id: str, vectors: np.ndarray) -> None:
    """Собрать индекс из матрицы (N, 768) и сохранить. Перезаписывает существующий."""
    index = faiss.IndexFlatIP(EMBED_DIM)
    if len(vectors):
        index.add(vectors)
    path = _index_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(path))
    _invalidate(project_id)  # старый индекс в кэше устарел после ребилда


def load_index(project_id: str) -> faiss.Index | None:
    global _cache
    if _cache is not None and _cache[0] == project_id:
        return _cache[1]
    path = _index_path(project_id)
    if not path.exists():
        return None
    index = faiss.read_index(str(path))
    _cache = (project_id, index)
    return index


def search(project_id: str, query_vec: np.ndarray, k: int) -> list[tuple[int, float]]:
    """Вернуть top-k как (faiss_id, score). Для Этапа 2 (retrieval)."""
    index = load_index(project_id)
    if index is None or index.ntotal == 0:
        return []
    q = query_vec.reshape(1, -1).astype("float32")
    scores, ids = index.search(q, min(k, index.ntotal))
    return [(int(i), float(s)) for i, s in zip(ids[0], scores[0]) if i != -1]
