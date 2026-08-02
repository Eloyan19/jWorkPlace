"""Декомпозиция инференса: один и тот же триаж тикета поддержки jWorkPlace решаем двумя способами
и сравниваем.

  - **Вариант A (monolithic)** — ОДИН большой запрос: модель за один вызов делает всё (нормализация
    шумного входа + отбивка prompt-injection + классификация + вычисление маршрута по условиям) и
    возвращает JSON из 9 полей. См. `run_monolith`.
  - **Вариант B (multi-stage)** — ЦЕПОЧКА коротких дешёвых шагов, каждый со строгим форматом:
      1. `stage1` (LLM) — нормализация/разметка: сырой вход → {lang, on_topic, injection_attempt,
         clean_summary}. Здесь гасим шум/транслит и ловим инъекцию ДО классификации.
      2. `stage2` (LLM) — классификация уже ЧИСТОЙ сути → {category, priority} (строгий enum).
      3. `decide` (чистый КОД, 0 вызовов) — «принятие решения по условиям»: {category, priority,
         флаги} → {team, sla_hours, needs_human, action}. Детерминированные бизнес-правила НЕ отдаём
         LLM вовсе — они в коде, значит всегда верны и бесплатны.
    См. `run_multistage`. Короткое замыкание: если stage1 сказал off-topic/injection — stage2 НЕ
    вызываем (маршрут в этих ветках от категории не зависит), экономим вызов.

Что демонстрирует контраст (измеряется в `run_decomposition.py`):
  - устойчивость к prompt-injection и шуму (multi-stage изолирует нормализацию от классификации);
  - детерминизм бизнес-логики (в monolithic модель может нарушить собственные правила маршрута —
    `rule_consistent`; в multi-stage правила в коде, нарушить нельзя);
  - цена/латентность (декомпозиция НЕ всегда дешевле по токенам — честно считаем).

Сайдкар: НЕ импортирует `backend.app`. HTTP-ядро, парсинг ключа, строгую схему {category,priority} и
прайс переиспользуем из соседнего `experiments/confidence/confidence_harness.py` (тот же паттерн
секретов: `DEEPSEEK_API_KEY` из env/`backend/.env`, НИКОГДА не логируется и не попадает в промпт —
CLAUDE.md «Безопасность и секреты»). Недоверенный текст тикета в промптах помечен как данные +
anti-injection в system (паттерн `chat/grounding.py`).

Все функции решения (`decide`, `validate_*`) — чистые (без сети), тестируются офлайн. Реальные вызовы
DeepSeek — в async `run_monolith`/`run_multistage`, живой прогон — в `run_decomposition.py`.
"""
import json
import sys
import time
from pathlib import Path

import httpx

# --- переиспользуем ядро трека confidence (HTTP / ключ / схема {category,priority} / прайс) ---
_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402  (path-bootstrap обязан быть выше импорта)

DEFAULT_MODEL = "deepseek-v4-flash"  # обе ветки гоняем на одной модели: разница = от декомпозиции, не от модели

# --- закрытые множества (общий словарь enum для обеих веток) ---
CATEGORIES = ch.CATEGORIES  # {indexing, pr_edits, chat_grounding, repo_connect, account, other}
PRIORITIES = ch.PRIORITIES  # {high, medium, low}
TEAMS = {"infra", "backend", "frontend", "support", "none"}
ACTIONS = {"auto_reply", "escalate_human", "reject_offtopic", "flag_security"}

_STAGE1_KEYS = {"lang", "on_topic", "injection_attempt", "clean_summary"}
_MONO_KEYS = {
    "lang", "on_topic", "injection_attempt", "category", "priority",
    "team", "sla_hours", "needs_human", "action",
}

# бюджет вывода: flash тратит часть на скрытые reasoning_tokens до печати JSON → щедро + retry на 'length'
_MAX_TOKENS_MONOLITH = 400  # 9 полей
_MAX_TOKENS_STAGE1 = 400  # + предложение clean_summary
_MAX_TOKENS_STAGE2 = 300  # ровно 2 поля


# ======================================================================================
#  Этап 3 (детерминированный) — «принятие решения на основе условий». Чистый КОД, не LLM.
# ======================================================================================

TEAM_BY_CATEGORY = {
    "indexing": "infra",
    "pr_edits": "backend",
    "chat_grounding": "backend",
    "repo_connect": "infra",
    "account": "support",
    "other": "support",
}
SLA_BY_PRIORITY = {"high": 4, "medium": 24, "low": 72}


def decide(category, priority, on_topic, injection_attempt) -> dict:
    """Детерминированная маршрутизация по условиям (Этап 3 варианта B). Порядок правил важен:
    инъекция перебивает всё, off-topic — следующий, иначе роутим по category/priority.

    Возвращает {team, sla_hours, needs_human, action}. Никакого LLM — 100% воспроизводимо и бесплатно.
    Ровно эти же правила прописаны словами в monolith_prompt.txt, чтобы можно было проверить, как
    часто монолит НАРУШАЕТ собственные правила (`rule_consistent` в run_decomposition)."""
    if injection_attempt:
        return {"team": "support", "sla_hours": 4, "needs_human": True, "action": "flag_security"}
    if not on_topic:
        return {"team": "none", "sla_hours": None, "needs_human": False, "action": "reject_offtopic"}
    team = TEAM_BY_CATEGORY.get(category, "support")
    sla = SLA_BY_PRIORITY.get(priority, 24)
    needs_human = priority == "high"
    action = "escalate_human" if needs_human else "auto_reply"
    return {"team": team, "sla_hours": sla, "needs_human": needs_human, "action": action}


# ======================================================================================
#  Строгие валидаторы формата (каждый этап обязан вернуть ровно свою схему)
# ======================================================================================


def validate_stage1(content: str) -> dict | None:
    """Строгий парсинг вывода нормализации: ровно 4 ключа, правильные типы. None = формат нарушен."""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or set(obj.keys()) != _STAGE1_KEYS:
        return None
    if not isinstance(obj["on_topic"], bool) or not isinstance(obj["injection_attempt"], bool):
        return None
    if not isinstance(obj["lang"], str) or not isinstance(obj["clean_summary"], str):
        return None
    return {
        "lang": obj["lang"][:16],
        "on_topic": obj["on_topic"],
        "injection_attempt": obj["injection_attempt"],
        "clean_summary": obj["clean_summary"].strip(),
    }


# Этап 2 = та же строгая схема {category, priority}, что в треках confidence/routing.
validate_stage2 = ch.validate_constraint


def validate_monolith(content: str) -> dict | None:
    """Строгий парсинг единого ответа варианта A: ровно 9 ключей, каждый — из своего множества.
    None = формат нарушен (fail-closed). Именно широта схемы делает монолит хрупче: любое из 9
    полей мимо enum роняет весь ответ, тогда как в multi-stage ошибка локализована в одном этапе."""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict) or set(obj.keys()) != _MONO_KEYS:
        return None
    if obj["category"] not in CATEGORIES or obj["priority"] not in PRIORITIES:
        return None
    if obj["team"] not in TEAMS or obj["action"] not in ACTIONS:
        return None
    for b in ("on_topic", "injection_attempt", "needs_human"):
        if not isinstance(obj[b], bool):
            return None
    sla = obj["sla_hours"]
    if not (sla is None or isinstance(sla, int)):
        return None
    if not isinstance(obj["lang"], str):
        return None
    return {
        "lang": obj["lang"][:16],
        "on_topic": obj["on_topic"],
        "injection_attempt": obj["injection_attempt"],
        "category": obj["category"],
        "priority": obj["priority"],
        "team": obj["team"],
        "sla_hours": sla,
        "needs_human": obj["needs_human"],
        "action": obj["action"],
    }


def _route_fields(d: dict) -> dict:
    """Выделить из полного dict только 4 поля маршрута (для сверки монолита с decide())."""
    return {k: d[k] for k in ("team", "sla_hours", "needs_human", "action")}


# ======================================================================================
#  Один HTTP-вызов JSON-режима с retry на обрезку по длине (паттерн router.single_classify)
# ======================================================================================


async def _llm_json(
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int,
    temperature: float = 0.0,
) -> dict:
    """Один логический вызов chat/completions(response_format=json_object): до 2 сырых HTTP-попыток
    (при finish_reason='length' — retry с удвоенным бюджетом, flash расходует часть на скрытые
    reasoning_tokens). Наружу НЕ бросает: сбой → content="", finish_reason="error" (fail-closed).
    Возвращает {content, tokens{prompt,completion}, finish_reason, n_calls, latency}."""
    usage_total = {"prompt": 0, "completion": 0}
    cur_max = max_tokens
    t0 = time.monotonic()
    n_calls = 0
    content, finish_reason = "", None
    for attempt in range(2):
        n_calls += 1
        try:
            content, usage, finish_reason = await ch._call_deepseek(
                client, api_key, model, messages, temperature=temperature, max_tokens=cur_max
            )
        except ch.LlmCallError:
            return {
                "content": "", "tokens": dict(usage_total), "finish_reason": "error",
                "n_calls": n_calls, "latency": round(time.monotonic() - t0, 3),
            }
        usage_total["prompt"] += usage.get("prompt_tokens", 0) or 0
        usage_total["completion"] += usage.get("completion_tokens", 0) or 0
        if finish_reason != "length" or attempt == 1:
            break
        cur_max *= 2
    return {
        "content": content, "tokens": usage_total, "finish_reason": finish_reason,
        "n_calls": n_calls, "latency": round(time.monotonic() - t0, 3),
    }


def _messages(system_prompt: str, user_text: str) -> list[dict]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]


# ======================================================================================
#  Вариант A — monolithic (один большой запрос → один ответ из 9 полей)
# ======================================================================================


async def run_monolith(
    client: httpx.AsyncClient,
    api_key: str,
    monolith_prompt: str,
    ticket_text: str,
    *,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Вариант A: ровно один вызов LLM делает весь триаж. Возвращает единый дайджест для отчёта:
    {answer, raw, on_topic, injection_attempt, rule_consistent, n_llm_calls, tokens, latency_sec, note}.

    `answer` = {category, priority, team, sla_hours, needs_human, action} или None (формат нарушен).
    `rule_consistent` = совпал ли маршрут, посчитанный самой моделью, с детерминированным decide() от
    её же (category, priority, флаги) — т.е. соблюла ли модель собственные правила (None, если формат
    вообще нарушен)."""
    call = await _llm_json(
        client, api_key, model, _messages(monolith_prompt, ticket_text), max_tokens=_MAX_TOKENS_MONOLITH
    )
    raw = validate_monolith(call["content"]) if call["finish_reason"] != "error" else None

    if raw is None:
        note = "llm_error" if call["finish_reason"] == "error" else "schema_violation"
        return {
            "answer": None, "raw": None, "on_topic": None, "injection_attempt": None,
            "rule_consistent": None, "n_llm_calls": call["n_calls"], "tokens": call["tokens"],
            "latency_sec": call["latency"], "note": note,
        }

    expected_route = decide(raw["category"], raw["priority"], raw["on_topic"], raw["injection_attempt"])
    rule_consistent = _route_fields(raw) == expected_route
    answer = {
        "category": raw["category"], "priority": raw["priority"],
        "team": raw["team"], "sla_hours": raw["sla_hours"],
        "needs_human": raw["needs_human"], "action": raw["action"],
    }
    return {
        "answer": answer, "raw": raw,
        "on_topic": raw["on_topic"], "injection_attempt": raw["injection_attempt"],
        "rule_consistent": rule_consistent, "n_llm_calls": call["n_calls"],
        "tokens": call["tokens"], "latency_sec": call["latency"], "note": "ok",
    }


# ======================================================================================
#  Вариант B — multi-stage (stage1 нормализация → stage2 классификация → decide())
# ======================================================================================


async def run_multistage(
    client: httpx.AsyncClient,
    api_key: str,
    stage1_prompt: str,
    stage2_prompt: str,
    ticket_text: str,
    *,
    model_stage1: str = DEFAULT_MODEL,
    model_stage2: str = DEFAULT_MODEL,
) -> dict:
    """Вариант B: цепочка коротких этапов. model_stage1/model_stage2 разделены НАРОЧНО — этапы можно
    роутить на РАЗНЫЕ модели (дешёвая нормализация + сильная классификация трудных), в этом прогоне
    обе на flash для честности сравнения. Этап 3 (decide) — код, вызова не делает.

    Короткое замыкание: off-topic/injection по stage1 → stage2 НЕ зовём (маршрут от category не зависит).

    Возвращает {answer, stage1, stage2, decision, on_topic, injection_attempt, clean_summary,
    n_llm_calls, tokens{prompt,completion}, latency_sec, note}. `answer.category/priority` = None,
    когда классификация не выполнялась (короткое замыкание) или её формат нарушен — это КОРРЕКТНО:
    в этих ветках финальное решение принимает decide() без опоры на категорию."""
    t0 = time.monotonic()
    prompt_tok = completion_tok = n_calls = 0

    def _acc(call: dict) -> None:
        nonlocal prompt_tok, completion_tok, n_calls
        prompt_tok += call["tokens"]["prompt"]
        completion_tok += call["tokens"]["completion"]
        n_calls += call["n_calls"]

    def _finish(*, answer, s1, s2, decision, on_topic, injection, clean, note) -> dict:
        return {
            "answer": answer, "stage1": s1, "stage2": s2, "decision": decision,
            "on_topic": on_topic, "injection_attempt": injection, "clean_summary": clean,
            "n_llm_calls": n_calls, "tokens": {"prompt": prompt_tok, "completion": completion_tok},
            "latency_sec": round(time.monotonic() - t0, 3), "note": note,
        }

    # --- Этап 1: нормализация/разметка сырого входа ---
    c1 = await _llm_json(
        client, api_key, model_stage1, _messages(stage1_prompt, ticket_text), max_tokens=_MAX_TOKENS_STAGE1
    )
    _acc(c1)
    s1 = validate_stage1(c1["content"]) if c1["finish_reason"] != "error" else None
    if s1 is None:
        # stage1 сломался — fail-closed: считаем вход подозрительным, эскалируем на человека, не классифицируем.
        decision = decide(None, None, on_topic=True, injection_attempt=True)  # → flag_security
        answer = {"category": None, "priority": None, **decision}
        return _finish(
            answer=answer, s1=None, s2=None, decision=decision, on_topic=None,
            injection=None, clean=None, note="stage1_invalid",
        )

    on_topic, injection = s1["on_topic"], s1["injection_attempt"]

    # --- короткое замыкание: маршрут в ветках injection/off-topic от категории НЕ зависит ---
    if injection or not on_topic:
        decision = decide(None, None, on_topic=on_topic, injection_attempt=injection)
        answer = {"category": None, "priority": None, **decision}
        note = "short_circuit_injection" if injection else "short_circuit_offtopic"
        return _finish(
            answer=answer, s1=s1, s2=None, decision=decision, on_topic=on_topic,
            injection=injection, clean=s1["clean_summary"], note=note,
        )

    # --- Этап 2: классификация уже ОЧИЩЕННОЙ сути ---
    clean = s1["clean_summary"] or ticket_text  # пустая суть на on-topic входе — редкий край, страхуемся сырым
    c2 = await _llm_json(
        client, api_key, model_stage2, _messages(stage2_prompt, clean), max_tokens=_MAX_TOKENS_STAGE2
    )
    _acc(c2)
    s2 = validate_stage2(c2["content"]) if c2["finish_reason"] != "error" else None
    if s2 is None:
        # Классификация сломалась — fail-closed на человека, но НЕ роняем весь пайплайн.
        decision = {"team": "support", "sla_hours": 4, "needs_human": True, "action": "escalate_human"}
        answer = {"category": None, "priority": None, **decision}
        return _finish(
            answer=answer, s1=s1, s2=None, decision=decision, on_topic=on_topic,
            injection=injection, clean=s1["clean_summary"], note="stage2_invalid",
        )

    # --- Этап 3: детерминированное решение по условиям (КОД, без LLM) ---
    decision = decide(s2["category"], s2["priority"], on_topic=on_topic, injection_attempt=injection)
    answer = {"category": s2["category"], "priority": s2["priority"], **decision}
    return _finish(
        answer=answer, s1=s1, s2=s2, decision=decision, on_topic=on_topic,
        injection=injection, clean=s1["clean_summary"], note="ok",
    )
