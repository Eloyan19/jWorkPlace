"""Двухуровневый инференс «micro-model first»: дешёвый Уровень 1 (эмбеддинг-классификатор `micro.py`)
отсекает уверенные входы, дорогой Уровень 2 (DeepSeek) включается ТОЛЬКО при UNSURE.

  Уровень 1  ──OK──▶  ответ микромодели (0 вызовов большой LLM)
     │
   UNSURE
     ▼
  Уровень 2 (DeepSeek `deepseek-v4-flash`) ──▶ классификация с anti-injection → ответ

Что здесь, а что в `micro.py`: микромодель и её сигнал уверенности — там; тут только маршрут
Уровень1→Уровень2, сам fallback-вызов LLM и сведение в единый результат для отчёта.

Сайдкар: НЕ импортирует `backend.app`. HTTP-ядро DeepSeek, парсинг ключа и прайс переиспользуем из
`experiments/confidence/confidence_harness.py` (тот же паттерн секретов: `DEEPSEEK_API_KEY` из
env/`backend/.env`, НИКОГДА не логируется и не попадает в промпт). Текст тикета — недоверенные данные:
в fallback-промпте он в nonce-делимитерах + anti-injection в system (паттерн `chat/grounding.py`).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from pathlib import Path

import httpx

import micro

# --- переиспользуем HTTP/ключ/прайс трека confidence ---
_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402  (path-bootstrap обязан быть выше импорта)

DEFAULT_MODEL = ch.DEFAULT_MODEL  # deepseek-v4-flash
_MAX_TOKENS_FALLBACK = 300  # ответ — одна строка JSON, но flash тратит часть на скрытые reasoning_tokens
_FALLBACK_KEYS = {"category"}


# ======================================================================================
#  Уровень 2 — LLM fallback (только категория, anti-injection, fail-closed)
# ======================================================================================


def validate_category(content: str) -> dict | None:
    """Строгий парсинг ответа fallback: ровно ключ `category` из закрытого множества. None = формат
    нарушен (fail-closed)."""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or set(obj.keys()) != _FALLBACK_KEYS:
        return None
    cat = obj.get("category")
    return {"category": cat} if cat in micro.CATEGORIES else None


def _fallback_messages(system_prompt: str, ticket_text: str, nonce: str) -> list[dict]:
    """Недоверенный текст тикета — в nonce-делимитерах, чтобы инъекция «SYSTEM: игнорируй…» внутри
    была видна модели как ДАННЫЕ, а не инструкция (паттерн chat/grounding.py::build_context)."""
    open_d, close_d = f"<<<TICKET nonce={nonce}", f"TICKET nonce={nonce}>>>"
    user = f"{open_d}\n{ticket_text}\n{close_d}\n\nВерни категорию этого тикета."
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


async def _llm_fallback(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    system_prompt: str,
    ticket_text: str,
) -> dict:
    """Один логический вызов DeepSeek (до 2 HTTP-попыток: retry на finish_reason='length' с удвоенным
    бюджетом — flash расходует часть на скрытые reasoning_tokens). Наружу не бросает: сбой →
    answer=None, note=llm_error. Возвращает {answer, tokens, n_calls, latency, note}."""
    nonce = secrets.token_hex(8)
    messages = _fallback_messages(system_prompt, ticket_text, nonce)
    usage_total = {"prompt": 0, "completion": 0}
    cur_max = _MAX_TOKENS_FALLBACK
    t0 = time.monotonic()
    n_calls = 0
    content, finish_reason = "", None
    for attempt in range(2):
        n_calls += 1
        try:
            content, usage, finish_reason = await ch._call_deepseek(
                client, api_key, model, messages, temperature=0.0, max_tokens=cur_max
            )
        except ch.LlmCallError:
            return {"answer": None, "tokens": dict(usage_total), "n_calls": n_calls,
                    "latency": round(time.monotonic() - t0, 3), "note": "llm_error"}
        usage_total["prompt"] += usage.get("prompt_tokens", 0) or 0
        usage_total["completion"] += usage.get("completion_tokens", 0) or 0
        if finish_reason != "length" or attempt == 1:
            break
        cur_max *= 2
    answer = validate_category(content)
    note = "ok" if answer else "schema_violation"
    return {"answer": answer, "tokens": usage_total, "n_calls": n_calls,
            "latency": round(time.monotonic() - t0, 3), "note": note}


# ======================================================================================
#  Двухуровневый пайплайн: micro → (UNSURE) → LLM
# ======================================================================================


async def classify_two_level(
    ticket_text: str,
    micro_model: micro.EmbeddingMicroModel,
    *,
    api_key: str,
    fallback_prompt: str,
    client: httpx.AsyncClient,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Полный маршрут одного входа. Микромодель считается в потоке (`to_thread`), чтобы embed-вызов
    Ollama не блокировал event loop; при UNSURE — async fallback DeepSeek.

    Возвращает дайджест для отчёта:
      {answer, source, escalated, micro, fallback, n_llm_calls, latency_sec, tokens, note}
    где `source` ∈ {micro, llm, none}, `escalated` — ушёл ли вход на Уровень 2, `n_llm_calls` —
    сколько РЕАЛЬНЫХ вызовов большой LLM сделано (0 если микромодель справилась)."""
    t0 = time.monotonic()
    micro_res = await asyncio.to_thread(micro_model.classify, ticket_text)

    # --- Уровень 1 уверен → отвечаем без большой LLM ---
    if micro_res["status"] == "OK":
        return {
            "answer": micro_res["answer"], "source": "micro", "escalated": False,
            "micro": micro_res, "fallback": None, "n_llm_calls": 0,
            "latency_sec": round(time.monotonic() - t0, 3),
            "tokens": {"prompt": 0, "completion": 0}, "note": "micro_ok",
        }

    # --- Уровень 1 не уверен (UNSURE) → эскалация на Уровень 2 ---
    fb = await _llm_fallback(client, api_key, model, fallback_prompt, ticket_text)
    source = "llm" if fb["answer"] else "none"
    return {
        "answer": fb["answer"], "source": source, "escalated": True,
        "micro": micro_res, "fallback": fb, "n_llm_calls": fb["n_calls"],
        "latency_sec": round(time.monotonic() - t0, 3),
        "tokens": fb["tokens"], "note": f"escalated:{micro_res['note']}",
    }


async def classify_llm_only(
    ticket_text: str,
    *,
    api_key: str,
    fallback_prompt: str,
    client: httpx.AsyncClient,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Базлайн «всегда большая LLM» (для честного сравнения в отчёте): тот же fallback-вызов, но на
    КАЖДОМ входе. Возвращает {answer, n_llm_calls, latency_sec, tokens, note}."""
    fb = await _llm_fallback(client, api_key, model, fallback_prompt, ticket_text)
    return {"answer": fb["answer"], "n_llm_calls": fb["n_calls"], "latency_sec": fb["latency"],
            "tokens": fb["tokens"], "note": fb["note"]}
