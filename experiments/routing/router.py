"""Routing между моделями DeepSeek с fallback-логикой: дешёвая `flash` отвечает первой,
эвристика решает — доверять ей или эскалировать на сильную `pro`.

Строится ПОВЕРХ трека `experiments/confidence`: его статус уверенности OK/UNSURE/FAIL — это и
есть эвристика «не уверен → эскалируй». Три эвристики эскалации разной цены/силы (см.
`ESCALATION_POLICIES`), чтобы на одном прогоне увидеть их плюсы/минусы:

  - **length**     — output-length gate: эскалируем, если ответ flash пустой/обрезан по длине.
                     Дёшево (1 вызов flash), но ловит только вырожденный вывод.
  - **constraint** — schema gate: эскалируем, если ответ flash нарушил строгую JSON-схему.
                     Тоже 1 вызов; ловит формат-ошибки, которые length пропустил бы.
  - **confidence** — uncertainty gate: полный confidence-пайплайн на flash (self-consistency +
                     self-check); эскалируем, если статус != OK ИЛИ agreement < порога. Сильный
                     сигнал семантической неуверенности, но дорогой (до 4 вызовов flash).

Сайдкар: НЕ импортирует `backend.app`. HTTP-клиент, парсинг ключа, строгую валидацию схемы и
confidence-пайплайн переиспользуем из соседнего `confidence_harness` (тот же паттерн секретов:
`DEEPSEEK_API_KEY` читается из env/`backend/.env`, НИКОГДА не логируется и не попадает в промпт —
см. CLAUDE.md «Безопасность и секреты»). Ничего своего про ключ здесь не делаем.

Все функции решения (`policy_*`, `route`, `model_cost`) — чистые: считают по уже собранным
сигналам, без сети. Сбор сигналов (реальные вызовы DeepSeek) — в `run_routing.py`.
"""
import sys
import time
from pathlib import Path

import httpx

# --- переиспользуем ядро соседнего трека confidence (HTTP/ключ/схема/пайплайн) ---
_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402  (path-bootstrap обязан быть выше импорта)

# --- модели: дешёвая → сильная ---
SMALL_MODEL = "deepseek-v4-flash"  # быстрая/дешёвая, отвечает первой
BIG_MODEL = "deepseek-v4-pro"  # сильная, fallback при неуверенности

# Прайс $ за 1M токенов ПО МОДЕЛИ — ПРИБЛИЗИТЕЛЬНО, обнови под актуальный прайс DeepSeek.
# Точные числа не принципиальны для вывода трека: качественный итог (routing дешевле, чем гнать
# всё на pro) держится, пока pro дороже flash. flash-строка совпадает с константами confidence_harness.
PRICE = {
    SMALL_MODEL: {"prompt": ch.PRICE_PROMPT_USD_PER_MTOK, "completion": ch.PRICE_COMPLETION_USD_PER_MTOK},
    BIG_MODEL: {"prompt": 0.55, "completion": 2.19},  # placeholder ~2x flash
}

# length-эвристика: ответ короче этого (после strip) считаем вырожденным/пустым → эскалация.
MIN_CONTENT_CHARS = 2
# confidence-эвристика: agreement строго меньше порога → эскалация. Порог 1.0 = «эскалируй любую
# неполную согласованность»; понизь (напр. 0.84), чтобы терпеть частичный раскол и реже эскалировать.
AGREEMENT_ESCALATE_THRESHOLD = 1.0


def model_cost(model: str, tokens: dict) -> float:
    """$-оценка одного набора токенов на конкретной модели по таблице `PRICE`."""
    price = PRICE[model]
    return (
        (tokens.get("prompt", 0) or 0) / 1_000_000 * price["prompt"]
        + (tokens.get("completion", 0) or 0) / 1_000_000 * price["completion"]
    )


# --- один вызов классификатора (для дешёвых эвристик и для big-fallback) ---


async def single_classify(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    system_prompt: str,
    ticket_text: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 300,
) -> dict:
    """Одна классификация тикета указанной моделью с retry на обрезку по длине.

    Возвращает `{answer, content, truncated, finish_reason, tokens{prompt,completion}, n_calls,
    latency}`. `answer` = провалидированная строгой схемой пара (или None). `truncated` = финальный
    `finish_reason == "length"` (deepseek-v4 тратит часть бюджета на скрытые reasoning_tokens — при
    тесном max_tokens content приходит пустым с finish_reason='length', поэтому 1 retry с удвоенным
    бюджетом, паттерн `confidence_harness._timed_call`). Наружу не бросает: сбой вызова → answer=None,
    finish_reason='error' (fail-closed, вызывающий трактует как «эскалируй»)."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": ticket_text},
    ]
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0}
    cur_max_tokens = max_tokens
    t0 = time.monotonic()
    n_calls = 0
    content, finish_reason = "", None

    for attempt in range(2):
        n_calls += 1
        try:
            # ch._call_deepseek — приватная по конвенции, но это осознанное переиспользование HTTP-ядра
            # соседнего сайдкара, чтобы не плодить второй httpx-клиент и не расходиться в обработке ошибок.
            content, usage, finish_reason = await ch._call_deepseek(
                client, api_key, model, messages, temperature=temperature, max_tokens=cur_max_tokens
            )
        except ch.LlmCallError:
            return {
                "answer": None,
                "content": "",
                "truncated": False,
                "finish_reason": "error",
                "tokens": {"prompt": usage_total["prompt_tokens"], "completion": usage_total["completion_tokens"]},
                "n_calls": n_calls,
                "latency": round(time.monotonic() - t0, 3),
            }
        usage_total["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
        usage_total["completion_tokens"] += usage.get("completion_tokens", 0) or 0
        if finish_reason != "length" or attempt == 1:
            break
        cur_max_tokens *= 2

    return {
        "answer": ch.validate_constraint(content),
        "content": content,
        "truncated": finish_reason == "length",
        "finish_reason": finish_reason,
        "tokens": {"prompt": usage_total["prompt_tokens"], "completion": usage_total["completion_tokens"]},
        "n_calls": n_calls,
        "latency": round(time.monotonic() - t0, 3),
    }


# --- эвристики эскалации (чистые функции над собранными сигналами) ---
# Сигнал одного входа: {"small": <single_classify flash>, "confidence": <classify_with_confidence
# flash>, "big": <single_classify pro>}. Каждая политика возвращает (escalate: bool, reason: str).


def policy_length(sig: dict) -> tuple[bool, str]:
    """Дешёвая эвристика ДЛИНЫ ОТВЕТА: доверяем flash, только если он выдал непустой неусечённый
    ответ. Не смотрит на смысл — ловит лишь вырожденный вывод (пусто/обрыв по max_tokens)."""
    s = sig["small"]
    if s["finish_reason"] == "error":
        return True, "small_error"
    if s["truncated"]:
        return True, "truncated"
    if len(s["content"].strip()) < MIN_CONTENT_CHARS:
        return True, "empty_output"
    return False, "length_ok"


def policy_constraint(sig: dict) -> tuple[bool, str]:
    """Дешёвая эвристика СХЕМЫ: доверяем flash, только если ответ прошёл строгую JSON-схему
    (ровно ключи category/priority из закрытых множеств). Ловит формат-брак, а не семантику."""
    s = sig["small"]
    if s["finish_reason"] == "error":
        return True, "small_error"
    if s["answer"] is None:
        return True, "schema_violation"
    return False, "schema_ok"


def policy_confidence(sig: dict) -> tuple[bool, str]:
    """Дорогая эвристика УВЕРЕННОСTИ: полный confidence-пайплайн flash. Эскалируем, если статус
    != OK (self-consistency раскол или self-check reject) ИЛИ agreement < порога. Сильнейший
    сигнал — ловит семантическую неуверенность, невидимую дешёвым эвристикам, но стоит до 4 вызовов."""
    c = sig["confidence"]
    if c["status"] == "FAIL":
        return True, "confidence_fail"
    if c["status"] == "UNSURE":
        return True, "confidence_unsure"
    if c["agreement"] < AGREEMENT_ESCALATE_THRESHOLD:
        return True, f"low_agreement<{AGREEMENT_ESCALATE_THRESHOLD}"
    return False, "confident_ok"


ESCALATION_POLICIES = {
    "length": policy_length,
    "constraint": policy_constraint,
    "confidence": policy_confidence,
}

# Сколько вызовов flash стоит «проба» каждой политики (чтобы не платить за чужой сигнал):
# length/constraint используют одиночный вызов `small`, confidence — весь свой пайплайн.
_POLICY_USES_CONFIDENCE_PROBE = {"length": False, "constraint": False, "confidence": True}


def route(
    policy_name: str,
    sig: dict,
    *,
    small_model: str = SMALL_MODEL,
    big_model: str = BIG_MODEL,
) -> dict:
    """Применить fallback-routing по одной эвристике к собранным сигналам одного входа.

    Возвращает `{escalated, reason, answer, model_used, cost_usd, n_llm_calls}`. Стоимость честная:
    только вызовы, которые ЭТА политика реально сделала бы в проде (своя проба на flash + вызов pro
    при эскалации) — чужие пробы других политик не учитываются."""
    escalate, reason = ESCALATION_POLICIES[policy_name](sig)

    if _POLICY_USES_CONFIDENCE_PROBE[policy_name]:
        probe = sig["confidence"]
        probe_tokens = probe["tokens"]
        probe_calls = probe["n_llm_calls"]
        small_answer = probe["answer"]  # мажоритарный ответ пайплайна
    else:
        probe = sig["small"]
        probe_tokens = probe["tokens"]
        probe_calls = probe["n_calls"]
        small_answer = probe["answer"]

    cost = model_cost(small_model, probe_tokens)
    n_calls = probe_calls

    if escalate:
        answer = sig["big"]["answer"]
        model_used = big_model
        cost += model_cost(big_model, sig["big"]["tokens"])
        n_calls += sig["big"]["n_calls"]
    else:
        answer = small_answer
        model_used = small_model

    return {
        "escalated": escalate,
        "reason": reason,
        "answer": answer,
        "model_used": model_used,
        "cost_usd": round(cost, 6),
        "n_llm_calls": n_calls,
    }
