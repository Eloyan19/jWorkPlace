"""Тесты grounding.py: line-based валидация цитат (без сети) + redaction секретов.

Файлы читаются с диска ($JWP_DATA_DIR/repos/<pid>/<file>) — как в проде (репо материализовано
`git clone`). Фикстура `data_dir` (conftest.py) даёт изолированный tmp JWP_DATA_DIR на тест.
"""
from app.chat import grounding
from app.config import get_settings

PID = "abc123def456"


def _hit(file="src/util.py", lang="python", symbol="striptags", start=1, end=1):
    return {
        "file": file,
        "lang": lang,
        "symbol": symbol,
        "symbol_kind": "function_definition",
        "start_line": start,
        "end_line": end,
        "citation": f"{file}::{symbol}::L{start}-{end}",
    }


def _write_repo_file(rel_path: str, content: str) -> None:
    path = get_settings().repos_dir / PID / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_valid_line_based_quote_accepted(data_dir):
    _write_repo_file("src/util.py", "def striptags(s):\n    return s\n")
    hit = _hit(start=1, end=1)
    raw = '{"answer": "удаляет теги [1]", "used": [{"id": 1, "quote": "def striptags(s):"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert dropped == 0
    assert len(sources) == 1
    assert sources[0] == {
        "id": 1,
        "file": "src/util.py",
        "symbol": "striptags",
        "lines": "L1-1",
        "citation": "src/util.py::striptags::L1-1",
        "quote": "def striptags(s):",
    }


def test_fabricated_quote_dropped(data_dir):
    _write_repo_file("src/util.py", "def striptags(s):\n    return s\n")
    hit = _hit(start=1, end=1)
    raw = '{"answer": "ok [1]", "used": [{"id": 1, "quote": "совершенно другая строка"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert sources == []
    assert dropped == 1


def test_code_quote_is_not_whitespace_normalized(data_dir):
    # Отступ в файле — 4 пробела; «цитата» с 2 пробелами не является дословной подстрокой.
    # Нормализация пробелов для КОДА запрещена (CLAUDE.md) — иначе Python/YAML ложно совпадают.
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    hit = _hit(start=1, end=2)
    raw = '{"answer": "ok", "used": [{"id": 1, "quote": "def f():\\n  return 1"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert sources == []
    assert dropped == 1


def test_prose_quote_is_whitespace_normalized(data_dir):
    _write_repo_file("README.md", "Этот   проект   делает X.\n")
    hit = _hit(file="README.md", lang=None, symbol="intro", start=1, end=1)
    raw = '{"answer": "ok", "used": [{"id": 1, "quote": "Этот проект делает X."}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert dropped == 0
    assert len(sources) == 1


def test_all_used_invalid_yields_empty_sources_for_downgrade(data_dir):
    """Пустой sources при непустом answer — сигнал вызывающему коду сделать downgrade в abstain
    (см. api/chat.py). Здесь проверяем только сам grounding-контракт: sources==[]."""
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    hit = _hit(start=1, end=2)
    raw = '{"answer": "выдуманный ответ без реальной цитаты", "used": [{"id": 1, "quote": "nonexistent"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert sources == []
    assert answer == "выдуманный ответ без реальной цитаты"
    assert dropped == 1


def test_invalid_id_dropped(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    hit = _hit(start=1, end=2)
    raw = '{"answer": "ok", "used": [{"id": 99, "quote": "def f():"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert sources == []
    assert dropped == 1


def test_path_traversal_in_file_rejected(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    hit = _hit(file="../../etc/passwd", start=1, end=1)
    raw = '{"answer": "ok", "used": [{"id": 1, "quote": "root"}]}'

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert sources == []
    assert dropped == 1


def test_malformed_json_falls_back_to_raw_with_no_sources(data_dir):
    answer, sources, dropped = grounding.parse_and_validate("не json вовсе", [], PID)

    assert sources == []
    assert dropped == 0
    assert answer == "не json вовсе"


def test_redact_masks_kv_secret():
    text = 'api_key="sk-abcdefghij1234567890ABCDEFGHIJ"'

    out = grounding.redact(text)

    assert "sk-abcdefghij1234567890ABCDEFGHIJ" not in out
    assert "[REDACTED]" in out


def test_redact_masks_bare_high_entropy_token():
    jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"
    text = f"токен в комментарии: {jwt}"

    out = grounding.redact(text)

    assert jwt not in out
    assert "[REDACTED]" in out


def test_quote_of_redacted_line_validates_against_redacted_excerpt(data_dir):
    # Строка с high-entropy токеном: build_context отдаёт модели redact(text), поэтому
    # дословная цитата модели содержит [REDACTED]. Валидация тоже прогоняет срез через redact —
    # значит цитата совпадает и НЕ отбрасывается (иначе был бы ложный abstain рядом с секретом).
    secret = "AKIA1234567890ABCDEF1234567890ABCDEFGH"
    _write_repo_file("src/cfg.py", f"TOKEN = '{secret}'\n")
    hit = _hit(file="src/cfg.py", lang="python", symbol="TOKEN", start=1, end=1)
    redacted_line = grounding.redact(f"TOKEN = '{secret}'")
    raw = '{"answer": "конфиг с токеном [1]", "used": [{"id": 1, "quote": %s}]}' % (
        __import__("json").dumps(redacted_line)
    )

    answer, sources, dropped = grounding.parse_and_validate(raw, [hit], PID)

    assert dropped == 0
    assert len(sources) == 1
    assert secret not in sources[0]["quote"]  # секрет наружу не течёт


def test_redact_leaves_ordinary_code_untouched():
    text = "def striptags(s: str) -> str:\n    return re.sub(r'<[^>]*>', '', s)"

    out = grounding.redact(text)

    assert out == text
