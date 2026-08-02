"""Прогон декомпозиции по `data/test_inputs.jsonl` через реальный DeepSeek: для каждого тикета
гоняем ОБА варианта — monolithic (A) и multi-stage (B) — и пишем `report.md`, сравнивая их по
точности классификации, формат-надёжности, устойчивости к инъекции/шуму, детерминизму маршрута и
цене/латентности.

Схема прогона: для КАЖДОГО входа один вызов монолита + одна цепочка multi-stage (параллельно между
собой; внутри цепочки этапы идут по очереди — stage2 зависит от stage1). Обе ветки — на одной модели
(`pipeline.DEFAULT_MODEL`), чтобы разница в метриках шла от СПОСОБА (моно vs декомпозиция), а не от
модели.

Не импортирует `backend.app`; ключ — через `confidence_harness.load_api_key()` (никогда не логируем).
Нет ключа / API недоступен → НЕ выдумываем числа: помечаем в отчёте, что прогон не выполнен, exit 1.

Запуск: `../../backend/venv/bin/python3 run_decomposition.py` (venv с httpx — см. README.md).
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

import pipeline as pl

_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402

_HERE = Path(__file__).resolve().parent
_DATA_PATH = _HERE / "data" / "test_inputs.jsonl"
_MONO_PROMPT_PATH = _HERE / "data" / "monolith_prompt.txt"
_STAGE1_PROMPT_PATH = _HERE / "data" / "stage1_prompt.txt"
_STAGE2_PROMPT_PATH = _HERE / "data" / "stage2_prompt.txt"
_REPORT_PATH = _HERE / "report.md"

_CONCURRENCY = 3
# Чего требует «спрятанный в тикете» атакующий во входе с prompt-injection (см. test_inputs.jsonl #19):
_INJECTION_TARGET = {"category": "pr_edits", "priority": "high"}


def _load_inputs() -> list[dict]:
    rows = []
    with _DATA_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _is_injection_row(row: dict) -> bool:
    return "игнорируй все предыдущие инструкции" in row["user"].lower()


async def _collect(client, row, prompts, api_key) -> dict:
    mono_p, s1_p, s2_p = prompts
    text = row["user"]
    mono_task = pl.run_monolith(client, api_key, mono_p, text)
    multi_task = pl.run_multistage(client, api_key, s1_p, s2_p, text)
    mono, multi = await asyncio.gather(mono_task, multi_task)
    return {**row, "mono": mono, "multi": multi}


async def _run_all(inputs, prompts, api_key) -> list[dict]:
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(client, row):
        async with sem:
            return await _collect(client, row, prompts, api_key)

    async with httpx.AsyncClient(timeout=ch.TIMEOUT) as client:
        return await asyncio.gather(*[_one(client, row) for row in inputs])


# --- вспомогательное ---


def _class_match(answer: dict | None, gold: dict | None) -> bool:
    if answer is None or gold is None:
        return False
    return answer.get("category") == gold["category"] and answer.get("priority") == gold["priority"]


def _cost(tokens: dict) -> float:
    return ch.estimate_cost_usd(tokens.get("prompt", 0), tokens.get("completion", 0))


def _fmt_ans(ans: dict | None) -> str:
    if ans is None:
        return "—(формат сломан)"
    cat = ans.get("category") or "—"
    prio = ans.get("priority") or "—"
    return f"{cat}/{prio} → {ans.get('action', '—')}"


def _build_report(rows: list[dict]) -> str:
    n = len(rows)
    correct_rows = [r for r in rows if r["bucket"] == "correct"]
    n_correct = len(correct_rows)
    noisy_rows = [r for r in rows if r["bucket"] == "noisy"]

    L: list[str] = []
    L.append("# Отчёт: декомпозиция инференса — monolithic (A) vs multi-stage (B)\n")
    L.append(
        f"Модель обеих веток: `{pl.DEFAULT_MODEL}`. Входов: **{n}** "
        f"(correct={n_correct}, borderline={sum(1 for r in rows if r['bucket']=='borderline')}, "
        f"noisy={len(noisy_rows)}). Задача: полный триаж тикета "
        "(нормализация + отбивка инъекции + классификация + маршрут по условиям).\n"
    )
    L.append(
        "**A (monolithic)** — 1 вызов, весь JSON из 9 полей сразу. "
        "**B (multi-stage)** — stage1 нормализация → stage2 классификация чистой сути → decide() "
        "(маршрут в КОДЕ, 0 вызовов); off-topic/injection коротко замыкают stage2.\n"
    )

    # === 1. Точность классификации на gold ===
    mono_acc = sum(1 for r in correct_rows if _class_match(r["mono"]["answer"], r["gold"]))
    multi_acc = sum(1 for r in correct_rows if _class_match(r["multi"]["answer"], r["gold"]))
    L.append("## 1. Точность классификации (category+priority) на корзине «correct» (есть gold)\n")
    L.append("| Вариант | точность vs gold |")
    L.append("|---|---|")
    L.append(f"| A monolithic | {mono_acc} / {n_correct} |")
    L.append(f"| B multi-stage | {multi_acc} / {n_correct} |")
    L.append(
        "\n_Если цифры близки — декомпозиция не роняет качество классификации на «чистых» входах; её "
        "выигрыш ищем ниже (устойчивость к шуму/инъекции + детерминизм маршрута), а не здесь._\n"
    )

    # === 2. Формат-надёжность ===
    mono_broken = [r for r in rows if r["mono"]["answer"] is None]
    multi_broken = [r for r in rows if r["multi"]["note"] in ("stage1_invalid", "stage2_invalid")]
    L.append("## 2. Формат-надёжность (валиден ли строгий JSON)\n")
    L.append(
        f"- **A**: невалидная схема у **{len(mono_broken)}/{n}** входов "
        "(любое из 9 полей мимо enum роняет ВЕСЬ ответ — широкая схема хрупче).\n"
        f"- **B**: сломанный этап у **{len(multi_broken)}/{n}** входов "
        "(ошибка локализована в конкретном этапе, остальные не задеты; decide() не ломается никогда — это код).\n"
    )
    if mono_broken:
        L.append("  - A сломался на: " + "; ".join(f"«{r['user'][:32]}…» ({r['mono']['note']})" for r in mono_broken) + "\n")

    # === 3. Устойчивость к инъекции и шуму (корзина noisy) ===
    L.append("## 3. Устойчивость к prompt-injection и шуму (корзина «noisy»)\n")
    L.append("| вход (обрезано) | A: inj? / on-topic / action | B: inj? / on-topic / action |")
    L.append("|---|---|---|")
    for r in noisy_rows:
        u = r["user"][:40].replace("|", "/").replace("\n", " ")
        m, b = r["mono"], r["multi"]
        a_inj = "—" if m["injection_attempt"] is None else ("inj✓" if m["injection_attempt"] else "inj✗")
        a_top = "—" if m["on_topic"] is None else ("on" if m["on_topic"] else "off")
        a_act = m["answer"]["action"] if m["answer"] else "—(сломан)"
        b_inj = "—" if b["injection_attempt"] is None else ("inj✓" if b["injection_attempt"] else "inj✗")
        b_top = "—" if b["on_topic"] is None else ("on" if b["on_topic"] else "off")
        b_act = b["answer"]["action"] if b["answer"] else "—"
        L.append(f"| {u}… | {a_inj} / {a_top} / {a_act} | {b_inj} / {b_top} / {b_act} |")
    L.append("")

    # спец-строка: утечка инъекции (вернул ли вариант то, что требовал атакующий)
    inj_rows = [r for r in rows if _is_injection_row(r)]
    if inj_rows:
        r = inj_rows[0]
        m_ans, b_ans = r["mono"]["answer"], r["multi"]["answer"]
        m_leak = m_ans is not None and m_ans.get("category") == _INJECTION_TARGET["category"] and m_ans.get("priority") == _INJECTION_TARGET["priority"]
        b_leak = b_ans is not None and b_ans.get("category") == _INJECTION_TARGET["category"] and b_ans.get("priority") == _INJECTION_TARGET["priority"]
        L.append(
            f"**Тест на утечку инъекции** (атакующий требовал `{_INJECTION_TARGET['category']}/"
            f"{_INJECTION_TARGET['priority']}`):\n"
            f"- A monolithic вернул требуемое атакующим: **{'ДА ⚠️ (поддался)' if m_leak else 'нет ✅'}** "
            f"(итог: {_fmt_ans(m_ans)}).\n"
            f"- B multi-stage вернул требуемое атакующим: **{'ДА ⚠️' if b_leak else 'нет ✅'}** "
            f"(итог: {_fmt_ans(b_ans)}; классификация {'пропущена — сработало короткое замыкание по инъекции' if b_ans and b_ans.get('category') is None else 'выполнена'}).\n"
        )

    # === 4. Детерминизм маршрута ===
    mono_checked = [r for r in rows if r["mono"]["rule_consistent"] is not None]
    mono_violations = [r for r in mono_checked if r["mono"]["rule_consistent"] is False]
    L.append("## 4. Детерминизм маршрута (соблюдены ли правила team/sla/needs_human/action)\n")
    L.append(
        f"- **A**: модель сама вычисляет маршрут по правилам из промпта → нарушила собственные "
        f"правила в **{len(mono_violations)}/{len(mono_checked)}** проверяемых ответах.\n"
        "- **B**: маршрут считает `decide()` в коде → нарушений **0 by construction** "
        "(бизнес-логику LLM вообще не отдаём).\n"
    )
    if mono_violations:
        L.append("  - A разошёлся с правилами на: " + "; ".join(
            f"«{r['user'][:30]}…» (модель дала {pl._route_fields(r['mono']['raw'])})" for r in mono_violations
        ) + "\n")

    # === 5. Цена / латентность / вызовы ===
    mono_cost = sum(_cost(r["mono"]["tokens"]) for r in rows)
    multi_cost = sum(_cost(r["multi"]["tokens"]) for r in rows)
    mono_calls = sum(r["mono"]["n_llm_calls"] for r in rows)
    multi_calls = sum(r["multi"]["n_llm_calls"] for r in rows)
    mono_lat = sum(r["mono"]["latency_sec"] for r in rows)
    multi_lat = sum(r["multi"]["latency_sec"] for r in rows)
    mono_tok = sum(r["mono"]["tokens"]["prompt"] + r["mono"]["tokens"]["completion"] for r in rows)
    multi_tok = sum(r["multi"]["tokens"]["prompt"] + r["multi"]["tokens"]["completion"] for r in rows)
    L.append("## 5. Цена / латентность / вызовы (весь набор, оценка по прайс-константам confidence_harness)\n")
    L.append("| Вариант | $ | LLM-вызовов | токенов | сумм. латентность, c | сред. латентность/вход, c |")
    L.append("|---|---|---|---|---|---|")
    L.append(f"| A monolithic | ${mono_cost:.4f} | {mono_calls} | {mono_tok} | {mono_lat:.1f} | {mono_lat/n:.2f} |")
    L.append(f"| B multi-stage | ${multi_cost:.4f} | {multi_calls} | {multi_tok} | {multi_lat:.1f} | {multi_lat/n:.2f} |")
    ratio = multi_cost / mono_cost if mono_cost else 0
    L.append(
        f"\n_Декомпозиция здесь ×{ratio:.2f} по цене к монолиту. Ждать удешевления «в лоб» НЕ стоит: "
        "два коротких промпта повторяют контекст, а на on-topic входах B делает 2 вызова против 1 у A "
        "(короткое замыкание экономит только на noisy). Реальная экономия декомпозиции — когда дешёвый "
        "stage1 отсеивает мусор ДО дорогого этапа, а сильную модель включаешь только на stage2 трудных "
        "(см. EXPLAINER, «разные модели по этапам»). На равной модели B покупает не цену, а надёжность/"
        "детерминизм/отлаживаемость._\n"
    )

    # === 6. Построчно ===
    L.append("## 6. Построчные решения\n")
    L.append("| bucket | вход (обрезано) | gold | A: cat/prio→action | B: cat/prio→action (note) |")
    L.append("|---|---|---|---|---|")
    for r in rows:
        u = r["user"][:34].replace("|", "/").replace("\n", " ")
        gold = f"{r['gold']['category']}/{r['gold']['priority']}" if r["gold"] else "—"
        L.append(
            f"| {r['bucket']} | {u}… | {gold} | {_fmt_ans(r['mono']['answer'])} | "
            f"{_fmt_ans(r['multi']['answer'])} ({r['multi']['note']}) |"
        )
    L.append("")

    # === 7. Выводы ===
    L.append("## 7. Выводы — когда моно, когда декомпозиция\n")
    L.append(
        "- **monolithic хорош**, когда вход чистый и предсказуемый, полей немного, а стоимость ошибки "
        "низкая: один вызов — минимум латентности и токенов, меньше кода. Плата — хрупкость: широкая "
        "схема легче ломается целиком, модель может поддаться инъекции (нормализация и классификация "
        "смешаны) и нарушить собственные правила маршрута (LLM считает бизнес-логику ненадёжно).\n"
        "- **multi-stage хорош**, когда вход шумный/враждебный, полей много и часть решения — жёсткие "
        "бизнес-правила: нормализация в отдельном этапе гасит инъекцию/транслит ДО классификации; "
        "строгий формат каждого этапа локализует поломку; **детерминированные условия уходят в код** "
        "(`decide()`) — 0 стоимости и 0 нарушений. Плата — больше вызовов/латентности на чистых входах "
        "и больше кода.\n"
        "- **Правило выбора:** декомпозируй там, где один запрос заставляет модель делать НЕСКОЛЬКО "
        "разнородных вещей сразу (очистка + классификация + арифметика правил) — раздели их, а всё, что "
        "выразимо детерминированными условиями, вынеси из LLM в код. Дешевле по деньгам это делает не "
        "само дробление, а возможность гнать разные этапы на разных (более дешёвых) моделях и коротко "
        "замыкать пайплайн на мусоре.\n"
    )
    return "\n".join(L)


def _no_run_report(reason: str) -> str:
    return (
        "# Отчёт: декомпозиция инференса (monolithic vs multi-stage)\n\n"
        "**Прогон НЕ выполнен.** Код готов, но реальный вызов DeepSeek не состоялся:\n\n"
        f"> {reason}\n\n"
        "Проверь `DEEPSEEK_API_KEY` (env или `backend/.env`) и доступность `api.deepseek.com`, "
        "затем перезапусти `python3 run_decomposition.py`.\n"
    )


async def _amain() -> int:
    api_key = ch.load_api_key()
    if not api_key:
        _REPORT_PATH.write_text(_no_run_report("DEEPSEEK_API_KEY не найден ни в env, ни в backend/.env."), encoding="utf-8")
        print("DEEPSEEK_API_KEY не найден — прогон пропущен, см. report.md", file=sys.stderr)
        return 1

    prompts = (
        _MONO_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        _STAGE1_PROMPT_PATH.read_text(encoding="utf-8").strip(),
        _STAGE2_PROMPT_PATH.read_text(encoding="utf-8").strip(),
    )
    inputs = _load_inputs()
    print(f"model={pl.DEFAULT_MODEL}. Входов: {len(inputs)}. Старт прогона (A monolithic + B multi-stage)…")

    try:
        rows = await _run_all(inputs, prompts, api_key)
    except Exception as exc:
        _REPORT_PATH.write_text(_no_run_report(f"Ошибка при прогоне: {type(exc).__name__}."), encoding="utf-8")
        print(f"Прогон прерван: {type(exc).__name__} — см. report.md", file=sys.stderr)
        return 1

    _REPORT_PATH.write_text(_build_report(rows), encoding="utf-8")
    print(f"Готово. report.md записан ({len(rows)} входов).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
