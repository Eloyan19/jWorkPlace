"""Тесты edit/patcher.py (Этап 3a): line-based валидация edits, traversal/forbidden-guard,
детерминированная сборка diff, git apply --check. Без сети — LLM-ответ подаём строкой.

Секреты в тестах — СИНТЕТИЧЕСКИЕ (репо публичный, Push Protection): не формат реальных провайдеров.
"""
import subprocess

from app.config import get_settings
from app.edit import patcher

PID = "abc123def456"


def _hit(file="src/util.py", lang="python", symbol="f", start=1, end=3):
    return {
        "file": file,
        "lang": lang,
        "symbol": symbol,
        "symbol_kind": "function_definition",
        "start_line": start,
        "end_line": end,
        "text": "",
        "citation": f"{file}::{symbol}::L{start}-{end}",
    }


def _write_repo_file(rel_path: str, content: str) -> None:
    path = get_settings().repos_dir / PID / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _git_repo(files: dict[str, str]) -> None:
    """Инициализировать git-репо в repos_dir/PID с заданными файлами (для check_apply)."""
    repo = get_settings().repos_dir / PID
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(repo)}
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], env=env, check=True, capture_output=True)


# --- parse_and_validate_edits ---

def test_valid_edit_accepted(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    raw = ('{"summary":"меняю 1 на 2","edits":[{"id":1,"file":"src/util.py",'
           '"old_block":"    return 1","new_block":"    return 2","reason":"по инструкции"}]}')

    summary, edits, dropped = patcher.parse_and_validate_edits(raw, [_hit()], PID)

    assert summary == "меняю 1 на 2"
    assert dropped == 0
    assert len(edits) == 1
    assert edits[0]["file"] == "src/util.py"
    assert edits[0]["old_block"] == "    return 1"
    assert edits[0]["citation"] == "src/util.py::f::L1-3"


def test_hallucinated_file_dropped(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    raw = ('{"summary":"x","edits":[{"id":1,"file":"src/does_not_exist.py",'
           '"old_block":"whatever","new_block":"y","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [_hit()], PID)

    assert edits == []
    assert dropped == 1


def test_forbidden_git_path_dropped(data_dir):
    _write_repo_file(".git/config", "[core]\n")
    hit = _hit(file=".git/config")
    raw = ('{"summary":"x","edits":[{"id":1,"file":".git/config",'
           '"old_block":"[core]","new_block":"[evil]","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [hit], PID)

    assert edits == []
    assert dropped == 1


def test_forbidden_workflow_path_dropped(data_dir):
    _write_repo_file(".github/workflows/ci.yml", "on: push\n")
    hit = _hit(file=".github/workflows/ci.yml")
    raw = ('{"summary":"x","edits":[{"id":1,"file":".github/workflows/ci.yml",'
           '"old_block":"on: push","new_block":"on: push\\n  # pwned","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [hit], PID)

    assert edits == []
    assert dropped == 1


def test_traversal_path_dropped(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    hit = _hit(file="../../etc/passwd")
    raw = ('{"summary":"x","edits":[{"id":1,"file":"../../etc/passwd",'
           '"old_block":"root","new_block":"pwn","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [hit], PID)

    assert edits == []
    assert dropped == 1


def test_non_verbatim_old_block_dropped(data_dir):
    # old_block не является дословной подстрокой файла (значение отличается) → отбрасываем.
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    raw = ('{"summary":"x","edits":[{"id":1,"file":"src/util.py",'
           '"old_block":"    return 42","new_block":"    return 2","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [_hit()], PID)

    assert edits == []
    assert dropped == 1


def test_ambiguous_old_block_dropped(data_dir):
    # old_block встречается дважды → неоднозначно, какое место править → fail-closed отбрасываем.
    _write_repo_file("src/util.py", "def a():\n    return 1\n\ndef b():\n    return 1\n")
    hit = _hit(start=1, end=5)
    raw = ('{"summary":"x","edits":[{"id":1,"file":"src/util.py",'
           '"old_block":"    return 1","new_block":"    return 2","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [hit], PID)

    assert edits == []
    assert dropped == 1


def test_duplicate_edits_deduped(data_dir):
    _write_repo_file("src/util.py", "def f():\n    return 1\n")
    raw = ('{"summary":"x","edits":['
           '{"id":1,"file":"src/util.py","old_block":"    return 1","new_block":"    return 2","reason":"r"},'
           '{"id":1,"file":"src/util.py","old_block":"    return 1","new_block":"    return 2","reason":"r"}]}')

    _summary, edits, dropped = patcher.parse_and_validate_edits(raw, [_hit()], PID)

    assert len(edits) == 1
    assert dropped == 1


def test_malformed_json_yields_nothing(data_dir):
    summary, edits, dropped = patcher.parse_and_validate_edits("не json", [_hit()], PID)

    assert summary == ""
    assert edits == []


# --- assemble_diff + check_apply ---

def test_assemble_and_check_apply_ok(data_dir):
    _git_repo({"src/util.py": "def f():\n    return 1\n"})
    edits = [{"file": "src/util.py", "old_block": "    return 1",
              "new_block": "    return 2", "reason": "r", "citation": "c"}]

    diff = patcher.assemble_diff(edits, PID)

    assert "-    return 1" in diff
    assert "+    return 2" in diff
    assert patcher.check_apply(PID, diff) is True


def test_check_apply_rejects_dirty_patch(data_dir):
    _git_repo({"src/util.py": "def f():\n    return 1\n"})
    # Патч ссылается на строку, которой в файле нет → git apply --check должен отвергнуть.
    bad_diff = (
        "--- a/src/util.py\n+++ b/src/util.py\n"
        "@@ -1,2 +1,2 @@\n def f():\n-    return 999\n+    return 2\n"
    )

    assert patcher.check_apply(PID, bad_diff) is False


def test_empty_diff_is_not_applicable(data_dir):
    _git_repo({"src/util.py": "def f():\n    return 1\n"})

    assert patcher.check_apply(PID, "") is False


def test_new_block_secret_is_redacted_in_diff(data_dir):
    # Синтетический high-entropy токен в new_block не должен утечь в показанный diff.
    _git_repo({"src/util.py": "def f():\n    return 1\n"})
    secret = "ZmFrZXNlY3JldDEyMzQ1Njc4OTBhYmNkZWZnaGlqa2xtbg"
    edits = [{"file": "src/util.py", "old_block": "    return 1",
              "new_block": f"    return '{secret}'", "reason": "r", "citation": "c"}]

    diff = patcher.assemble_diff(edits, PID)

    assert secret not in diff
    assert "[REDACTED]" in diff


def test_no_matching_old_block_yields_empty_diff(data_dir):
    _git_repo({"src/util.py": "def f():\n    return 1\n"})
    edits = [{"file": "src/util.py", "old_block": "nonexistent line",
              "new_block": "x", "reason": "r", "citation": "c"}]

    diff = patcher.assemble_diff(edits, PID)

    assert diff == ""
    assert patcher.check_apply(PID, diff) is False
