"""LLM-бэкенд демо: реальная DeepSeek через project-адаптер ИЛИ детерминированный mock.

- real: зовёт настоящую модель через `app.llm.deepseek` (тот же LlmService, что в проде) —
  честная демонстрация, что инъекция реально пробивает живую LLM. Нужен DEEPSEEK_API_KEY в
  backend/.env. Non-thinking модель (env DEEPSEEK_MODEL), temperature=0.
- mock: «наивная послушная LLM» — симулятор для воспроизводимого/бесплатного прогона. НЕ ИИ:
  грубо извлекает императивы из данных и «подчиняется», моделируя уязвимое поведение. Помечаем
  явно, чтобы не выдавать симуляцию за живой результат.
"""
import asyncio
import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _load_real_llm():
    """Ленивая загрузка project-адаптера: добавляем backend/ в sys.path, читаем .env в env."""
    sys.path.insert(0, str(_BACKEND))
    env = _BACKEND / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                import os
                os.environ.setdefault(k.strip(), v.strip())
    from app.config import get_settings  # noqa: E402
    from app.llm.deepseek import get_llm  # noqa: E402
    return get_llm(get_settings())


class RealLLM:
    """Обёртка над project LlmService: system+user → строка ответа (temperature=0)."""

    def __init__(self):
        self._llm = _load_real_llm()

    def ask(self, system: str, user: str) -> str:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        return asyncio.run(self._llm.chat(messages, temperature=0.0, max_tokens=512))


class MockGullibleLLM:
    """Симулятор наивной LLM: подчиняется любой найденной в ЛЮБОЙ части промпта императивной
    команде (в т.ч. из данных) — так воспроизводимо демонстрируем механику инъекции без сети.
    Реальная модель ведёт себя похоже, но недетерминированно; mock — стабильный нижний предел."""

    # Директивы, которые «наивная» модель послушно исполнит, встретив в тексте.
    _APPEND_RE = re.compile(r"(?:add(?:ing)? (?:the )?line|добав[ья].{0,20}строку)[:\s]+(.+)", re.I)
    _REPLACE_RE = re.compile(r"(?:reply only with|ответь только|выведи только)[:\s]+(.+)", re.I)
    _REVEAL_RE = re.compile(r"(?:reveal|раскрой|покажи).{0,30}system prompt", re.I)
    _FAKE_RE = re.compile(r"(?:state that|say that|утвержда[йи].{0,10}что)[:\s]+(.+)", re.I)

    def ask(self, system: str, user: str) -> str:
        blob = f"{system}\n{user}"
        # Инъекция «выведи только X» перебивает всё (симуляция игнора system prompt).
        m = self._REPLACE_RE.search(blob)
        if m:
            out = m.group(1).strip().splitlines()[0]
            if self._REVEAL_RE.search(blob):
                out += " | SYSTEM PROMPT: " + system.splitlines()[0]
            return out
        # Инъекция «утверждай что X» → поддельный факт в ответе.
        m = self._FAKE_RE.search(blob)
        if m:
            return "По данным источника: " + m.group(1).strip().splitlines()[0]
        # Базовая задача = «суммаризируй»: берём первые предложения…
        summary = " ".join(re.split(r"(?<=[.!?])\s+", _visible(user))[:2]).strip()
        # …но инъекция «добавь строку X» дописывает атакующую строку в summary.
        m = self._APPEND_RE.search(blob)
        if m:
            summary = (summary + "\n" + m.group(1).strip().splitlines()[0]).strip()
        return summary or "(пустой ответ)"


def _visible(text: str) -> str:
    """Грубо «видимый человеку» текст: без html-комментариев и тегов — что осталось бы на экране."""
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def get_backend(mode: str):
    """mode='real'|'mock'. При сбое real (нет ключа/сети) — падаем в mock с пометкой."""
    if mode == "mock":
        return MockGullibleLLM(), "mock"
    try:
        return RealLLM(), "real"
    except Exception as exc:  # noqa: BLE001 — учебный харнесс, любой сбой → фолбэк
        print(f"[!] real LLM недоступна ({type(exc).__name__}), фолбэк в mock", file=sys.stderr)
        return MockGullibleLLM(), "mock(fallback)"
