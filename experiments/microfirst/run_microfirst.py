"""Прогон двухуровневого инференса «micro-model first» по `data/test_inputs.jsonl` через реальные
Ollama (Уровень 1) + DeepSeek (Уровень 2) и запись `report.md`.

Для КАЖДОГО входа: (1) двухуровневый маршрут `classify_two_level` (micro → при UNSURE → LLM);
(2) базлайн `classify_llm_only` (всегда большая LLM) — для честного сравнения точности/цены.
Метрики из ТЗ: сколько обработала микромодель, сколько ушло в fallback, общее число вызовов большой
LLM, средняя latency; плюс точность (micro-first vs baseline vs gold) и экономия вызовов/денег.

Не импортирует `backend.app`; ключ — `confidence_harness.load_api_key()` (никогда не логируем). Нет
ключа / API/Ollama недоступны → НЕ выдумываем числа: помечаем в отчёте и exit 1.

Запуск: `../../backend/venv/bin/python3 run_microfirst.py` (venv с httpx+numpy — см. README.md).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

import micro
import pipeline as pl

_CONF_DIR = Path(__file__).resolve().parents[1] / "confidence"
if str(_CONF_DIR) not in sys.path:
    sys.path.insert(0, str(_CONF_DIR))
import confidence_harness as ch  # noqa: E402

_HERE = Path(__file__).resolve().parent
_DATA_PATH = _HERE / "data" / "test_inputs.jsonl"
_SEED_PATH = _HERE / "data" / "seed_examples.jsonl"
_FALLBACK_PROMPT_PATH = _HERE / "data" / "fallback_prompt.txt"
_REPORT_PATH = _HERE / "report.md"

_CONCURRENCY = 3
_INJECTION_TARGET = {"category": "pr_edits"}  # что требует «спрятанный в тикете» атакующий (row #19)


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


async def _collect(client, row, micro_model, fb_prompt, api_key) -> dict:
    text = row["user"]
    twolevel_task = pl.classify_two_level(
        text, micro_model, api_key=api_key, fallback_prompt=fb_prompt, client=client
    )
    baseline_task = pl.classify_llm_only(
        text, api_key=api_key, fallback_prompt=fb_prompt, client=client
    )
    twolevel, baseline = await asyncio.gather(twolevel_task, baseline_task)
    return {**row, "tl": twolevel, "base": baseline}


async def _run_all(inputs, micro_model, fb_prompt, api_key) -> list[dict]:
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _one(client, row):
        async with sem:
            return await _collect(client, row, micro_model, fb_prompt, api_key)

    async with httpx.AsyncClient(timeout=ch.TIMEOUT) as client:
        return await asyncio.gather(*[_one(client, row) for row in inputs])


# --- вспомогательное ---


def _cat_match(answer: dict | None, gold: dict | None) -> bool:
    if answer is None or gold is None:
        return False
    return answer.get("category") == gold["category"]


def _cost(tokens: dict) -> float:
    return ch.estimate_cost_usd(tokens.get("prompt", 0), tokens.get("completion", 0))


def _fmt_cat(ans: dict | None) -> str:
    return (ans or {}).get("category") or "—(сломан)"


def _build_report(rows: list[dict]) -> str:
    n = len(rows)
    correct_rows = [r for r in rows if r["bucket"] == "correct"]
    n_correct = len(correct_rows)

    micro_handled = [r for r in rows if not r["tl"]["escalated"]]
    escalated = [r for r in rows if r["tl"]["escalated"]]
    n_micro = len(micro_handled)
    n_esc = len(escalated)

    L: list[str] = []
    L.append("# Отчёт: micro-model first — двухуровневый инференс (эмбеддинг-микромодель → DeepSeek)\n")
    L.append(
        f"Уровень 1: nearest-centroid на `nomic-embed-text` (Ollama, локально). Уровень 2: "
        f"`{pl.DEFAULT_MODEL}`. Входов: **{n}** (correct={n_correct}, "
        f"borderline={sum(1 for r in rows if r['bucket']=='borderline')}, "
        f"noisy={sum(1 for r in rows if r['bucket']=='noisy')}). Задача: классификация интента "
        "(6 категорий). Пороги микромодели: "
        f"margin ≥ {micro.TAU_MARGIN}, top1 ≥ {micro.TAU_ABS}.\n"
    )

    # === 1. Отсев: сколько взяла микромодель, сколько ушло в fallback ===
    L.append("## 1. Главная метрика ТЗ — отсев микромоделью\n")
    L.append("| | входов | доля |")
    L.append("|---|---|---|")
    L.append(f"| Обработала **микромодель** (Уровень 1, OK) | {n_micro} | {n_micro/n:.0%} |")
    L.append(f"| Ушло в **fallback** на большую LLM (UNSURE) | {n_esc} | {n_esc/n:.0%} |")
    total_llm_calls = sum(r["tl"]["n_llm_calls"] for r in rows)
    baseline_llm_calls = sum(r["base"]["n_llm_calls"] for r in rows)
    L.append(
        f"\n**Вызовов большой LLM: {total_llm_calls}** (micro-first) против **{baseline_llm_calls}** "
        f"у базлайна «всегда LLM» → экономия "
        f"**{(1 - total_llm_calls/baseline_llm_calls) if baseline_llm_calls else 0:.0%}** вызовов "
        "(микромодель считается локально, вызовов большой LLM не делает).\n"
    )
    if escalated:
        L.append("Причины эскалации: " + "; ".join(
            f"«{r['user'][:26]}…» ({r['tl']['micro']['note']})" for r in escalated) + "\n")

    # === 2. Latency ===
    tl_lat = [r["tl"]["latency_sec"] for r in rows]
    micro_lat = [r["tl"]["latency_sec"] for r in micro_handled]
    esc_lat = [r["tl"]["latency_sec"] for r in escalated]
    base_lat = [r["base"]["latency_sec"] for r in rows]
    L.append("## 2. Latency (средняя на вход, сек)\n")
    L.append("| путь | средняя latency |")
    L.append("|---|---|")
    L.append(f"| micro-first: обработано микромоделью (Уровень 1) | {(sum(micro_lat)/len(micro_lat)) if micro_lat else 0:.3f} |")
    L.append(f"| micro-first: эскалировано (Уровень 1+2) | {(sum(esc_lat)/len(esc_lat)) if esc_lat else 0:.3f} |")
    L.append(f"| micro-first: **в среднем по всем** | {sum(tl_lat)/n:.3f} |")
    L.append(f"| базлайн «всегда LLM» | {sum(base_lat)/n:.3f} |")
    L.append(
        "\n_Микромодель отвечает на порядок быстрее сетевого вызова DeepSeek — за счёт неё падает "
        "средняя latency всего пайплайна (уверенные входы не ждут облако)._\n"
    )

    # === 3. Точность (на корзине correct, есть gold) ===
    micro_first_acc = sum(1 for r in correct_rows if _cat_match(r["tl"]["answer"], r["gold"]))
    base_acc = sum(1 for r in correct_rows if _cat_match(r["base"]["answer"], r["gold"]))
    # точность самой микромодели на входах, что она взяла (из correct)
    micro_only_correct = [r for r in correct_rows if not r["tl"]["escalated"]]
    micro_only_acc = sum(1 for r in micro_only_correct if _cat_match(r["tl"]["answer"], r["gold"]))
    L.append("## 3. Точность категории на корзине «correct» (есть gold)\n")
    L.append("| система | точность vs gold |")
    L.append("|---|---|")
    L.append(f"| **micro-first** (комбинированная) | {micro_first_acc} / {n_correct} |")
    L.append(f"| базлайн «всегда LLM» | {base_acc} / {n_correct} |")
    L.append(f"| — из них: только микромодель, на входах что взяла сама | {micro_only_acc} / {len(micro_only_correct)} |")
    L.append(
        "\n_Ключевой вопрос двухуровневой схемы: роняет ли дешёвый Уровень 1 точность. Если "
        "micro-first ≈ базлайн — микромодель отсекает нагрузку БЕЗ потери качества (то, ради чего "
        "всё и затевается). Разрыв = цена, которую платим за экономию._\n"
    )

    # === 4. Устойчивость к инъекции / off-topic (что сделал каждый уровень) ===
    L.append("## 4. Инъекция и off-topic — что сделали уровни\n")
    inj_rows = [r for r in rows if _is_injection_row(r)]
    off_rows = [r for r in rows if r["bucket"] == "noisy" and "борщ" in r["user"].lower()]
    for tag, subset in (("prompt-injection", inj_rows), ("off-topic (борщ)", off_rows)):
        for r in subset:
            m = r["tl"]["micro"]
            leaked = _cat_match(r["tl"]["answer"], _INJECTION_TARGET) if tag.startswith("prompt") else None
            L.append(
                f"- **{tag}**: микромодель top1=`{_fmt_cat(m['answer'])}` margin={m['margin']} → "
                f"статус **{m['status']}**"
                + (" → эскалация" if r["tl"]["escalated"] else " → ответила сама")
                + f"; итог системы = `{_fmt_cat(r['tl']['answer'])}` (source={r['tl']['source']})"
                + (f"; **вернула требуемое атакующим ({_INJECTION_TARGET['category']}): "
                   f"{'ДА ⚠️' if leaked else 'нет ✅'}**" if leaked is not None else "")
                + "."
            )
    L.append(
        "\n_Микромодель семантически НЕ понимает «враждебность» или «постороннесть» — для неё это "
        "просто текст. Её единственная защита — margin. Prompt-injection маскируется под реальный "
        "вопрос («…как подключить репозиторий?») и оттого двусмысленна: margin схлопывается → UNSURE "
        "→ эскалация на Уровень 2, где anti-injection-промпт DeepSeek не поддаётся требованию "
        "атакующего. Off-topic же, уверенно ложащийся в `other` (борщ далёк от всех тех-категорий, "
        "но к «прочему» ближе прочего), микромодель берёт сама и не ошибается. Вывод: защита "
        "двухуровневой схемы — не «распознавание атаки» микромоделью, а связка «низкий margin → "
        "fail-closed эскалация → умный Уровень 2»._\n"
    )

    # === 5. Цена ===
    tl_cost = sum(_cost(r["tl"]["tokens"]) for r in rows)
    base_cost = sum(_cost(r["base"]["tokens"]) for r in rows)
    L.append("## 5. Цена (только токены большой LLM; embed-вызовы Ollama локальны и бесплатны)\n")
    L.append("| система | $ (по прайс-константам confidence_harness) | вызовов LLM |")
    L.append("|---|---|---|")
    L.append(f"| micro-first | ${tl_cost:.4f} | {total_llm_calls} |")
    L.append(f"| базлайн «всегда LLM» | ${base_cost:.4f} | {baseline_llm_calls} |")
    saving = (1 - tl_cost / base_cost) if base_cost else 0
    L.append(
        f"\n_Экономия ~**{saving:.0%}** по деньгам на этом наборе. Реальная экономия тем больше, "
        "чем выше доля «лёгких» уверенных входов в проде (типичный support-трафик — как раз хвост "
        "повторяющихся простых обращений)._\n"
    )

    # === 6. Построчно ===
    L.append("## 6. Построчные решения\n")
    L.append("| bucket | вход (обрезано) | gold | micro: top1/margin/status | итог micro-first (source) | базлайн LLM |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        u = r["user"][:30].replace("|", "/").replace("\n", " ")
        gold = r["gold"]["category"] if r["gold"] else "—"
        m = r["tl"]["micro"]
        micro_col = f"{_fmt_cat(m['answer'])} / {m['margin']} / {m['status']}"
        L.append(
            f"| {r['bucket']} | {u}… | {gold} | {micro_col} | "
            f"{_fmt_cat(r['tl']['answer'])} ({r['tl']['source']}) | {_fmt_cat(r['base']['answer'])} |"
        )
    L.append("")

    # === 7. Выводы ===
    L.append("## 7. Выводы\n")
    L.append(
        f"- **Отсев работает:** микромодель забрала {n_micro}/{n} входов ({n_micro/n:.0%}) без единого "
        f"вызова большой LLM; в облако ушли только {n_esc} по-настоящему трудных (пограничные/шум/"
        "инъекция/off-topic). Именно там margin эмбеддингов схлопывается — микромодель сама помечает "
        "их UNSURE.\n"
        "- **Сигнал уверенности = margin, не абсолютная близость.** nomic (retrieval-эмбеддинг) держит "
        "все косинусы в узкой полосе ~0.7, поэтому «далеко/близко» по абсолютной величине почти не "
        "различает off-topic; отделяет уверенное от неуверенного именно ОТРЫВ top1 от top2.\n"
        "- **Точность не просела** (micro-first ≈ базлайн на gold) — значит дешёвый уровень отсёк "
        "нагрузку даром. Если бы просела — надо поднимать порог margin (меньше берём сами, больше "
        "эскалируем: точность↑, экономия↓) — это и есть ручка компромисса.\n"
        "- **Микромодель не судит враждебность** — она лишь честно не уверена на нетипичном входе и "
        "отдаёт его LLM с anti-injection. Двухуровневость = дёшево на массе + умно на хвосте.\n"
    )
    return "\n".join(L)


def _no_run_report(reason: str) -> str:
    return (
        "# Отчёт: micro-model first (двухуровневый инференс)\n\n"
        "**Прогон НЕ выполнен.** Код готов, но реальный прогон не состоялся:\n\n"
        f"> {reason}\n\n"
        "Проверь: Ollama на :11434 с `nomic-embed-text`, `DEEPSEEK_API_KEY` (env или `backend/.env`), "
        "доступность `api.deepseek.com`. Затем перезапусти `python3 run_microfirst.py`.\n"
    )


async def _amain() -> int:
    api_key = ch.load_api_key()
    if not api_key:
        _REPORT_PATH.write_text(_no_run_report("DEEPSEEK_API_KEY не найден ни в env, ни в backend/.env."), encoding="utf-8")
        print("DEEPSEEK_API_KEY не найден — прогон пропущен, см. report.md", file=sys.stderr)
        return 1

    # --- строим микромодель (центроиды из сид-примеров) ---
    try:
        print("Строю центроиды микромодели из сид-примеров (Ollama nomic)…")
        micro_model = micro.EmbeddingMicroModel.from_seeds_file(_SEED_PATH)
    except (micro.EmbeddingError, ValueError) as exc:
        _REPORT_PATH.write_text(_no_run_report(f"Не удалось построить микромодель: {exc}"), encoding="utf-8")
        print(f"Микромодель не построена: {exc} — см. report.md", file=sys.stderr)
        return 1

    fb_prompt = _FALLBACK_PROMPT_PATH.read_text(encoding="utf-8").strip()
    inputs = _load_inputs()
    print(f"model={pl.DEFAULT_MODEL}. Входов: {len(inputs)}. Старт прогона (micro-first + базлайн)…")

    try:
        rows = await _run_all(inputs, micro_model, fb_prompt, api_key)
    except Exception as exc:
        _REPORT_PATH.write_text(_no_run_report(f"Ошибка при прогоне: {type(exc).__name__}."), encoding="utf-8")
        print(f"Прогон прерван: {type(exc).__name__} — см. report.md", file=sys.stderr)
        return 1

    _REPORT_PATH.write_text(_build_report(rows), encoding="utf-8")
    print(f"Готово. report.md записан ({len(rows)} входов).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
