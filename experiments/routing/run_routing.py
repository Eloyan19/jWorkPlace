"""Прогон routing-эксперимента по `data/test_inputs.jsonl` через реальный DeepSeek и запись
`report.md`: какие запросы остались на дешёвой `flash`, какие ушли на сильную `pro`, во что это
обошлось и что дало по точности — по трём эвристикам эскалации (length / constraint / confidence).

Схема прогона (экономная и методологически чистая): для КАЖДОГО входа один раз собираем три
сигнала — одиночный ответ flash, полный confidence-пайплайн flash, одиночный ответ pro — а затем
каждую политику ПРОСЧИТЫВАЕМ по этим общим сигналам (без новых вызовов). Так все три эвристики
судят по идентичным ответам моделей: разница в метриках идёт от решения о роутинге, а не от
разброса сэмплинга. «Стоимость политики» при этом честная — считается только по вызовам, которые
именно эта политика сделала бы в проде (см. `router.route`).

Не импортирует `backend.app`; ключ — через `confidence_harness.load_api_key()`. Нет ключа / API
недоступен → НЕ выдумывает числа: помечает в отчёте, что прогон не выполнен, и выходит с кодом 1.

Запуск: `../../backend/venv/bin/python3 run_routing.py` (venv с httpx — см. README.md).
"""
import asyncio
import json
import sys
from pathlib import Path

import httpx

import router as rt

_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402

_HERE = Path(__file__).resolve().parent
_DATA_PATH = _HERE / "data" / "test_inputs.jsonl"
_SYSTEM_PROMPT_PATH = _HERE / "data" / "system_prompt.txt"
_REPORT_PATH = _HERE / "report.md"

_CONCURRENCY = 3  # мягкий потолок параллельных входов (внутри входа ещё до 6 вызовов идут по очереди)
_POLICIES = list(rt.ESCALATION_POLICIES)  # ["length", "constraint", "confidence"]


def _load_inputs() -> list[dict]:
    rows = []
    with _DATA_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


async def _collect_signals(
    client: httpx.AsyncClient, row: dict, system_prompt: str, api_key: str
) -> dict:
    """Три независимых сигнала для одного входа, параллельно: small(flash) · confidence(flash) · big(pro)."""
    text = row["user"]
    small_task = rt.single_classify(client, api_key, rt.SMALL_MODEL, system_prompt, text, temperature=0.0)
    conf_task = ch.classify_with_confidence(
        text, system_prompt, api_key=api_key, model=rt.SMALL_MODEL, client=client
    )
    big_task = rt.single_classify(client, api_key, rt.BIG_MODEL, system_prompt, text, temperature=0.0)
    small, confidence, big = await asyncio.gather(small_task, conf_task, big_task)
    return {**row, "small": small, "confidence": confidence, "big": big}


async def _run_all(inputs: list[dict], system_prompt: str, api_key: str) -> list[dict]:
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(client: httpx.AsyncClient, row: dict) -> dict:
        async with sem:
            return await _collect_signals(client, row, system_prompt, api_key)

    async with httpx.AsyncClient(timeout=ch.TIMEOUT) as client:
        return await asyncio.gather(*[_one(client, row) for row in inputs])


# --- вспомогательное для отчёта ---


def _gold_matches(answer: dict | None, gold: dict | None) -> bool:
    if answer is None or gold is None:
        return False
    return answer["category"] == gold["category"] and answer["priority"] == gold["priority"]


def _pct(n: int, total: int) -> str:
    return f"{n} ({100 * n / total:.0f}%)" if total else f"{n} (—)"


def _build_report(rows: list[dict]) -> str:
    n_total = len(rows)
    buckets = ["correct", "borderline", "noisy"]
    correct_rows = [r for r in rows if r["bucket"] == "correct"]
    n_correct = len(correct_rows)
    big_ok = any(r["big"]["finish_reason"] != "error" for r in rows)

    # предпосчёт routed-результата каждой политики по каждому входу
    routed = {p: [rt.route(p, r) for r in rows] for p in _POLICIES}

    L: list[str] = []
    L.append("# Отчёт: routing между моделями DeepSeek с fallback-логикой\n")
    L.append(
        f"Дешёвая модель: `{rt.SMALL_MODEL}` · сильная (fallback): `{rt.BIG_MODEL}`. "
        f"Входов: **{n_total}**. Эвристики эскалации: {', '.join(f'`{p}`' for p in _POLICIES)}.\n"
    )
    if not big_ok:
        L.append(
            f"> ⚠️ **Ни один вызов `{rt.BIG_MODEL}` не удался** (модель недоступна/имя неверно). "
            "Метрики «all-big» и эскалаций ниже недостоверны — почини имя модели и перезапусти.\n"
        )

    # === 1. Роутинг: кто остался на flash, кто ушёл на pro ===
    L.append("## 1. Роутинг по эвристикам — кто остался на flash, кто ушёл на pro\n")
    L.append("| Эвристика | осталось на flash | ушло на pro (эскалация) | доля эскалаций |")
    L.append("|---|---|---|---|")
    for p in _POLICIES:
        esc = sum(1 for x in routed[p] if x["escalated"])
        stayed = n_total - esc
        share = f"{100 * esc / n_total:.0f}%" if n_total else "—"
        L.append(f"| `{p}` | {stayed} | {esc} | {share} |")
    L.append("")

    # разбивка эскалаций по корзинам
    L.append("### Эскалации по корзинам (сколько входов каждой корзины ушло на pro)\n")
    L.append("| Эвристика | correct | borderline | noisy |")
    L.append("|---|---|---|---|")
    for p in _POLICIES:
        cells = []
        for b in buckets:
            idxs = [i for i, r in enumerate(rows) if r["bucket"] == b]
            esc = sum(1 for i in idxs if routed[p][i]["escalated"])
            cells.append(_pct(esc, len(idxs)))
        L.append(f"| `{p}` | {cells[0]} | {cells[1]} | {cells[2]} |")
    L.append("")

    # === 2. Точность: all-small vs all-big vs routed (на корзине correct с gold) ===
    L.append("## 2. Точность на корзине «correct» (есть gold) — что даёт fallback\n")
    all_small_acc = sum(1 for r in correct_rows if _gold_matches(r["small"]["answer"], r["gold"]))
    all_big_acc = sum(1 for r in correct_rows if _gold_matches(r["big"]["answer"], r["gold"]))
    L.append("| Конфигурация | точность vs gold |")
    L.append("|---|---|")
    L.append(f"| всё на flash (без routing) | {all_small_acc} / {n_correct} |")
    L.append(f"| всё на pro (дорогой потолок) | {all_big_acc} / {n_correct} |")
    for p in _POLICIES:
        acc = sum(
            1 for i, r in enumerate(rows)
            if r["bucket"] == "correct" and _gold_matches(routed[p][i]["answer"], r["gold"])
        )
        L.append(f"| routing по `{p}` | {acc} / {n_correct} |")
    L.append(
        "\n_Читать так: routing выигрывает, когда его точность близка к «всё на pro», а стоимость "
        "(п.3) — к «всё на flash». Если flash и pro тут одинаково точны, выигрыш точности = 0, и "
        "смысл routing только в отлове брака/деградации, а не в приросте accuracy на этом наборе._\n"
    )

    # === 3. Стоимость: all-small vs all-big vs routed ===
    L.append("## 3. Стоимость прогона (оценка по прайс-константам `router.PRICE` — приблизительно)\n")
    all_small_cost = sum(rt.model_cost(rt.SMALL_MODEL, r["small"]["tokens"]) for r in rows)
    all_big_cost = sum(rt.model_cost(rt.BIG_MODEL, r["big"]["tokens"]) for r in rows)
    L.append("| Конфигурация | $ на весь набор | LLM-вызовов | к «всё на flash» |")
    L.append("|---|---|---|---|")
    small_calls = sum(r["small"]["n_calls"] for r in rows)
    big_calls_all = sum(r["big"]["n_calls"] for r in rows)
    L.append(f"| всё на flash | ${all_small_cost:.4f} | {small_calls} | ×1.0 |")
    ratio_big = all_big_cost / all_small_cost if all_small_cost else 0
    L.append(f"| всё на pro | ${all_big_cost:.4f} | {big_calls_all} | ×{ratio_big:.1f} |")
    for p in _POLICIES:
        cost = sum(x["cost_usd"] for x in routed[p])
        calls = sum(x["n_llm_calls"] for x in routed[p])
        ratio = cost / all_small_cost if all_small_cost else 0
        L.append(f"| routing по `{p}` | ${cost:.4f} | {calls} | ×{ratio:.1f} |")
    L.append(
        "\n_«confidence» дороже даже без единой эскалации: сама проба уверенности — это 3–4 вызова "
        "flash на вход (self-consistency + self-check). «length»/«constraint» — 1 вызов flash + pro "
        "только на реально эскалированных. Вот он trade-off силы сигнала против его цены._\n"
    )

    # === 4. Построчно: решение каждой эвристики по каждому входу ===
    L.append("## 4. Построчные решения роутинга\n")
    L.append("| bucket | вход (обрезано) | flash-ответ | pro-ответ | length | constraint | confidence |")
    L.append("|---|---|---|---|---|---|---|")
    for i, r in enumerate(rows):
        user_short = r["user"][:38].replace("|", "/").replace("\n", " ")
        sa = json.dumps(r["small"]["answer"], ensure_ascii=False) if r["small"]["answer"] else "—"
        ba = json.dumps(r["big"]["answer"], ensure_ascii=False) if r["big"]["answer"] else "—"

        def _decis(p: str) -> str:
            x = routed[p][i]
            return ("🔺pro " if x["escalated"] else "flash ") + f"({x['reason']})"

        L.append(
            f"| {r['bucket']} | {user_short}… | {sa} | {ba} | "
            f"{_decis('length')} | {_decis('constraint')} | {_decis('confidence')} |"
        )
    L.append("")

    # === 5. Выводы ===
    L.append("## 5. Выводы (плюсы/минусы эвристик)\n")
    conf_esc = sum(1 for x in routed["confidence"] if x["escalated"])
    len_esc = sum(1 for x in routed["length"] if x["escalated"])
    con_esc = sum(1 for x in routed["constraint"] if x["escalated"])
    conf_cost = sum(x["cost_usd"] for x in routed["confidence"])
    L.append(
        f"- **length** ({len_esc}/{n_total} эскалаций) — почти бесплатная, но слепая: срабатывает "
        "только на пустом/обрезанном ответе. В `response_format=json_object` flash почти всегда "
        "выдаёт непустой валидный JSON, так что эта эвристика на «здоровом» наборе молчит — дёшево, "
        "но и пользы ноль, пока модель не начнёт реально обрываться.\n"
        f"- **constraint** ({con_esc}/{n_total}) — тоже 1 вызов, но ловит формат-брак (не та схема, "
        "мусор вместо JSON), который length пропустит. Чуть сильнее length при той же цене; всё ещё "
        "не видит смысловых ошибок (правильная схема с неверной категорией её не тревожит).\n"
        f"- **confidence** ({conf_esc}/{n_total}, ${conf_cost:.4f}) — единственная, кто ловит "
        "семантическую неуверенность: эскалирует ровно там, где flash «плавает» между категориями "
        "(self-consistency раскол) или не проходит самопроверку. Платит за это 3–4× вызовов flash на "
        "КАЖДЫЙ вход, даже не эскалированный. Сильнейший сигнал — самая дорогая проба.\n"
        "- **Итог routing**: дешёвые эвристики — страховка от вырожденного вывода (почти даром, но "
        "редко срабатывают на робастной модели); confidence — реальный роутер по трудности запроса, "
        "но его проба стоит как несколько ответов. Разумный прод-выбор — каскад: сначала бесплатные "
        "length/constraint (отсечь явный брак), а дорогую confidence-пробу включать выборочно "
        "(напр. только для запросов с высокой ценой ошибки), а не на каждый запрос.\n"
    )
    return "\n".join(L)


def _no_run_report(reason: str) -> str:
    return (
        "# Отчёт: routing между моделями DeepSeek\n\n"
        "**Прогон НЕ выполнен.** Код готов, но реальный вызов DeepSeek не состоялся:\n\n"
        f"> {reason}\n\n"
        "Проверь `DEEPSEEK_API_KEY` (env или `backend/.env`) и доступность `api.deepseek.com`, "
        "затем перезапусти `python3 run_routing.py`.\n"
    )


async def _amain() -> int:
    api_key = ch.load_api_key()
    if not api_key:
        _REPORT_PATH.write_text(
            _no_run_report("DEEPSEEK_API_KEY не найден ни в env, ни в backend/.env."), encoding="utf-8"
        )
        print("DEEPSEEK_API_KEY не найден — прогон пропущен, см. report.md", file=sys.stderr)
        return 1

    system_prompt = _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    inputs = _load_inputs()
    print(f"flash={rt.SMALL_MODEL} · pro={rt.BIG_MODEL}. Входов: {len(inputs)}. Старт прогона…")

    try:
        rows = await _run_all(inputs, system_prompt, api_key)
    except Exception as exc:  # клиентский сбой (не отдельного вызова) — не выдумываем числа
        _REPORT_PATH.write_text(
            _no_run_report(f"Ошибка при прогоне: {type(exc).__name__}."), encoding="utf-8"
        )
        print(f"Прогон прерван: {type(exc).__name__} — см. report.md", file=sys.stderr)
        return 1

    _REPORT_PATH.write_text(_build_report(rows), encoding="utf-8")
    print(f"Готово. report.md записан ({len(rows)} входов).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
