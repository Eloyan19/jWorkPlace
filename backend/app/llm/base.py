"""Провайдер-абстракция LLM.

DeepSeek — первая реализация (см. deepseek.py), но бизнес-логика выше по стеку
не должна знать про DeepSeek-специфику: она работает только через этот интерфейс.
Это даёт возможность позже добавить Claude/GPT/локальный Ollama без правок вызывающего кода.
"""
from abc import ABC, abstractmethod


class LlmService(ABC):
    @abstractmethod
    async def chat(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ) -> str:
        """Диалоговый вызов: список сообщений {role, content} -> текст ответа.

        response_format={"type": "json_object"} — JSON-режим (grounded-ответы, Этап 2b):
        промпт обязан упоминать слово "json" (требование DeepSeek API). max_tokens — бюджет
        на ответ; реализация может увеличить его на внутренний retry при обрезанном ответе.
        """
        raise NotImplementedError

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Одиночный promt-комплишн -> текст ответа."""
        raise NotImplementedError
