"""Тесты embeddings: кэш по (blob_sha, chunk_hash), обработка слишком длинных чанков, L2-нормализация.
Ollama mockируется (не трогаем сеть). БД кэша — реальная SQLite из data_dir.
"""
import numpy as np
import pytest

from app import db
from app.indexing import embeddings


def _fake_vec(prefix: int = 0) -> np.ndarray:
    """Синтетический эмбеддинг (768-dim, детерминированный)."""
    vec = np.arange(768, dtype="float32") + prefix
    return vec / np.linalg.norm(vec)


def test_chunk_hash_consistent():
    """chunk_hash возвращает одинаковый результат для одного текста."""
    text = "def escape(s): return html.escape(s)"
    h1 = embeddings.chunk_hash(text)
    h2 = embeddings.chunk_hash(text)
    assert h1 == h2


def test_chunk_hash_different_texts():
    """chunk_hash различает разные тексты."""
    h1 = embeddings.chunk_hash("text1")
    h2 = embeddings.chunk_hash("text2")
    assert h1 != h2


def test_embed_documents_cache_hit(data_dir, monkeypatch):
    """embed_documents находит чанк в кэше и не вызывает Ollama."""
    blob_sha = "sha_abc"
    text = "def foo(): pass"
    vec = _fake_vec()

    # Закладываем вектор в кэш до
    h = embeddings.chunk_hash(text)
    db.cache_put(blob_sha, h, vec.tobytes())

    call_count = 0

    def fake_embed_call(client, txt):
        nonlocal call_count
        call_count += 1
        return np.zeros(768, dtype="float32")

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    vectors, kept = embeddings.embed_documents([blob_sha], [text])

    # Ollama не вызывалась (кэш-хит)
    assert call_count == 0
    assert kept == [0]
    assert vectors.shape == (1, 768)


def test_embed_documents_cache_miss(data_dir, monkeypatch):
    """embed_documents вызывает Ollama на кэш-промах."""
    blob_sha = "sha_def"
    text = "def bar(): pass"
    vec = _fake_vec()

    call_count = 0

    def fake_embed_call(client, txt):
        nonlocal call_count
        call_count += 1
        return vec.copy()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    vectors, kept = embeddings.embed_documents([blob_sha], [text])

    # Ollama вызывалась
    assert call_count == 1
    assert kept == [0]
    assert vectors.shape == (1, 768)


def test_embed_documents_skips_oversized_chunk(data_dir, monkeypatch):
    """embed_documents пропускает чанк слишком большой для Ollama и продолжает."""
    blob_shas = ["sha1", "sha2"]
    texts = ["small text", "x" * 10000]  # второй чанк слишком большой

    def fake_embed_call(client, txt):
        # На второй (слишком большой) чанк возвращаем None
        if len(txt) > embeddings._MAX_EMBED_CHARS:
            return None
        return _fake_vec()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    vectors, kept = embeddings.embed_documents(blob_shas, texts)

    # Только первый чанк успешен
    assert kept == [0]
    assert vectors.shape == (1, 768)


def test_embed_documents_filters_none_blob_sha(data_dir, monkeypatch):
    """embed_documents не кэширует эмбеддинги для None blob_sha (временные чанки)."""
    text = "temp chunk"
    vec = _fake_vec()

    cache_put_called = False

    def fake_embed_call(client, txt):
        return vec.copy()

    original_cache_put = db.cache_put

    def fake_cache_put(*args):
        nonlocal cache_put_called
        cache_put_called = True
        return original_cache_put(*args)

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)
    monkeypatch.setattr(db, "cache_put", fake_cache_put)

    vectors, kept = embeddings.embed_documents([None], [text])

    # Эмбеддинг был создан, но кэш не вызывался
    assert kept == [0]
    assert not cache_put_called


def test_embed_documents_progress_callback(data_dir, monkeypatch):
    """embed_documents вызывает progress_cb для каждого чанка (кэш-хит и промах)."""
    texts = ["text1", "text2", "text3"]
    blob_shas = ["sha1", "sha2", "sha3"]

    def fake_embed_call(client, txt):
        return _fake_vec()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    progress = []

    def progress_cb(done):
        progress.append(done)

    vectors, kept = embeddings.embed_documents(blob_shas, texts, progress_cb=progress_cb)

    # Callbacks: начало каждого + финальный
    # [0, 1, 2, 3] — начало 0, 1, 2, финал 3
    assert len(progress) == 4
    assert progress[0] == 0  # начало первого
    assert progress[-1] == 3  # финал


def test_embed_documents_returns_normalized_vectors(data_dir, monkeypatch):
    """embed_documents возвращает L2-нормированные векторы."""
    text = "code"
    vec = np.array([3.0, 4.0] + [0.0] * 766, dtype="float32")  # норма = 5.0
    monkeypatch.setattr(embeddings, "_embed_call", lambda c, t: vec.copy())

    vectors, _ = embeddings.embed_documents(["sha"], [text])

    # Проверяем L2-норму (должна быть ~1.0)
    norms = np.linalg.norm(vectors, axis=1)
    np.testing.assert_allclose(norms, 1.0, rtol=1e-6)


def test_embed_documents_empty_input(data_dir, monkeypatch):
    """embed_documents на пустом списке возвращает правильную форму."""
    vectors, kept = embeddings.embed_documents([], [])
    assert vectors.shape == (0, 768)
    assert kept == []


def test_embed_query_normalizes(data_dir, monkeypatch):
    """embed_query возвращает L2-нормированный вектор."""
    vec = np.array([3.0, 4.0] + [0.0] * 766, dtype="float32")
    monkeypatch.setattr(embeddings, "_embed_call", lambda c, t: vec.copy())

    result = embeddings.embed_query("search")

    norm = np.linalg.norm(result)
    np.testing.assert_allclose(norm, 1.0, rtol=1e-6)


def test_embed_query_raises_on_oversized(data_dir, monkeypatch):
    """embed_query raises ValueError если запрос слишком большой."""
    monkeypatch.setattr(embeddings, "_embed_call", lambda c, t: None)

    with pytest.raises(ValueError, match="слишком длинный"):
        embeddings.embed_query("x" * 50000)


def test_embed_query_adds_query_prefix(data_dir, monkeypatch):
    """embed_query добавляет 'search_query: ' префикс перед отправкой на Ollama."""
    captured_text = None

    def fake_embed_call(client, txt):
        nonlocal captured_text
        captured_text = txt
        return _fake_vec()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    embeddings.embed_query("my query")

    assert captured_text.startswith("search_query: ")


def test_embed_documents_adds_doc_prefix(data_dir, monkeypatch):
    """embed_documents добавляет 'search_document: ' префикс для каждого чанка."""
    captured_texts = []

    def fake_embed_call(client, txt):
        captured_texts.append(txt)
        return _fake_vec()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    embeddings.embed_documents(["sha1"], ["my chunk"])

    assert len(captured_texts) == 1
    assert captured_texts[0].startswith("search_document: ")


def test_embed_documents_on_too_long_text_still_works(data_dir, monkeypatch):
    """embed_documents вызывает _embed_call (она обрезает текст внутри, если нужно)."""
    long_text = "x" * (embeddings._MAX_EMBED_CHARS + 1000)
    call_count = 0

    def fake_embed_call(client, txt):
        nonlocal call_count
        call_count += 1
        # _embed_call сама обрезает внутри: text[:_MAX_EMBED_CHARS]
        return _fake_vec()

    monkeypatch.setattr(embeddings, "_embed_call", fake_embed_call)

    vectors, kept = embeddings.embed_documents(["sha"], [long_text])

    # _embed_call была вызвана, несмотря на длину
    assert call_count == 1
    assert kept == [0]
    assert vectors.shape == (1, 768)
