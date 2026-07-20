"""MCP-клиент поддержки (Задание 2): backend как Model Context Protocol клиент к серверу тикетов.

Поднимает `mcp_servers/tickets_server.py` как дочерний stdio-процесс, вызывает read-only tools
(get_ticket/get_user) и возвращает контекст обращения. Транспорт stdio — сервер не слушает сеть,
SSRF-поверхности нет. Fail-closed: любая ошибка/таймаут/невалидный id → None (поддержка ответит
по FAQ без контекста тикета, но не упадёт и не подвесит запрос).

Инвариант границы: id тикета/пользователя валидируем allowlist-регуляркой ДО запроса (не доверяем
модели и клиенту передавать произвольные строки в MCP). Ответ сервера прогоняем через redact.
"""
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.chat import grounding
from app.config import get_settings

logger = logging.getLogger("jworkplace.support.mcp")

# Разрешённый формат id (T-1001, u-101 …): буквы/цифры/дефис/подчёркивание, до 40 символов.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

_SERVER_PATH = Path(__file__).resolve().parent.parent.parent / "mcp_servers" / "tickets_server.py"

_TIMEOUT_S = 15

# Потолок одновременных MCP-subprocess'ов: каждый запрос поднимает python+FastMCP; на 3.8 ГБ RAM
# серия параллельных запросов иначе истощит память. Ограничиваем конкуренцию (лишние ждут).
_MAX_CONCURRENCY = 2
_semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)


def _valid_id(value: str | None) -> bool:
    return bool(value) and bool(_ID_RE.match(value))


def _tickets_path() -> str:
    """JSON тикетов: файл в data-dir (если пользователь положил свой), иначе дефолт сервера."""
    override = get_settings().support_dir / "tickets.json"
    return str(override) if override.is_file() else ""


def _server_params() -> StdioServerParameters:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/root")}
    override = _tickets_path()
    if override:
        env["JWP_TICKETS_PATH"] = override
    return StdioServerParameters(command=sys.executable, args=[str(_SERVER_PATH)], env=env)


def _tool_dict(result) -> dict:
    """Извлечь dict из CallToolResult: structuredContent (FastMCP оборачивает в {'result': ...})
    либо первый текстовый блок как JSON. Не dict → {}."""
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result", structured)
        return inner if isinstance(inner, dict) else {}
    import json
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                data = json.loads(text)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _redact_ticket(ctx: dict) -> dict:
    """Прогнать текстовые поля тикета/пользователя через redact (второй барьер секретов)."""
    for section in ("ticket", "user"):
        obj = ctx.get(section)
        if isinstance(obj, dict):
            ctx[section] = {k: grounding.redact(v) if isinstance(v, str) else v for k, v in obj.items()}
    return ctx


async def _fetch(ticket_id: str | None, user_id: str | None) -> dict | None:
    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            ctx: dict = {}
            if _valid_id(ticket_id):
                ticket = _tool_dict(await session.call_tool("get_ticket", {"ticket_id": ticket_id}))
                if ticket:
                    ctx["ticket"] = ticket
                    uid = ticket.get("user_id")
                    if _valid_id(uid):
                        user = _tool_dict(await session.call_tool("get_user", {"user_id": uid}))
                        if user:
                            ctx["user"] = user
            elif _valid_id(user_id):
                user = _tool_dict(await session.call_tool("get_user", {"user_id": user_id}))
                if user:
                    ctx["user"] = user
            return _redact_ticket(ctx) if ctx else None


async def fetch_ticket_context(ticket_id: str | None, user_id: str | None) -> dict | None:
    """Контекст обращения через MCP (или None). Fail-closed на ошибке/таймауте/невалидном id."""
    if not _valid_id(ticket_id) and not _valid_id(user_id):
        return None
    try:
        async with _semaphore:
            return await asyncio.wait_for(_fetch(ticket_id, user_id), timeout=_TIMEOUT_S)
    except Exception:
        logger.warning("MCP-запрос тикета не удался (fail-closed, отвечаем по FAQ без контекста)")
        return None
