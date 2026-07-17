"""Тесты чанкера: границы символов (tree-sitter), пропуск секретов, fallback, соответствие строк."""
from app.indexing.chunker import chunk_file

_PY = '''import os


def alpha(x):
    return x + 1


class Beta:
    def method(self):
        return 2


def gamma():
    pass
'''


def test_python_symbol_boundaries():
    chunks = chunk_file("m.py", "python", _PY)
    symbols = {c.symbol for c in chunks if c.symbol}
    assert {"alpha", "Beta", "gamma"} <= symbols
    # каждый чанк text дословно совпадает со своим диапазоном строк (инвариант валидации цитат)
    lines = _PY.splitlines()
    for c in chunks:
        assert c.text == "\n".join(lines[c.start_line - 1:c.end_line])


def test_secret_range_skips_chunk():
    # alpha на строках 4-5 — помечаем как секрет, чанк должен исчезнуть
    chunks = chunk_file("m.py", "python", _PY, secret_ranges=[(4, 5)])
    assert all(c.symbol != "alpha" for c in chunks)


def test_fallback_for_unknown_lang():
    text = "\n".join(f"line {i}" for i in range(1, 200))
    chunks = chunk_file("notes.txt", None, text)
    assert len(chunks) >= 2                    # построчные окна
    assert all(c.symbol is None for c in chunks)
    assert chunks[0].start_line == 1


def test_empty_file():
    assert chunk_file("e.py", "python", "") == []
