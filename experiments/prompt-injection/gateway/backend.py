"""LLM-бэкенд для гейтвея. Переиспользуем харнесс трека (../llm_backend.py):
real DeepSeek через project-адаптер ИЛИ детерминированный mock-фолбэк.

Контракт бэкенда для гейтвея — один метод: `.ask(system: str, user: str) -> str`.
Тесты подменяют его `StubBackend`, чтобы прогон был воспроизводимым и без сети.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

_TRACK_DIR = Path(__file__).resolve().parent.parent  # experiments/prompt-injection/
# В КОНЕЦ sys.path (нужен только ради llm_backend): вставка в начало затеняла бы одноимённые
# локальные модули gateway (напр. run.py есть и тут, и в родительском треке).
if str(_TRACK_DIR) not in sys.path:
    sys.path.append(str(_TRACK_DIR))


class Backend(Protocol):
    def ask(self, system: str, user: str) -> str: ...


class OllamaBackend:
    """Слабая ЛОКАЛЬНАЯ LLM (Ollama :11434) как бэкенд. Маленькие модели (qwen2.5-coder:3b)
    заметно легче поддаются инъекции, чем выровненный флагман → Output Guard ловит НАСТОЯЩУЮ
    утечку/галлюцинацию секрета, а не срежиссированную на Stub."""

    def __init__(self, model: str = "qwen2.5-coder:3b", host: str = "http://127.0.0.1:11434"):
        self.model = model
        self.host = host

    def ask(self, system: str, user: str) -> str:
        import httpx  # noqa: E402

        payload = {
            "model": self.model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        r = httpx.post(f"{self.host}/api/chat", json=payload, timeout=180.0)
        r.raise_for_status()
        return r.json()["message"]["content"]


def get_backend(mode: str) -> tuple[Backend, str]:
    """mode='mock'|'real'|'ollama'. Возвращает (backend, label). Ленивый импорт, чтобы модуль
    грузился без backend/.env (например, в чисто-unit-тестах, где бэкенд подменён)."""
    if mode == "ollama":
        import os

        model = os.environ.get("GATEWAY_OLLAMA_MODEL", "qwen2.5-coder:3b")
        return OllamaBackend(model), f"ollama:{model}"
    from llm_backend import get_backend as _track_get_backend  # noqa: E402

    return _track_get_backend(mode)


class StubBackend:
    """Управляемый бэкенд для тестов: отдаёт заранее заданный ответ (или эхо), не ходит в сеть."""

    def __init__(self, response: str = "OK", echo_system: bool = False):
        self._response = response
        self._echo_system = echo_system
        self.calls: list[tuple[str, str]] = []

    def ask(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self._echo_system:
            return self._response + " " + system
        return self._response
