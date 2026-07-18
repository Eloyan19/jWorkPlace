"""DeepSeek-адаптер LlmService.

Реальный HTTP-вызов к OpenAI-совместимому API DeepSeek. Ключ читается из Settings один раз
в конструкторе и попадает **только** в заголовок Authorization — никогда в query-параметры,
логи или исключения (см. CLAUDE.md «Безопасность и секреты»).

Модель — фиксированно `deepseek-chat`: `deepseek-reasoner`/thinking-mode несовместим с
tools/JSON-режимом (используется ролью-роем и grounded-генерацией на Этапе 2b/4).
"""
import logging

import httpx

from app.config import Settings
from app.llm.base import LlmService

logger = logging.getLogger("jworkplace.llm")

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"

# connect=10s (быстро отличить недоступность сети от медленной генерации), общий потолок 60s.
_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class LlmError(Exception):
    """Сигнал вызывающему коду: генерация не удалась.

    Несёт только человекочитаемую причину, безопасную для лога/ответа клиенту — никогда
    repr(exc)/URL исходного запроса (в них не должно быть секретов, но лишний риск ни к чему).
    """


class DeepSeekLlmService(LlmService):
    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.deepseek_api_key

    async def chat(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        if not self._api_key:
            raise LlmError("DEEPSEEK_API_KEY не задан")

        content, finish_reason = await self._request(messages, response_format, temperature, max_tokens)
        if finish_reason == "length":
            # Обрезанный ответ (обычно ломает JSON) — один retry с бОльшим бюджетом токенов.
            content, finish_reason = await self._request(
                messages, response_format, temperature, max_tokens * 2
            )
            if finish_reason == "length":
                raise LlmError("ответ DeepSeek обрезан по длине дважды подряд")
        return content

    async def complete(self, prompt: str) -> str:
        return await self.chat([{"role": "user", "content": prompt}])

    async def _request(
        self,
        messages: list[dict],
        response_format: dict | None,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, str | None]:
        payload: dict = {
            "model": DEEPSEEK_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    DEEPSEEK_API_URL,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            # Статус-код — не секрет, но тело/заголовки ответа могут его эхом вернуть при 4xx —
            # логируем только код, не resp.text.
            logger.error("DeepSeek API вернул ошибку: HTTP %d", exc.response.status_code)
            raise LlmError(f"DeepSeek API ошибка: HTTP {exc.response.status_code}") from None
        except httpx.HTTPError:
            logger.error("DeepSeek API недоступен (сетевая ошибка)")
            raise LlmError("DeepSeek API недоступен") from None
        except ValueError:
            logger.error("DeepSeek API вернул невалидный JSON")
            raise LlmError("DeepSeek API вернул невалидный ответ") from None

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError):
            raise LlmError("DeepSeek API вернул неожиданный формат ответа") from None
        return content, finish_reason


def get_llm(settings: Settings) -> LlmService:
    """Фабрика провайдера по Settings.llm_provider."""
    if settings.llm_provider == "deepseek":
        return DeepSeekLlmService(settings)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
