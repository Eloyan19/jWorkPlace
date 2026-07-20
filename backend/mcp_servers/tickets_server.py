"""MCP-сервер тикетов/пользователей (Задание 2) — реальный Model Context Protocol поверх JSON.

Отдельный процесс, транспорт stdio (не HTTP → нет сетевого порта и SSRF-поверхности). Экспонирует
ТОЛЬКО read-инструменты — никаких write/exec: поддержке нужен контекст обращения, не изменение CRM.
Источник данных — JSON по пути `JWP_TICKETS_PATH` (backend передаёт его в env при запуске сервера);
по умолчанию — синтетический `app/support/data/tickets.json` рядом в пакете.

Запуск (обычно поднимается backend'ом как дочерний stdio-процесс, см. app/support/mcp_client.py):
    python backend/mcp_servers/tickets_server.py
"""
import json
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("jworkplace-tickets")

_DEFAULT_DB = Path(__file__).resolve().parent.parent / "app" / "support" / "data" / "tickets.json"


def _load() -> dict:
    path = Path(os.environ.get("JWP_TICKETS_PATH", str(_DEFAULT_DB)))
    if not path.is_file():
        return {"users": [], "tickets": []}
    return json.loads(path.read_text(encoding="utf-8"))


@mcp.tool()
def get_ticket(ticket_id: str) -> dict:
    """Вернуть один тикет по id (или {} если не найден). Read-only."""
    data = _load()
    for t in data.get("tickets", []):
        if t.get("id") == ticket_id:
            return t
    return {}


@mcp.tool()
def list_tickets(user_id: str) -> list[dict]:
    """Тикеты одного пользователя (по user_id). Read-only. Без user_id — пусто: сервер НЕ отдаёт
    полный дамп тикетов (least-privilege, чтобы недостижимый путь не стал PII-дампом всей CRM)."""
    if not user_id:
        return []
    return [t for t in _load().get("tickets", []) if t.get("user_id") == user_id]


@mcp.tool()
def get_user(user_id: str) -> dict:
    """Вернуть пользователя по id (или {} если не найден). Read-only."""
    for u in _load().get("users", []):
        if u.get("id") == user_id:
            return u
    return {}


if __name__ == "__main__":
    mcp.run(transport="stdio")
