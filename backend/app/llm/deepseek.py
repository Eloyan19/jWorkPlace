"""DeepSeek-адаптер LlmService.

Этап 0: только заготовка. Ключ сохраняется из Settings (уже читанного из env),
но никакого httpx-клиента не создаём и в сеть не ходим — поведение (chat/complete,
tool-loop, JSON-режим grounding) реализуется на Этапе 2/4.

Инвариант: DEEPSEEK_API_KEY нигде не логируется и не попадает в контекст LLM —
здесь он лишь хранится в приватном атрибуте до появления реального HTTP-вызова.
"""
from app.config import Settings
from app.llm.base import LlmService


class DeepSeekLlmService(LlmService):
    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.deepseek_api_key

    async def chat(self, messages: list[dict]) -> str:
        raise NotImplementedError

    async def complete(self, prompt: str) -> str:
        raise NotImplementedError


def get_llm(settings: Settings) -> LlmService:
    """Фабрика провайдера по Settings.llm_provider. Пока никем не вызывается из роутеров."""
    if settings.llm_provider == "deepseek":
        return DeepSeekLlmService(settings)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
