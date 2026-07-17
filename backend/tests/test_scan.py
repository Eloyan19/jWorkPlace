"""Тесты скана: чувствительные имена исключены, бинарь/oversize/vendored отсеяны."""
from app.indexing.scan import scan_repo


def _write(root, rel, content: bytes | str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        p.write_text(content)
    else:
        p.write_bytes(content)


def test_sensitive_and_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("JWP_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    get_settings.cache_clear()

    repo = tmp_path / "repo"
    _write(repo, "app.py", "def main():\n    return 1\n")
    _write(repo, ".env", "SECRET=abc123\n")                 # чувствительное имя → excluded
    _write(repo, "key.pem", "-----BEGIN KEY-----\n")        # чувствительный суффикс → excluded
    _write(repo, "logo.png", b"\x89PNG\x00\x00binary")       # бинарь → is_binary
    _write(repo, "package-lock.json", "{}\n")               # vendored
    _write(repo, "node_modules/dep/index.js", "x")          # в skip-каталоге → не попадает вовсе
    _write(repo, "data.bin", b"\x00\x01\x02\x00null")        # null-байты → is_binary

    result = scan_repo(repo)
    by_path = {r["path"]: r for r in result.file_rows}

    assert "node_modules/dep/index.js" not in by_path       # каталог пропущен целиком
    assert by_path[".env"]["excluded"] == 1
    assert by_path["key.pem"]["excluded"] == 1
    assert by_path["logo.png"]["is_binary"] == 1
    assert by_path["data.bin"]["is_binary"] == 1
    assert by_path["package-lock.json"]["is_vendored"] == 1
    # индексируемый только app.py
    assert by_path["app.py"]["excluded"] == 0
    assert by_path["app.py"]["is_binary"] == 0
    assert by_path["app.py"]["is_vendored"] == 0
    assert by_path["app.py"]["lang"] == "python"

    get_settings.cache_clear()


def test_oversize_lines_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("JWP_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    get_settings.cache_clear()

    repo = tmp_path / "repo"
    _write(repo, "huge.py", "x = 1\n" * 2000)               # > max_file_lines (1500) → vendored
    result = scan_repo(repo)
    row = {r["path"]: r for r in result.file_rows}["huge.py"]
    assert row["is_vendored"] == 1
    get_settings.cache_clear()
