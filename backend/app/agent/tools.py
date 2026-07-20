"""Инструменты файлового tool-агента (Задание 3, MVP Слоя B) + состояние прогона.

Один агент DeepSeek function-calling САМ комбинирует read-only инструменты (search_code/read_file/
list_files) и изменяющие (propose_patch — правка существующего кода; write_file — ТОЛЬКО новые
markdown-файлы: docs/CHANGELOG/ADR), затем finish. Изменения не пишутся на диск: они копятся в
состоянии и в конце собираются в ЕДИНЫЙ diff (patcher.assemble_full_diff) → git apply --check →
human-confirm → open_pr (как /pr Этапа 3b).

Инварианты безопасности (security-auditor):
- Результаты инструментов — НЕДОВЕРЕННЫЕ данные (redact на каждый выход, анти-инъекция в промпте).
- Каждый tool-вход через traversal-guard (`safe_repo_path`); propose_patch — file ∈ прочитанного
  (hit_pool/read_files, антигаллюцинация); write_file — только НОВЫЕ .md вне `.git/`/`.github/`.
- Секреты (PAT/ключ LLM) в tool-namespace не попадают; open_pr берёт токен серверно, не из модели.
"""
import fnmatch
import logging

from app.chat.grounding import redact, safe_repo_path
from app.edit import patcher
from app.indexing import hybrid

logger = logging.getLogger("jworkplace.agent.tools")

_MAX_READ_LINES = 400        # окно read_file (защита контекста)
_MAX_SEARCH_K = 10
_MAX_LIST = 200
_SNIPPET_CHARS = 300         # обрезка тела hit в выдаче search_code

# Запрет для ВСЕХ изменяющих инструментов агента (propose_patch и write_file): git-внутренности и
# ВЕСЬ .github/ — не только workflows, но и CODEOWNERS/dependabot.yml/composite-actions (тоже
# CI-поверхность, исполняются в CI). Строже, чем patcher._forbidden (тот — для /edit Этапа 3a).
_AGENT_FORBIDDEN_PREFIXES = (".git/", ".github/")


def _forbidden_path(path: str) -> bool:
    norm = path.lstrip("/")
    return any(norm.startswith(p) for p in _AGENT_FORBIDDEN_PREFIXES)


# --- JSON-схемы инструментов (OpenAI-совместимый function-calling) ---

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_code",
        "description": "Найти релевантные фрагменты кода проекта по запросу (гибридный поиск). "
                       "Возвращает список источников file::symbol::строки с фрагментом.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "что искать (идентификатор, поведение, тема)"},
            "k": {"type": "integer", "description": "сколько результатов (1..10)"},
        }, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Прочитать диапазон строк файла проекта (до 400 строк за вызов).",
        "parameters": {"type": "object", "properties": {
            "file": {"type": "string", "description": "путь файла в репозитории"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        }, "required": ["file", "start_line", "end_line"]},
    }},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "Список путей файлов проекта, опционально по glob-шаблону (напр. src/**/*.py).",
        "parameters": {"type": "object", "properties": {
            "glob": {"type": "string", "description": "glob-шаблон, по умолчанию все файлы"},
        }, "required": []},
    }},
    {"type": "function", "function": {
        "name": "propose_patch",
        "description": "Предложить точечную правку СУЩЕСТВУЮЩЕГО файла: заменить дословный old_block "
                       "на new_block. Файл должен быть заранее прочитан (search_code/read_file).",
        "parameters": {"type": "object", "properties": {
            "file": {"type": "string"},
            "old_block": {"type": "string", "description": "дословный непрерывный кусок из файла (уникальный)"},
            "new_block": {"type": "string", "description": "чем заменить (пусто — удалить)"},
            "reason": {"type": "string"},
        }, "required": ["file", "old_block", "new_block"]},
    }},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Создать НОВЫЙ markdown-файл (документация/ADR/CHANGELOG). Только .md, только "
                       "несуществующий путь, не в .git/ и не в .github/. Существующий код правь через propose_patch.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "путь нового .md файла"},
            "content": {"type": "string"},
        }, "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Завершить задачу. result_text — итог для пользователя. needs_pr=true, если "
                       "нужно открыть Pull Request с накопленными правками; false — задача только на чтение/анализ.",
        "parameters": {"type": "object", "properties": {
            "result_text": {"type": "string"},
            "needs_pr": {"type": "boolean"},
        }, "required": ["result_text", "needs_pr"]},
    }},
]


class AgentState:
    """Состояние одного прогона агента: накопленные знания и предложенные изменения."""

    def __init__(self, project_id: str) -> None:
        self.project_id = project_id
        self.hit_pool: dict[str, dict] = {}     # citation -> hit (allowlist файлов для propose_patch)
        self.read_files: set[str] = set()       # файлы, реально прочитанные (доп. allowlist)
        self.staged_edits: list[dict] = []       # валидные правки существующих файлов
        self.staged_writes: list[dict] = []      # [{path, content}] новые файлы
        self.tool_cache: dict[str, str] = {}     # дедуп read/search (ключ name+args)
        self.patch_rejections = 0
        self.finished = False
        self.result_text = ""
        self.needs_pr = False

    @property
    def known_files(self) -> set[str]:
        return {h["file"] for h in self.hit_pool.values()} | self.read_files


# --- исполнители ---

def _tool_search_code(state: AgentState, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "ошибка: пустой query"
    k = min(int(args.get("k", 6) or 6), _MAX_SEARCH_K)
    try:
        hits = hybrid.hybrid_search(state.project_id, query, k)
    except ValueError as exc:
        return f"ошибка поиска: {exc}"
    if not hits:
        return "ничего не найдено"
    lines = []
    for h in hits:
        state.hit_pool[h["citation"]] = h        # копим allowlist
        snippet = redact(h.get("text", ""))[:_SNIPPET_CHARS]
        lines.append(f"[{h['citation']}]\n{snippet}")
    return "\n\n".join(lines)


def _tool_read_file(state: AgentState, args: dict) -> str:
    file = str(args.get("file", "")).strip()
    try:
        start = int(args.get("start_line", 1))
        end = int(args.get("end_line", start))
    except (TypeError, ValueError):
        return "ошибка: start_line/end_line должны быть числами"
    view = patcher._file_view(state.project_id, file)   # redacted-проекция + traversal-guard
    if view is None:
        return "ошибка: файл недоступен или вне репозитория"
    all_lines = view.splitlines(keepends=True)
    if start < 1:
        start = 1
    end = min(end, start + _MAX_READ_LINES - 1, len(all_lines))
    if start > len(all_lines):
        return "ошибка: start_line за пределами файла"
    state.read_files.add(file)
    body = "".join(all_lines[start - 1:end])
    return f"{file} L{start}-{end}:\n{body}"


def _tool_list_files(state: AgentState, args: dict) -> str:
    from app import db
    glob = str(args.get("glob", "") or "").strip()
    paths = [r["path"] for r in db.indexable_files(state.project_id)]
    if glob:
        paths = [p for p in paths if fnmatch.fnmatch(p, glob)]
    paths = sorted(paths)[:_MAX_LIST]
    if not paths:
        return "нет файлов по шаблону"
    return "\n".join(paths)


def _tool_propose_patch(state: AgentState, args: dict) -> str:
    file = str(args.get("file", "")).strip()
    old_block = str(args.get("old_block", ""))
    new_block = str(args.get("new_block", ""))
    reason = str(args.get("reason", "")).strip()

    if not file or file not in state.known_files:
        state.patch_rejections += 1
        return "отклонено: сначала прочитай файл через search_code/read_file"
    if _forbidden_path(file):
        state.patch_rejections += 1
        return "отклонено: этот путь править нельзя (.git/.github)"
    view = patcher._file_view(state.project_id, file)
    if view is None:
        state.patch_rejections += 1
        return "отклонено: файл недоступен"
    if not old_block or view.count(old_block) != 1:
        state.patch_rejections += 1
        return "отклонено: old_block должен встречаться в файле РОВНО один раз и дословно"
    if any(e["file"] == file and e["old_block"] == old_block for e in state.staged_edits):
        return "уже добавлено"
    citation = next((c for c, h in state.hit_pool.items() if h["file"] == file), file)
    state.staged_edits.append({
        "file": file, "old_block": old_block, "new_block": new_block,
        "reason": reason, "citation": citation,
    })
    return "правка принята"


def _write_allowed(project_id: str, path: str) -> tuple[bool, str]:
    """write_file: только НОВЫЙ .md вне .git/.github. Traversal + symlink-guard через safe_repo_path
    (резолвит родителя; путь вне клона → None). Существующий файл на диске → отказ (это не новый)."""
    norm = path.lstrip("/")
    if _forbidden_path(norm):
        return False, "путь запрещён (.git/.github)"
    if not norm.lower().endswith(".md"):
        return False, "write_file разрешён только для .md (доку/ADR/changelog); код правь через propose_patch"
    resolved = safe_repo_path(project_id, norm)
    if resolved is None:
        return False, "путь вне репозитория"
    if resolved.exists():
        return False, "файл уже существует — новые файлы только; правь существующий через propose_patch"
    return True, ""


def _tool_write_file(state: AgentState, args: dict) -> str:
    path = str(args.get("path", "")).strip().lstrip("/")
    content = str(args.get("content", ""))
    ok, reason = _write_allowed(state.project_id, path)
    if not ok:
        state.patch_rejections += 1
        return f"отклонено: {reason}"
    if not content.strip():
        return "отклонено: пустой content"
    if any(w["path"] == path for w in state.staged_writes):
        return "уже добавлено"
    state.staged_writes.append({"path": path, "content": content})
    return "новый файл принят"


def _tool_finish(state: AgentState, args: dict) -> str:
    state.finished = True
    state.result_text = str(args.get("result_text", "")).strip()
    state.needs_pr = bool(args.get("needs_pr", False))
    return "готово"


_DISPATCH = {
    "search_code": _tool_search_code,
    "read_file": _tool_read_file,
    "list_files": _tool_list_files,
    "propose_patch": _tool_propose_patch,
    "write_file": _tool_write_file,
    "finish": _tool_finish,
}

# Инструменты, чей результат кэшируем для дедупа (детерминированы по аргументам).
_CACHEABLE = {"search_code", "read_file", "list_files"}


def execute_tool(state: AgentState, name: str, args: dict) -> str:
    """Выполнить инструмент. Результат redacted (второй барьер) + дедуп read/search."""
    fn = _DISPATCH.get(name)
    if fn is None:
        return f"ошибка: неизвестный инструмент {name}"
    cache_key = f"{name}:{sorted(args.items())}" if name in _CACHEABLE else None
    if cache_key and cache_key in state.tool_cache:
        return f"(уже запрашивалось ранее)\n{state.tool_cache[cache_key]}"
    try:
        result = fn(state, args)
    except Exception:
        logger.exception("сбой инструмента %s", name)
        return "ошибка: внутренний сбой инструмента"
    result = redact(result)
    if cache_key:
        state.tool_cache[cache_key] = result
    return result
