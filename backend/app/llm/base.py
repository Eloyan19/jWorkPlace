"""Провайдер-абстракция LLM.

DeepSeek — первая реализация (см. deepseek.py), но бизнес-логика выше по стеку
не должна знать про DeepSeek-специфику: она работает только через этот интерфейс.
Это даёт возможность позже добавить Claude/GPT/локальный Ollama без правок вызывающего кода.
"""
from abc import ABC, abstractmethod


class LlmService(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict]) -> str:
        """Диалоговый вызов: список сообщений {role, content} -> текст ответа."""
        raise NotImplementedError

    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Одиночный promt-комплишн -> текст ответа."""
        raise NotImplementedError
