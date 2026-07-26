"""Тесты faiss_store: LRU-кэш индекса, build/load/delete/search с мокированным FAISS."""
import numpy as np
import pytest

from app.indexing import faiss_store
from app.indexing.embeddings import EMBED_DIM


PID1 = "project_aaa111"
PID2 = "project_bbb222"


def _make_vectors(n: int, seed: int = 0) -> np.ndarray:
    """Создать n векторов размером EMBED_DIM=768 (нормированных для IndexFlatIP)."""
    rng = np.random.RandomState(seed)
    vecs = rng.randn(n, EMBED_DIM).astype("float32")
    # Нормируем
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    vecs = vecs / (norms + 1e-8)
    return vecs


@pytest.fixture(autouse=True)
def reset_cache():
    """После каждого теста сбросить глобальный кэш."""
    yield
    faiss_store._cache = None


def test_build_index_creates_file(data_dir):
    """build_index создаёт файл индекса и инвалидирует кэш."""
    vectors = _make_vectors(3)
    faiss_store.build_index(PID1, vectors)

    index_path = faiss_store._index_path(PID1)
    assert index_path.exists()


def test_build_empty_index(data_dir):
    """build_index с пустым массивом тоже должен работать."""
    vectors = np.array([], dtype="float32").reshape(0, EMBED_DIM)
    faiss_store.build_index(PID1, vectors)

    index_path = faiss_store._index_path(PID1)
    assert index_path.exists()


def test_load_index_cache_hit(data_dir):
    """load_index на второй вызов с тем же project_id возвращает кэшированный индекс."""
    vectors = _make_vectors(2)
    faiss_store.build_index(PID1, vectors)

    # Первый вызов — читает с диска, кэширует
    idx1 = faiss_store.load_index(PID1)
    assert idx1 is not None

    # Второй вызов — кэш-хит
    idx2 = faiss_store.load_index(PID1)
    assert idx2 is idx1  # один и тот же объект из кэша


def test_load_index_cache_miss_different_project(data_dir):
    """При переключении проекта старый индекс выгружается из кэша."""
    vectors = _make_vectors(1)
    faiss_store.build_index(PID1, vectors)
    faiss_store.build_index(PID2, vectors)

    idx1 = faiss_store.load_index(PID1)
    # Переключаемся на PID2 — PID1 выходит из кэша
    idx2 = faiss_store.load_index(PID2)
    assert idx1 is not None
    assert idx2 is not None
    assert idx1 is not idx2

    # PID1 больше не в кэше, третий вызов читает с диска
    idx1_again = faiss_store.load_index(PID1)
    assert idx1_again is not idx1  # новый объект с диска


def test_load_index_nonexistent_returns_none(data_dir):
    """load_index на несуществующий индекс возвращает None."""
    result = faiss_store.load_index("nonexistent")
    assert result is None


def test_delete_index_removes_file(data_dir):
    """delete_index удаляет файл и сбрасывает кэш."""
    vectors = _make_vectors(1)
    faiss_store.build_index(PID1, vectors)
    assert faiss_store._index_path(PID1).exists()

    faiss_store.delete_index(PID1)
    assert not faiss_store._index_path(PID1).exists()


def test_delete_index_invalidates_cache(data_dir):
    """delete_index инвалидирует кэш для этого проекта."""
    vectors = _make_vectors(1)
    faiss_store.build_index(PID1, vectors)

    # Кэшируем индекс
    idx1 = faiss_store.load_index(PID1)
    assert faiss_store._cache is not None

    # Удаляем
    faiss_store.delete_index(PID1)
    assert faiss_store._cache is None


def test_delete_nonexistent_is_idempotent(data_dir):
    """delete_index на несуществующий проект не ошибается."""
    # Не должно быть исключения
    faiss_store.delete_index("nonexistent")


def test_search_returns_top_k(data_dir):
    """search возвращает top-k результатов с косинусными скорами."""
    vectors = _make_vectors(3, seed=42)
    faiss_store.build_index(PID1, vectors)

    # Запрос близок к первому вектору (используем первый вектор)
    query = vectors[0]
    results = faiss_store.search(PID1, query, k=2)

    assert len(results) == 2
    assert results[0][0] == 0  # faiss_id=0 (первый вектор — максимум)
    assert results[0][1] > 0.99  # высокий score (почти идентичен себе)


def test_search_empty_index_returns_empty(data_dir):
    """search на пустом индексе возвращает пустой список."""
    vectors = np.array([], dtype="float32").reshape(0, EMBED_DIM)
    faiss_store.build_index(PID1, vectors)

    query = _make_vectors(1)[0]
    results = faiss_store.search(PID1, query, k=5)
    assert results == []


def test_search_nonexistent_index_returns_empty(data_dir):
    """search на несуществующем индексе возвращает пустой список."""
    query = _make_vectors(1)[0]
    results = faiss_store.search("nonexistent", query, k=5)
    assert results == []


def test_search_k_limited_by_total(data_dir):
    """search не возвращает больше элементов, чем всего в индексе."""
    vectors = _make_vectors(2)
    faiss_store.build_index(PID1, vectors)

    query = _make_vectors(1, seed=99)[0]
    results = faiss_store.search(PID1, query, k=10)  # k=10, но всего 2 элемента
    assert len(results) == 2


def test_rebuild_index_invalidates_cache(data_dir):
    """При rebuild_index кэш инвалидируется для использования свежего индекса."""
    vectors1 = _make_vectors(1)
    faiss_store.build_index(PID1, vectors1)
    idx1 = faiss_store.load_index(PID1)

    # Перестраиваем с другим контентом
    vectors2 = _make_vectors(2, seed=77)
    faiss_store.build_index(PID1, vectors2)

    idx2 = faiss_store.load_index(PID1)
    # Разные объекты индекса (кэш был инвалидирован)
    assert idx1 is not idx2
