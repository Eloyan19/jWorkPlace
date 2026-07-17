"""Интеграционный тест гейта секретов (нужен установленный gitleaks — иначе skip).

Критический инвариант безопасности: чанк, пересекающий строку с секретом (находка gitleaks),
НЕ попадает в индекс, и сам секрет не остаётся в тексте чанков.
"""
import shutil

import pytest

from app.indexing.chunker import chunk_file
from app.indexing.scan import scan_repo

pytestmark = pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks не установлен")

_LEAKY = '''def normal_function():
    return 42


def leaky():
    token = "ghp_aBcD1234567890aBcD1234567890aBcD12ef"
    return token
'''


def test_secret_chunk_excluded(tmp_path):
    (tmp_path / "app.py").write_text(_LEAKY)
    result = scan_repo(tmp_path)

    assert "app.py" in result.secret_ranges, "gitleaks должен найти секрет в app.py"

    chunks = chunk_file("app.py", "python", _LEAKY, result.secret_ranges.get("app.py"))
    symbols = {c.symbol for c in chunks}
    assert "leaky" not in symbols                      # чанк с секретом исключён
    assert "normal_function" in symbols                # нормальный код остаётся
    assert "ghp_" not in " ".join(c.text for c in chunks)  # секрета нет в тексте чанков


def test_secret_in_nested_dir(tmp_path):
    """Секрет во вложенном каталоге: ключ secret_ranges = полный rel-путь (src/app.py)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(_LEAKY)
    result = scan_repo(tmp_path)

    assert "src/app.py" in result.secret_ranges, "путь должен быть репо-относительным, не basename"
    chunks = chunk_file("src/app.py", "python", _LEAKY, result.secret_ranges.get("src/app.py"))
    assert "leaky" not in {c.symbol for c in chunks}
    assert "ghp_" not in " ".join(c.text for c in chunks)
