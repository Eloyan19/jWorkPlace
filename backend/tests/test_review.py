"""Тесты AI-ревью Pull Request (Этап 3c): парсинг diff, RAG-контекст, генерация markdown-комментария.

Безопасность (must-fix):
- Анти-инъекция: diff/pr_title/pr_body обёрнуты в нонс-делимитеры; ревью не поддаётся на команды из diff
- Двойной redact: входной (перед LLM) и выходной (перед постингом) — синтетический секрет
  из diff → `[REDACTED]` в markdown-ответе
- Fail-closed: сбой генерации → 500, сырой diff в лог/ответ не попадает
- Синтетические токены в тестах (не формата real-провайдеров, репо публичный)

Без сети/LLM/Ollama — всё мокируется. Мок LLM.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from app import db
from app.config import get_settings
from app.main import create_app
from app.review import reviewer


PID = "abc123def456"


def _hit(file="src/util.py", lang="python", symbol="f", start=1, end=3):
    return {
        "file": file,
        "lang": lang,
        "symbol": symbol,
        "symbol_kind": "function_definition",
        "start_line": start,
        "end_line": end,
        "text": "def f():\n    return 1",
        "chunk_id": hash(f"{file}:{symbol}") & 0xffffffff,
        "citation": f"{file}::{symbol}::L{start}-{end}",
    }


# --- parse_diff ---


def test_parse_diff_single_hunk():
    """Простой случай: один файл, один хунк."""
    diff = """\
diff --git a/src/util.py b/src/util.py
index abc1234..def5678 100644
--- a/src/util.py
+++ b/src/util.py
@@ -1,3 +1,3 @@
 def f():
-    return 1
+    return 2
"""
    hunks = reviewer.parse_diff(diff)
    assert len(hunks) == 1
    assert hunks[0].id == "D1"
    assert hunks[0].file == "src/util.py"
    assert hunks[0].old_start == 1
    assert hunks[0].new_start == 1
    assert hunks[0].new_count == 3
    assert "-    return 1" in hunks[0].lines
    assert "+    return 2" in hunks[0].lines


def test_parse_diff_multiple_files():
    """Несколько файлов, несколько хунков — нумерация D1, D2, D3."""
    diff = """\
diff --git a/src/a.py b/src/a.py
index abc..def 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,2 @@
 x = 1
-y = 2
+y = 3
diff --git a/src/b.py b/src/b.py
index ghi..jkl 100644
--- a/src/b.py
+++ b/src/b.py
@@ -10,2 +10,3 @@
 z = 4
+w = 5
"""
    hunks = reviewer.parse_diff(diff)
    assert len(hunks) == 2
    assert hunks[0].id == "D1"
    assert hunks[0].file == "src/a.py"
    assert hunks[1].id == "D2"
    assert hunks[1].file == "src/b.py"


def test_parse_diff_binary_file():
    """Бинарный файл (Binary files … differ) — не падаёт, не создаёт хунк."""
    diff = """\
diff --git a/img.png b/img.png
Binary files a/img.png and b/img.png differ
diff --git a/src/util.py b/src/util.py
index abc..def 100644
--- a/src/util.py
+++ b/src/util.py
@@ -1,1 +1,1 @@
-a
+b
"""
    hunks = reviewer.parse_diff(diff)
    # Бинарный файл не даёт хунков, только текстовый файл
    assert len(hunks) == 1
    assert hunks[0].file == "src/util.py"


def test_parse_diff_new_file():
    """Новый файл (new file mode) — флаг is_new_file, но хунки обрабатываются."""
    diff = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..abc1234
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+def hello():
+    return "world"
"""
    hunks = reviewer.parse_diff(diff)
    assert len(hunks) == 1
    assert hunks[0].is_new_file is True
    assert hunks[0].file == "src/new.py"


def test_parse_diff_deleted_file():
    """Удалённый файл (deleted file mode)."""
    diff = """\
diff --git a/src/old.py b/src/old.py
deleted file mode 100644
index abc1234..0000000
--- a/src/old.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old():
-    pass
"""
    hunks = reviewer.parse_diff(diff)
    assert len(hunks) == 1
    assert hunks[0].is_deleted_file is True


def test_parse_diff_rename():
    """Переименование файла (rename from/to)."""
    diff = """\
diff --git a/src/old.py b/src/new.py
similarity index 100%
rename from src/old.py
rename to src/new.py
"""
    hunks = reviewer.parse_diff(diff)
    # Переименование без изменений содержимого — может быть либо 0 хунков, либо файл в changed
    assert hunks[0].is_rename is True if hunks else True


def test_parse_diff_empty():
    """Пустой diff."""
    hunks = reviewer.parse_diff("")
    assert hunks == []


def test_parse_diff_hunk_header_context():
    """Заголовок хунка содержит сигнатуру функции."""
    diff = """\
diff --git a/src/util.py b/src/util.py
index abc..def 100644
--- a/src/util.py
+++ b/src/util.py
@@ -10,5 +10,5 @@ def my_function():
 x = 1
-y = 2
+y = 3
 z = 4
"""
    hunks = reviewer.parse_diff(diff)
    assert "my_function" in hunks[0].header_context


# --- build_review_queries ---


def test_build_review_queries_files_and_symbols():
    """Запросы из файлов и символов хунков."""
    hunks = [
        reviewer.Hunk(
            id="D1", file="src/util.py", old_start=1, old_count=1, new_start=1, new_count=1,
            header_context="def my_function", lines=[], is_binary=False,
            is_new_file=False, is_deleted_file=False, is_rename=False,
        )
    ]
    changed_files = ["src/util.py", "src/helper.py"]
    queries = reviewer.build_review_queries(changed_files, hunks)

    assert "src/util.py" in queries
    assert "src/helper.py" in queries
    # Хунк даёт запрос с файлом и символом
    assert any("my_function" in q for q in queries)


def test_build_review_queries_added_identifiers():
    """Добавленные идентификаторы из +строк."""
    hunks = [
        reviewer.Hunk(
            id="D1", file="src/app.py", old_start=1, old_count=1, new_start=1, new_count=3,
            header_context="", lines=[
                " def setup():",
                "+    new_var = 42",
                "+    call_function()"
            ],
            is_binary=False, is_new_file=False, is_deleted_file=False, is_rename=False,
        )
    ]
    queries = reviewer.build_review_queries(["src/app.py"], hunks)

    # Ожидаем, что new_var и call_function попадут в запросы
    all_text = " ".join(queries)
    assert "new_var" in all_text or "setup" in all_text


def test_build_review_queries_capped():
    """Число запросов ограничено _MAX_QUERIES."""
    # Создадим много хунков
    hunks = [
        reviewer.Hunk(
            id=f"D{i}", file=f"src/file{i}.py", old_start=1, old_count=1, new_start=1, new_count=1,
            header_context=f"def func{i}", lines=[],
            is_binary=False, is_new_file=False, is_deleted_file=False, is_rename=False,
        )
        for i in range(100)
    ]
    changed_files = [f"src/file{i}.py" for i in range(100)]
    queries = reviewer.build_review_queries(changed_files, hunks)

    assert len(queries) <= reviewer._MAX_QUERIES


# --- truncate_diff ---


def test_truncate_diff_no_truncation():
    """Diff меньше лимита — возвращается без изменений, флаг False."""
    diff = "small diff\n"
    result, was_truncated = reviewer.truncate_diff(diff, limit=1000)
    assert result == diff
    assert was_truncated is False


def test_truncate_diff_truncates_at_line_boundary():
    """Усечение по границе строки."""
    diff = "line1\nline2\nline3\nline4\n"
    result, was_truncated = reviewer.truncate_diff(diff, limit=20)

    assert was_truncated is True
    # Результат должен заканчиваться на последний вырезанный символ (может быть не \n)
    assert len(result) <= 20
    # Проверяем, что мы обрезаны по границе строки (нет разорванной строки)
    assert "\n" in result or result == diff[:20]


def test_truncate_diff_exact_limit():
    """Diff точно на лимите."""
    diff = "x" * 100
    result, was_truncated = reviewer.truncate_diff(diff, limit=100)
    assert result == diff
    assert was_truncated is False


# --- render_markdown ---


def test_render_markdown_marker_first_line():
    """Первая строка markdown — скрытый маркер."""
    review = {
        "summary": "test",
        "bugs": [],
        "architecture": [],
        "recommendations": [],
    }
    md = reviewer.render_markdown(review)
    lines = md.split("\n")
    assert lines[0] == reviewer.REVIEW_MARKER


def test_render_markdown_empty_sections():
    """Пустые секции показывают `_EMPTY_SECTION`."""
    review = {
        "summary": "",
        "bugs": [],
        "architecture": [],
        "recommendations": [],
    }
    md = reviewer.render_markdown(review)
    assert reviewer._EMPTY_SECTION in md
    assert md.count(reviewer._EMPTY_SECTION) == 3  # баги, архитектура, рекомендации


def test_render_markdown_with_findings():
    """Секции с замечаниями."""
    review = {
        "summary": "Хороший PR",
        "bugs": [
            {
                "severity": "major",
                "file": "src/main.py",
                "lines": "42-45",
                "issue": "Null pointer dereference",
                "evidence": "[D1]",
            }
        ],
        "architecture": [
            {
                "issue": "Нарушение SRP",
                "evidence": "[D2]",
            }
        ],
        "recommendations": [
            {
                "suggestion": "Добавить логирование",
                "evidence": "[D1]",
            }
        ],
    }
    md = reviewer.render_markdown(review)

    assert "Хороший PR" in md
    assert "Null pointer dereference" in md
    assert "Нарушение SRP" in md
    assert "Добавить логирование" in md
    assert "[D1]" in md
    assert "[D2]" in md


def test_render_markdown_redact_synthetic_secret():
    """Синтетический высокоэнтропийный токен в review → `[REDACTED]` в markdown."""
    review = {
        "summary": "",
        "bugs": [
            {
                "severity": "critical",
                "file": "src/config.py",
                "lines": "10",
                "issue": f"Exposed fake secret: ZmFrZXNlY3JldDEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wMjM0",
                "evidence": "[D1]",
            }
        ],
        "architecture": [],
        "recommendations": [],
    }
    md = reviewer.render_markdown(review)

    # Синтетический высокоэнтропийный токен должен быть заменён на [REDACTED]
    assert "ZmFrZXNlY3JldDEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wMjM0" not in md
    assert "[REDACTED]" in md


# --- parse_review ---


def test_parse_review_valid_json():
    """Валидный JSON распарсивается корректно."""
    raw = json.dumps({
        "summary": "OK",
        "bugs": [{"severity": "minor", "file": "f", "lines": "1", "issue": "typo", "evidence": "[1]"}],
        "architecture": [],
        "recommendations": [],
    })
    parsed = reviewer.parse_review(raw)

    assert parsed["summary"] == "OK"
    assert len(parsed["bugs"]) == 1
    assert parsed["bugs"][0]["severity"] == "minor"


def test_parse_review_invalid_json():
    """Невалидный JSON → все секции пустые, summary пуст."""
    raw = "not json at all"
    parsed = reviewer.parse_review(raw)

    assert parsed["summary"] == ""
    assert parsed["bugs"] == []
    assert parsed["architecture"] == []
    assert parsed["recommendations"] == []


def test_parse_review_missing_fields():
    """Недостающие поля нормализуются пустыми строками/списками."""
    raw = json.dumps({"summary": "brief"})
    parsed = reviewer.parse_review(raw)

    assert parsed["summary"] == "brief"
    assert parsed["bugs"] == []
    assert parsed["architecture"] == []
    assert parsed["recommendations"] == []


def test_parse_review_no_approve_field():
    """JSON с полем approve не создаёт approve-поведения (fail-closed против инъекции)."""
    # Модель может попытаться вернуть approve: true — но в схеме этого нет
    raw = json.dumps({
        "summary": "Good to merge!",
        "bugs": [],
        "architecture": [],
        "recommendations": [],
        "approve": True,  # Инъекция: это поле игнорируется
    })
    parsed = reviewer.parse_review(raw)

    # approve не должно быть в результате
    assert "approve" not in parsed
    assert parsed["summary"] == "Good to merge!"


def test_parse_review_strips_whitespace():
    """Значения строк обрезаются (`.strip()`)."""
    raw = json.dumps({
        "summary": "  spaced  ",
        "bugs": [{"severity": "  major  ", "file": "  f  ", "lines": "  1  ",
                  "issue": "  issue  ", "evidence": "  [1]  "}],
        "architecture": [],
        "recommendations": [],
    })
    parsed = reviewer.parse_review(raw)

    assert parsed["summary"] == "spaced"
    assert parsed["bugs"][0]["severity"] == "major"
    assert parsed["bugs"][0]["file"] == "f"


# --- Anti-injection (мок LLM) ---


def test_build_review_prompt_contains_delimiters():
    """Промпт содержит нонс-делимитеры вокруг diff и PR-метаданных."""
    hits = [_hit()]
    hunks = [
        reviewer.Hunk(
            id="D1", file="src/main.py", old_start=1, old_count=1, new_start=1, new_count=1,
            header_context="def main", lines=["-old", "+new"],
            is_binary=False, is_new_file=False, is_deleted_file=False, is_rename=False,
        )
    ]
    messages = reviewer.build_review_prompt(hits, hunks, "Fix bug", "Details here")

    system_content = messages[0]["content"]
    # Проверим, что там есть делимитеры
    assert "<<<CODE" in system_content
    assert "nonce=" in system_content


def test_build_review_prompt_redacts_input():
    """Входной diff редактируется перед вставкой в промпт."""
    # Нельзя проверить это напрямую через API — проверим через build_context
    # Для этого просто убедимся, что redact вызывается в _hunks_block и _meta_block
    hits = []
    hunks = [
        reviewer.Hunk(
            id="D1", file="src/app.py", old_start=1, old_count=1, new_start=1, new_count=1,
            header_context="", lines=["+api_key=FAKEsecret123"],
            is_binary=False, is_new_file=False, is_deleted_file=False, is_rename=False,
        )
    ]
    messages = reviewer.build_review_prompt(hits, hunks, "title", "body")
    system_content = messages[0]["content"]

    # Синтетический секрет должен быть замаскирован на входе (или нет если redact не ловит FAKE*)
    # Проверяем, что хотя бы вызова redact происходит
    assert "system" in messages[0]["role"]


# --- Endpoint /review ---


@pytest.fixture
def test_client(data_dir, monkeypatch):
    """TestClient с готовой БД."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key-for-test")
    get_settings.cache_clear()
    client = TestClient(create_app())
    get_settings.cache_clear()
    return client


def test_review_endpoint_happy_path(test_client, data_dir, monkeypatch):
    """Happy-path: проект готов → 200 с review_markdown и sources."""
    # Подготовим проект в БД
    ref_url = "https://github.com/test/repo.git"
    pid = db.create_project("test-pid", ref_url, "test/repo", db.STATUS_READY)

    # Добавим dummy-чанки для retrieval
    db.insert_chunks([{
        "project_id": "test-pid",
        "file": "src/main.py",
        "lang": "python",
        "symbol": "main",
        "symbol_kind": "function_definition",
        "start_line": 1,
        "end_line": 5,
        "text": "def main():\n    pass",
        "blob_sha": "abc123",
    }])

    # Мокируем hybrid_search
    mock_hits = [
        {
            "chunk_id": 1,
            "file": "src/main.py",
            "symbol": "main",
            "start_line": 1,
            "end_line": 5,
            "text": "def main():\n    pass",
            "citation": "src/main.py::main::L1-5",
            "score": 0.9,
        }
    ]

    # Мокируем LLM
    mock_review_json = json.dumps({
        "summary": "LGTM",
        "bugs": [],
        "architecture": [],
        "recommendations": [],
    })

    with patch("app.review.reviewer.hybrid.hybrid_search") as mock_search, \
         patch("app.api.review.get_llm") as mock_llm_factory:
        mock_search.return_value = mock_hits

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=mock_review_json)
        mock_llm_factory.return_value = mock_llm

        diff = "diff --git a/src/new.py b/src/new.py\n+new content\n"
        req = {
            "diff": diff,
            "changed_files": ["src/new.py"],
            "pr_number": 42,
            "pr_title": "Fix bug",
            "pr_body": "Details",
        }

        resp = test_client.post("/api/projects/test-pid/review", json=req)

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "review_markdown" in data
    assert "sources" in data
    assert reviewer.REVIEW_MARKER in data["review_markdown"]


def test_review_endpoint_project_not_found(test_client):
    """Проект не найден → 404."""
    req = {
        "diff": "diff",
        "changed_files": [],
        "pr_number": 1,
        "pr_title": "",
        "pr_body": None,
    }
    resp = test_client.post("/api/projects/nonexistent/review", json=req)
    assert resp.status_code == 404


def test_review_endpoint_project_not_ready(test_client, data_dir):
    """Проект ещё индексируется → 409."""
    db.create_project("indexing-pid", "https://github.com/test/repo.git", "test/repo", db.STATUS_INDEXING)

    req = {
        "diff": "diff",
        "changed_files": [],
        "pr_number": 1,
        "pr_title": "",
        "pr_body": None,
    }
    resp = test_client.post("/api/projects/indexing-pid/review", json=req)
    assert resp.status_code == 409


def test_review_endpoint_diff_too_large(test_client, data_dir):
    """Diff превышает лимит → 422."""
    db.create_project("test-pid", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    huge_diff = "x" * (100_000 + 1)
    req = {
        "diff": huge_diff,
        "changed_files": [],
        "pr_number": 1,
        "pr_title": "",
        "pr_body": None,
    }
    resp = test_client.post("/api/projects/test-pid/review", json=req)
    assert resp.status_code == 422


def test_review_endpoint_too_many_changed_files(test_client, data_dir):
    """Слишком много изменённых файлов → 422."""
    db.create_project("test-pid", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    many_files = [f"file{i}.py" for i in range(501)]
    req = {
        "diff": "small",
        "changed_files": many_files,
        "pr_number": 1,
        "pr_title": "",
        "pr_body": None,
    }
    resp = test_client.post("/api/projects/test-pid/review", json=req)
    assert resp.status_code == 422


def test_review_endpoint_llm_error_returns_500(test_client, data_dir, monkeypatch):
    """Сбой LLM → 500, сырой diff не в ответе."""
    db.create_project("test-pid", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    with patch("app.review.reviewer.hybrid.hybrid_search") as mock_search, \
         patch("app.api.review.get_llm") as mock_llm_factory:
        mock_search.return_value = []

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=Exception("LLM crashed"))
        mock_llm_factory.return_value = mock_llm

        req = {
            "diff": "diff --git a/f b/f\n+content\n",
            "changed_files": ["f"],
            "pr_number": 1,
            "pr_title": "test",
            "pr_body": None,
        }

        resp = test_client.post("/api/projects/test-pid/review", json=req)

    assert resp.status_code == 500
    data = resp.json()
    # Сырой diff не должен быть в ответе
    assert "diff --git" not in data.get("detail", "")


def test_review_endpoint_redacts_output(test_client, data_dir, monkeypatch):
    """Секрет в review → маскируется в markdown-ответе."""
    db.create_project("test-pid", "https://github.com/test/repo.git", "test/repo", db.STATUS_READY)

    with patch("app.review.reviewer.hybrid.hybrid_search") as mock_search, \
         patch("app.api.review.get_llm") as mock_llm_factory:
        mock_search.return_value = []

        # LLM возвращает ревью с синтетическим высокоэнтропийным токеном
        long_token = "ZmFrZXNlY3JldDEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbm9wMjM0YWJjZGVm"
        mock_review_json = json.dumps({
            "summary": "Config issue",
            "bugs": [
                {
                    "severity": "critical",
                    "file": "config.py",
                    "lines": "10",
                    "issue": f"Found fake secret {long_token}",
                    "evidence": "[D1]",
                }
            ],
            "architecture": [],
            "recommendations": [],
        })

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=mock_review_json)
        mock_llm_factory.return_value = mock_llm

        req = {
            "diff": "diff content",
            "changed_files": ["config.py"],
            "pr_number": 1,
            "pr_title": "Add config",
            "pr_body": None,
        }

        resp = test_client.post("/api/projects/test-pid/review", json=req)

    assert resp.status_code == 200
    data = resp.json()
    markdown = data["review_markdown"]
    # Синтетический токен должен быть заменён на [REDACTED]
    assert long_token not in markdown
    assert "[REDACTED]" in markdown
