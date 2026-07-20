"""Эндпоинт ассистента поддержки (Задание 2): POST /api/support/ask.

Глобальный (не привязан к проекту): отвечает на вопросы о продукте jWorkPlace по FAQ-корпусу, с
опциональным контекстом тикета из MCP. Поток: (по ticket_id/user_id) MCP get_ticket → qa.answer
(retrieve FAQ → гейт эскалации → генерация → валидация). Контекст тикета — недоверенные данные
(qa.build_ticket_block оборачивает в делимитеры + redact). Fail-closed: сбой MCP → ответ по FAQ.
"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.support import mcp_client, qa

logger = logging.getLogger("jworkplace.support")

router = APIRouter(prefix="/api/support")


class SupportRequest(BaseModel):
    # Как у остальных интерактивных запросов — короткий ввод; огромный текст отсекаем до LLM.
    question: str = Field(max_length=2000)
    ticket_id: str | None = None
    user_id: str | None = None


@router.post("/ask")
async def support_ask(req: SupportRequest) -> dict:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Пустой вопрос.")

    ticket_ctx = await mcp_client.fetch_ticket_context(req.ticket_id, req.user_id)

    try:
        result = await qa.answer(question, ticket_ctx)
    except Exception:
        logger.exception("сбой ассистента поддержки")
        raise HTTPException(status_code=500, detail="внутренняя ошибка")

    # Отдаём флаг, был ли учтён тикет (без содержимого — фронту хватит факта).
    result["ticket_applied"] = ticket_ctx is not None
    return result
