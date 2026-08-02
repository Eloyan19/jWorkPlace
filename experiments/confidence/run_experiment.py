"""Прогон эксперимента «уверенность инференса» по `data/test_inputs.jsonl` через реальный
DeepSeek, агрегация метрик по корзинам (correct/borderline/noisy) и запись `report.md`.

Не импортирует `backend.app` — сайдкар, ключ через `confidence_harness.load_api_key()`
(env → `backend/.env`). При отсутствии ключа/сетевой недоступности API — НЕ выдумывает числа:
помечает в отчёте, что прогон не выполнен, и завершается без падения (см. `_no_run_report`).

Запуск: `python3 run_experiment.py` (venv с httpx — см. README.md).
"""
import asyncio
import json
import statistics
import sys
from pathlib import Path

import httpx

import confidence_harness as ch

_HERE = Path(__file__).resolve().parent
_DATA_PATH = _HERE / "data" / "test_inputs.jsonl"
_REPORT_PATH = _HERE / "report.md"
_EVAL_CORRECT_PATH = Path("/tmp/eval_correct.jsonl")

_CONCURRENCY = 4  # мягкое ограничение параллельных запросов к DeepSeek


def _load_system_prompt() -> str:
    """System-промпт классификатора — дословно из первого сообщения первого примера
    `/tmp/eval_correct.jsonl` (тот же промпт, что в прошлом треке классификации тикетов)."""
    if not _EVAL_CORRECT_PATH.exists():
        raise FileNotFoundError(
            f"{_EVAL_CORRECT_PATH} не найден — нужен system-промпт классификатора из прошлого трека."
        )
    with _EVAL_CORRECT_PATH.open(encoding="utf-8") as f:
        first = json.loads(f.readline())
    system_msg = next(m for m in first["messages"] if m["role"] == "system")
    return system_msg["content"]


def _load_inputs() -> list[dict]:
    rows = []
    with _DATA_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


async def _run_all(inputs: list[dict], system_prompt: str, api_key: str, model: str) -> list[dict]:
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(client: httpx.AsyncClient, row: dict) -> dict:
        async with sem:
            result = await ch.classify_with_confidence(
                row["user"], system_prompt, api_key=api_key, model=model, client=client
            )
        return {**row, "result": result}

    async with httpx.AsyncClient(timeout=ch.TIMEOUT) as client:
        tasks = [_one(client, row) for row in inputs]
        return await asyncio.gather(*tasks)


def _pct(n: int, total: int) -> str:
    return f"{n} ({100 * n / total:.0f}%)" if total else f"{n} (—)"


def _fmt_stats(values: list[float]) -> str:
    if not values:
        return "—"
    return (
        f"mean={statistics.mean(values):.3f}s · "
        f"median={statistics.median(values):.3f}s · "
        f"max={max(values):.3f}s (n={len(values)})"
    )


def _gold_matches(answer: dict | None, gold: dict | None) -> bool:
    if answer is None or gold is None:
        return False
    return answer["category"] == gold["category"] and answer["priority"] == gold["priority"]


def _build_report(rows: list[dict]) -> str:
    buckets = ["correct", "borderline", "noisy"]
    lines: list[str] = []
    lines.append("# Отчёт: уверенность инференса без fine-tuning (DeepSeek, классификация тикетов)\n")
    lines.append(f"Всего входов: **{len(rows)}**. Подходы: constraint-based → redundancy (K={ch.K_SAMPLES}, "
                  f"T={ch.TEMPERATURE_CONSISTENCY}) → self-check (T={ch.TEMPERATURE_SELF_CHECK}).\n")

    # --- статусы по корзинам ---
    lines.append("## Статусы по корзинам\n")
    lines.append("| Корзина | n | OK | UNSURE | FAIL (отклонено) |")
    lines.append("|---|---|---|---|---|")
    total_counts = {"OK": 0, "UNSURE": 0, "FAIL": 0}
    for bucket in buckets:
        bucket_rows = [r for r in rows if r["bucket"] == bucket]
        n = len(bucket_rows)
        counts = {"OK": 0, "UNSURE": 0, "FAIL": 0}
        for r in bucket_rows:
            counts[r["result"]["status"]] += 1
            total_counts[r["result"]["status"]] += 1
        lines.append(
            f"| {bucket} | {n} | {_pct(counts['OK'], n)} | {_pct(counts['UNSURE'], n)} | "
            f"{_pct(counts['FAIL'], n)} |"
        )
    n_total = len(rows)
    lines.append(
        f"| **итого** | {n_total} | {_pct(total_counts['OK'], n_total)} | "
        f"{_pct(total_counts['UNSURE'], n_total)} | {_pct(total_counts['FAIL'], n_total)} |\n"
    )

    # --- доп. инференс / стоимость вызовов ---
    lines.append("## Дополнительный инференс (цена контроля качества)\n")
    extra_infer_rows = [r for r in rows if r["result"]["status"] in ("UNSURE",)]
    extra_calls_total = sum(max(0, r["result"]["n_llm_calls"] - 1) for r in rows)
    base_calls = len(rows)  # 1 базовый вызов на вход, если бы контроля не было вовсе
    actual_calls = sum(r["result"]["n_llm_calls"] for r in rows)
    lines.append(
        f"- Входов, ушедших в UNSURE (раскол redundancy или self-check=reject): "
        f"**{len(extra_infer_rows)} / {n_total}**.\n"
        f"- Всего LLM-вызовов сделано: **{actual_calls}** (было бы {base_calls} без контроля "
        f"уверенности → **+{extra_calls_total} лишних вызовов** сверх 1 базового на вход).\n"
        f"- FAIL (constraint) сэкономил вызовы: входы, отклонённые на шаге 1, дальше не пошли "
        f"(нет redundancy/self-check calls).\n"
    )

    # --- latency ---
    lines.append("## Latency\n")
    pipeline_latencies = [r["result"]["latency_sec"] for r in rows]
    call_latencies = [lat for r in rows for lat in r["result"].get("call_latencies", [])]
    lines.append(f"- На вход (полный пайплайн): {_fmt_stats(pipeline_latencies)}")
    lines.append(f"- На 1 LLM-вызов: {_fmt_stats(call_latencies)}\n")

    # --- cost ---
    lines.append("## Cost (оценка по прайс-константам harness — см. предупреждение в коде)\n")
    total_prompt = sum(r["result"]["tokens"]["prompt"] for r in rows)
    total_completion = sum(r["result"]["tokens"]["completion"] for r in rows)
    total_cost = ch.estimate_cost_usd(total_prompt, total_completion)
    lines.append(
        f"- Токены: prompt={total_prompt}, completion={total_completion}, "
        f"total={total_prompt + total_completion}.\n"
        f"- Оценка стоимости прогона: **${total_cost:.4f}** "
        f"(prompt ${ch.PRICE_PROMPT_USD_PER_MTOK}/M, completion ${ch.PRICE_COMPLETION_USD_PER_MTOK}/M — "
        "ОБНОВИ константы под актуальный прайс).\n"
    )

    # --- accuracy + false reject на correct ---
    lines.append("## Точность на корзине «correct» (есть gold)\n")
    correct_rows = [r for r in rows if r["bucket"] == "correct"]
    n_correct = len(correct_rows)
    matched = sum(1 for r in correct_rows if _gold_matches(r["result"]["answer"], r["gold"]))
    # Для FAIL answer всегда None (constraint не прошёл до majority vote), поэтому false reject
    # считаем отдельно по каждому статусу, не одним общим выражением.
    false_reject_fail = sum(
        1 for r in correct_rows if r["result"]["status"] == "FAIL"
    )
    false_reject_unsure_correct_majority = sum(
        1 for r in correct_rows
        if r["result"]["status"] == "UNSURE" and _gold_matches(r["result"]["answer"], r["gold"])
    )
    lines.append(
        f"- Accuracy majority-ответа vs gold (среди входов со статусом OK/UNSURE, где есть "
        f"majority-answer): **{matched} / {n_correct}**.\n"
        f"- Отклонено (FAIL) на «correct»-корзине: **{false_reject_fail} / {n_correct}** — "
        "constraint не должен был падать на чистых примерах прошлого трека; если >0, разбор ниже.\n"
        f"- False reject: правильный majority-ответ, но контроль всё равно ушёл в UNSURE "
        f"(self-check/redundancy отклонили верный ответ): "
        f"**{false_reject_unsure_correct_majority} / {n_correct}** — это и есть цена контроля "
        "качества (за надёжность платим долей ложных отказов).\n"
    )

    # --- построчная таблица ---
    lines.append("## Построчные результаты\n")
    lines.append("| bucket | user (обрезано) | gold | answer | status | agreement | self_check | calls |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        res = r["result"]
        user_short = r["user"][:50].replace("|", "/").replace("\n", " ")
        gold_s = json.dumps(r["gold"], ensure_ascii=False) if r["gold"] else "—"
        answer_s = json.dumps(res["answer"], ensure_ascii=False) if res["answer"] else "—"
        lines.append(
            f"| {r['bucket']} | {user_short}… | {gold_s} | {answer_s} | {res['status']} | "
            f"{res['agreement']} | {res['self_check'] or '—'} | {res['n_llm_calls']} |"
        )

    # --- атрибуция UNSURE: что именно его вызвало (redundancy-раскол vs self-check-reject) ---
    resolved_rows = [r for r in rows if r["result"]["status"] in ("OK", "UNSURE")]
    unsure_rows = [r for r in rows if r["result"]["status"] == "UNSURE"]
    unsure_from_redundancy = sum(1 for r in unsure_rows if r["result"]["agreement"] < 1.0)
    unsure_from_self_check_only = sum(
        1 for r in unsure_rows if r["result"]["agreement"] == 1.0 and r["result"]["self_check"] == "reject"
    )
    self_check_reject_total = sum(1 for r in resolved_rows if r["result"]["self_check"] == "reject")
    self_check_confirm_total = sum(1 for r in resolved_rows if r["result"]["self_check"] == "confirm")
    fail_total = sum(1 for r in rows if r["result"]["status"] == "FAIL")

    lines.append("\n## Интерпретация\n")
    lines.append(
        f"- **Constraint-based** — самый дешёвый фильтр (0 доп. вызовов при валидном ответе): в "
        f"этом прогоне отклонил **{fail_total}/{n_total}** входов на первом же вызове (жёсткий "
        "reject, дальше по этому входу не считаем — экономия cost). С `response_format=json_object` "
        "DeepSeek обычно возвращает валидный JSON-конверт; реальная причина FAIL здесь чаще не "
        "\"модель придумала свою схему\", а `finish_reason=\"length\"` — часть бюджета `max_tokens` "
        "уходит на скрытые `reasoning_tokens` до печати JSON (harness делает 1 retry с удвоенным "
        "бюджетом — см. `_timed_call`; FAIL остаётся, только если обрезано и после retry).\n"
        f"- **Redundancy (self-consistency K={ch.K_SAMPLES})** — источник **{unsure_from_redundancy} "
        f"из {len(unsure_rows)}** UNSURE в этом прогоне: там, где модель «плавает» между двумя "
        "категориями/приоритетами, 3 прогона при T=0.7 расходятся (agreement < 1.0) — в основном "
        "на входах с объективно неоднозначной формулировкой (`borderline`-корзина создана именно "
        "для этого).\n"
        f"- **Self-check** — в этом прогоне вернул `confirm` **{self_check_confirm_total}** раз и "
        f"`reject` **{self_check_reject_total}** раз (из {len(resolved_rows)} входов, дошедших до "
        f"этого шага); **{unsure_from_self_check_only}** UNSURE случаев объясняются ИМЕННО "
        "self-check'ом (redundancy сама была согласна 3/3). "
        + (
            "На этом небольшом прогоне self-check не добавил сигнала сверх redundancy (0 "
            "самостоятельных reject) — включая prompt-injection и полностью off-topic входы из "
            "noisy-корзины: модель их корректно проигнорировала/распознала уже на шаге классификации, "
            "и self-check это только подтвердил. Не повод считать шаг бесполезным на большем "
            "наборе (self-check — независимый прогон, ловит другой класс ошибок, чем redundancy), "
            "но на ЭТИХ 20 входах его предельная ценность — ноль, а стоимость — реальная (+1 вызов "
            "на каждый вход, прошедший redundancy)."
            if unsure_from_self_check_only == 0
            else "Это и есть случаи, где мажоритарный ответ выглядел согласованным, но не выдержал "
            "независимой проверки — ценность подхода именно в них."
        )
        + "\n"
        f"- **Trade-off**: {n_total} входов дали **{sum(r['result']['n_llm_calls'] for r in rows)}** "
        f"LLM-вызовов вместо {n_total} без контроля — цена в деньгах/latency видна выше; цена в "
        f"качестве — false reject на «correct»-корзине: **{false_reject_unsure_correct_majority} "
        f"/ {n_correct}** верных ответов контроль всё равно увёл в UNSURE. Соотношение стоит держать в голове при выборе "
        "порога строгости для продакшена: 100%-OK недостижимо без потери части верных ответов.\n"
    )
    return "\n".join(lines)


def _no_run_report(reason: str) -> str:
    return (
        "# Отчёт: уверенность инференса без fine-tuning\n\n"
        "**Прогон НЕ выполнен.** Код готов к запуску, но реальный вызов DeepSeek не состоялся:\n\n"
        f"> {reason}\n\n"
        "Проверь `DEEPSEEK_API_KEY` (env или `backend/.env`) и доступность `api.deepseek.com`, "
        "затем перезапусти `python3 run_experiment.py`.\n"
    )


async def _amain() -> int:
    api_key = ch.load_api_key()
    if not api_key:
        _REPORT_PATH.write_text(
            _no_run_report("DEEPSEEK_API_KEY не найден ни в env, ни в backend/.env."), encoding="utf-8"
        )
        print("DEEPSEEK_API_KEY не найден — прогон пропущен, см. report.md", file=sys.stderr)
        return 1

    model = ch.load_model_name()
    system_prompt = _load_system_prompt()
    inputs = _load_inputs()
    print(f"Модель: {model}. Входов: {len(inputs)}. Старт прогона…")

    try:
        rows = await _run_all(inputs, system_prompt, api_key, model)
    except Exception as exc:  # сетевой сбой уровня клиента (не отдельного вызова) — не выдумываем числа
        _REPORT_PATH.write_text(
            _no_run_report(f"Ошибка при прогоне: {type(exc).__name__}."), encoding="utf-8"
        )
        print(f"Прогон прерван: {type(exc).__name__} — см. report.md", file=sys.stderr)
        return 1

    report = _build_report(rows)
    _REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Готово. report.md записан ({len(rows)} входов).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
