"""Тесты слоя БД: CRUD проектов/файлов/чанков, кэш, recover_stuck."""
from app import db


def test_project_lifecycle(data_dir):
    db.create_project("p1", "https://github.com/o/r.git", "o/r", db.STATUS_CLONING)
    assert db.get_project("p1")["status"] == db.STATUS_CLONING

    db.set_head_sha("p1", "abc123")
    db.set_status("p1", db.STATUS_INDEXING)
    assert db.get_project("p1")["head_sha"] == "abc123"

    db.mark_ready("p1")
    row = db.get_project("p1")
    assert row["status"] == db.STATUS_READY
    assert row["indexed_at"] is not None
    assert row["error"] is None


def test_recover_stuck(data_dir):
    db.create_project("a", "u", "n", db.STATUS_INDEXING)
    db.create_project("b", "u", "n", db.STATUS_READY)
    db.create_project("c", "u", "n", db.STATUS_CLONING)

    recovered = db.recover_stuck()
    assert recovered == 2                          # a и c были in-progress
    assert db.get_project("a")["status"] == db.STATUS_ERROR
    assert db.get_project("b")["status"] == db.STATUS_READY
    assert db.get_project("c")["status"] == db.STATUS_ERROR


def test_files_and_indexable(data_dir):
    db.create_project("p", "u", "n", db.STATUS_SCANNING)
    db.replace_files("p", [
        {"path": "a.py", "blob_sha": "s1", "lang": "python", "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
        {"path": ".env", "blob_sha": "s2", "lang": None, "size": 5,
         "is_binary": 0, "is_vendored": 0, "excluded": 1},
        {"path": "img.png", "blob_sha": "s3", "lang": None, "size": 99,
         "is_binary": 1, "is_vendored": 0, "excluded": 0},
    ])
    indexable = [r["path"] for r in db.indexable_files("p")]
    assert indexable == ["a.py"]                    # .env и img.png отсеяны


def test_chunks_and_faiss_ids(data_dir):
    db.create_project("p", "u", "n", db.STATUS_INDEXING)
    ids = db.insert_chunks([
        {"project_id": "p", "file": "a.py", "lang": "python", "symbol": "f",
         "symbol_kind": "function_definition", "start_line": 1, "end_line": 3,
         "blob_sha": "s1", "text": "def f(): pass"},
    ])
    assert len(ids) == 1
    db.set_faiss_ids([(0, ids[0])])
    assert db.chunk_count("p") == 1

    db.delete_chunks("p")
    assert db.chunk_count("p") == 0


def test_embed_cache(data_dir):
    assert db.cache_get("blob", "h") is None
    db.cache_put("blob", "h", b"\x00\x01\x02\x03")
    assert db.cache_get("blob", "h") == b"\x00\x01\x02\x03"
    # повторный put не падает (INSERT OR IGNORE)
    db.cache_put("blob", "h", b"\xff")
    assert db.cache_get("blob", "h") == b"\x00\x01\x02\x03"
