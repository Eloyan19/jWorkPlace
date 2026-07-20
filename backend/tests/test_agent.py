"""Тесты файлового tool-агента (Задание 3): исполнители/guard'ы, tool-loop, сборка diff,
human-confirm PR. LLM (chat_raw) мокается сценарием tool_calls; git-репо для check_apply — реальный.
"""
import json
import subprocess

from starlette.testclient import TestClient

from app import db
from app.agent import loop, tools
from app.api import agent as agent_api
from app.config import get_settings
from app.edit import patcher
from app.main import create_app

PID = "abc123def456"


# --- утилиты ---

def _client():
    return TestClient(create_app())


def _ready(url="https://github.com/o/r"):
    db.create_project(PID, url, "o/r", db.STATUS_READY)


def _git_repo(files: dict):
    repo = get_settings().repos_dir / PID
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for path, content in files.items():
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)


def _call(cid, name, args):
    return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _resp(tool_calls=None, content=None, finish_reason=None):
    return {
        "content": content, "tool_calls": tool_calls,
        "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
    }


class FakeLlm:
    def __init__(self, responses):
        self.responses = list(responses)
        self.i = 0
        self.calls = 0

    async def chat_raw(self, messages, *, tools=None, tool_choice="auto", temperature=0.0, max_tokens=1024):
        self.calls += 1
        r = self.responses[min(self.i, len(self.responses) - 1)]
        self.i += 1
        return r


def _hit(file="src/util.py", text="def striptags(s):\n    return s\n"):
    return {
        "file": file, "symbol": "striptags", "symbol_kind": "function_definition", "lang": "python",
        "start_line": 1, "end_line": 2, "citation": f"{file}::striptags::L1-2", "text": text,
        "dense_score": 0.8, "bm25_score": None, "rrf_score": 0.5,
    }


# --- guard'ы исполнителей (без LLM) ---

def test_write_allowed_rejects_non_md_and_forbidden(data_dir):
    _git_repo({"a.py": "x\n"})
    st = tools.AgentState(PID)
    assert tools._write_allowed(PID, "src/new.py")[0] is False        # не .md
    assert tools._write_allowed(PID, ".github/x.md")[0] is False       # весь .github запрещён
    assert tools._write_allowed(PID, ".git/x.md")[0] is False
    assert tools._write_allowed(PID, "../escape.md")[0] is False       # traversal
    assert tools._write_allowed(PID, "a.py")[0] is False               # существующий (и не .md)
    ok, _ = tools._write_allowed(PID, "docs/NEW.md")
    assert ok is True                                                  # новый .md — можно


def test_write_file_rejects_existing_md(data_dir):
    _git_repo({"README.md": "# repo\n"})
    st = tools.AgentState(PID)
    res = tools._tool_write_file(st, {"path": "README.md", "content": "hijack"})
    assert "отклонено" in res
    assert st.staged_writes == []


def test_propose_patch_requires_known_file(data_dir):
    _git_repo({"src/util.py": "def striptags(s):\n    return s\n"})
    st = tools.AgentState(PID)
    # файл не прочитан → отклонено
    res = tools._tool_propose_patch(st, {"file": "src/util.py", "old_block": "return s", "new_block": "return s.strip()"})
    assert "отклонено" in res and st.staged_edits == []
    # после read_file — принимается
    tools._tool_read_file(st, {"file": "src/util.py", "start_line": 1, "end_line": 2})
    res2 = tools._tool_propose_patch(st, {"file": "src/util.py", "old_block": "    return s\n", "new_block": "    return s.strip()\n"})
    assert res2 == "правка принята" and len(st.staged_edits) == 1


def test_propose_patch_rejects_ambiguous_old_block(data_dir):
    _git_repo({"src/a.py": "x = 1\nx = 1\n"})
    st = tools.AgentState(PID)
    tools._tool_read_file(st, {"file": "src/a.py", "start_line": 1, "end_line": 2})
    res = tools._tool_propose_patch(st, {"file": "src/a.py", "old_block": "x = 1\n", "new_block": "x = 2\n"})
    assert "отклонено" in res                                         # встречается дважды


def test_propose_patch_rejects_github_non_workflow(data_dir):
    """CODEOWNERS/dependabot/composite-actions в .github/ — тоже CI-поверхность: агент их не правит
    (паритет с write_file; строже, чем patcher._forbidden для /edit)."""
    _git_repo({".github/CODEOWNERS": "* @owner\n"})
    st = tools.AgentState(PID)
    tools._tool_read_file(st, {"file": ".github/CODEOWNERS", "start_line": 1, "end_line": 1})
    res = tools._tool_propose_patch(st, {"file": ".github/CODEOWNERS", "old_block": "* @owner\n", "new_block": "* @attacker\n"})
    assert "отклонено" in res and st.staged_edits == []


def test_execute_tool_dedup_reads(data_dir):
    _git_repo({"src/util.py": "def f():\n    pass\n"})
    st = tools.AgentState(PID)
    r1 = tools.execute_tool(st, "read_file", {"file": "src/util.py", "start_line": 1, "end_line": 2})
    r2 = tools.execute_tool(st, "read_file", {"file": "src/util.py", "start_line": 1, "end_line": 2})
    assert "уже запрашивалось" in r2 and "def f" in r1


# --- tool-loop через эндпоинт ---

def test_agent_readonly_task_no_pr(data_dir, monkeypatch):
    _ready()
    _git_repo({"src/util.py": "def striptags(s):\n    return s\n"})
    monkeypatch.setattr(loop.tools.hybrid, "hybrid_search", lambda pid, q, k: [_hit()])
    fake = FakeLlm([
        _resp([_call("1", "search_code", {"query": "striptags"})]),
        _resp([_call("2", "finish", {"result_text": "Используется в src/util.py", "needs_pr": False})]),
    ])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)

    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "найди использования striptags"})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_pr"] is False
    assert "src/util.py" in body["result_text"]
    assert "run_id" not in body


def test_agent_write_file_produces_pr_preview(data_dir, monkeypatch):
    _ready()
    _git_repo({"src/util.py": "def f():\n    pass\n"})
    fake = FakeLlm([
        _resp([_call("1", "write_file", {"path": "CHANGELOG.md", "content": "# Changelog\n\n- добавлено\n"})]),
        _resp([_call("2", "finish", {"result_text": "Создал CHANGELOG.md", "needs_pr": True})]),
    ])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)

    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "сгенерируй changelog"})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_pr"] is True
    assert body["run_id"]
    assert body["can_edit"] is False           # токен не привязан
    assert "CHANGELOG.md" in body["diff"]
    assert patcher.check_apply(PID, body["diff"]) is True


def test_agent_edit_produces_applicable_diff(data_dir, monkeypatch):
    _ready()
    _git_repo({"src/util.py": "def striptags(s):\n    return s\n"})
    monkeypatch.setattr(loop.tools.hybrid, "hybrid_search", lambda pid, q, k: [_hit()])
    fake = FakeLlm([
        _resp([_call("1", "search_code", {"query": "striptags"})]),
        _resp([_call("2", "propose_patch", {
            "file": "src/util.py", "old_block": "    return s\n",
            "new_block": "    return s.strip()\n", "reason": "trim",
        })]),
        _resp([_call("3", "finish", {"result_text": "Поправил striptags", "needs_pr": True})]),
    ])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)

    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "обрежь пробелы в striptags"})
    body = r.json()
    assert body["needs_pr"] is True
    assert "s.strip()" in body["diff"]
    assert patcher.check_apply(PID, body["diff"]) is True


def test_agent_empty_goal_400(data_dir):
    _ready()
    assert _client().post(f"/api/projects/{PID}/agent", json={"goal": "   "}).status_code == 400


def test_agent_unknown_project_404(data_dir):
    assert _client().post(f"/api/projects/{PID}/agent", json={"goal": "x"}).status_code == 404


def test_agent_stops_when_no_finish_tool(data_dir, monkeypatch):
    """Модель болтает текстом без finish → одно напоминание, затем стоп с этим текстом (не зависает)."""
    _ready()
    _git_repo({"a.py": "x\n"})
    fake = FakeLlm([_resp(content="просто текст без инструмента", finish_reason="stop")])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)
    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "вопрос"})
    body = r.json()
    assert body["needs_pr"] is False
    assert fake.calls == 2                      # ответ + напоминание, дальше стоп


# --- human-confirm PR ---

def test_confirm_without_token_403(data_dir, monkeypatch):
    _ready()
    _git_repo({"src/util.py": "def f():\n    pass\n"})
    fake = FakeLlm([
        _resp([_call("1", "write_file", {"path": "docs/ADR-1.md", "content": "# ADR 1\n\nрешение\n"})]),
        _resp([_call("2", "finish", {"result_text": "ADR", "needs_pr": True})]),
    ])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)
    run = _client().post(f"/api/projects/{PID}/agent", json={"goal": "сделай ADR"}).json()

    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "", "confirm": True, "run_id": run["run_id"]})
    assert r.status_code == 403


def test_confirm_bad_run_id_409(data_dir):
    _ready()
    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "", "confirm": True, "run_id": "nope"})
    assert r.status_code == 409


def test_confirm_opens_pr_with_token(data_dir, monkeypatch):
    _ready()
    _git_repo({"src/util.py": "def f():\n    pass\n"})
    db.set_project_token(PID, b"enc-token-blob")     # проект «с правками»
    fake = FakeLlm([
        _resp([_call("1", "write_file", {"path": "docs/NOTES.md", "content": "# заметки\n\nтекст\n"})]),
        _resp([_call("2", "finish", {"result_text": "готово", "needs_pr": True})]),
    ])
    monkeypatch.setattr(loop, "get_llm", lambda s: fake)
    monkeypatch.setattr(agent_api.github, "decrypt_token", lambda settings, enc: "ghp_test")
    monkeypatch.setattr(agent_api.github, "open_pr", lambda *a, **k: "https://github.com/o/r/pull/7")

    run = _client().post(f"/api/projects/{PID}/agent", json={"goal": "заметки"}).json()
    assert run["can_edit"] is True
    r = _client().post(f"/api/projects/{PID}/agent", json={"goal": "", "confirm": True, "run_id": run["run_id"]})
    body = r.json()
    assert body["ok"] is True
    assert body["pr_url"] == "https://github.com/o/r/pull/7"
