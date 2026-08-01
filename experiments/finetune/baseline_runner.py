#!/usr/bin/env python3
"""Провайдер-агностичный раннер baseline для classification тикетов jWorkPlace.

Учебный сайдкар (experiments/finetune/), не прод-код backend'а. Берёт первые N примеров
из data/eval.jsonl, шлёт (system, user) любому OpenAI-совместимому чат-эндпоинту
(Ollama /v1, DeepSeek, OpenAI — тот же клиент, отличается только base_url/model),
парсит ответ модели как JSON {"category","priority"} и считает per-field accuracy
против эталона из датасета. Невалидный/неполный JSON-ответ засчитывается как промах
по обоим полям, не роняет прогон.

Секреты: ключ ТОЛЬКО из env/CLI-аргумента, никогда не печатается и не логируется
(инвариант CLAUDE.md — секреты не в лог/ответ/промпт).

Использование:
    python3 baseline_runner.py --base-url http://localhost:11434/v1 --model jwp-qwen \
        [--api-key-env OLLAMA_API_KEY] [--n 10] [--eval data/eval.jsonl] \
        [--out baseline_responses.json]

Для DeepSeek:
    python3 baseline_runner.py --base-url https://api.deepseek.com/v1 --model deepseek-chat \
        --api-key-env DEEPSEEK_API_KEY
"""
import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_N = 10
DEFAULT_EVAL_PATH = Path(__file__).parent / "data" / "eval.jsonl"
DEFAULT_OUT_PATH = Path(__file__).parent / "baseline_responses.json"


def _load_eval_examples(path: Path, n: int) -> list[dict]:
    examples = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            examples.append(json.loads(line))
            if len(examples) >= n:
                break
    return examples


def _extract_gold(record: dict) -> dict:
    assistant_content = record["messages"][2]["content"]
    return json.loads(assistant_content)


def _parse_model_json(raw_text: str) -> dict | None:
    """Пытается вытащить {"category":...,"priority":...} из сырого ответа модели.

    Модель может обернуть JSON в markdown-код-блок или добавить пробелы вокруг —
    терпимо к этому, но не пытается угадывать смысл при полностью невалидном ответе.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json"):]
        text = text.strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _call_chat(client, model: str, system: str, user: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0,
    )
    return response.choices[0].message.content or ""


def run(base_url: str, model: str, api_key: str, eval_path: Path, n: int, out_path: Path) -> dict:
    try:
        from openai import OpenAI
    except ImportError:
        print("Нужен пакет 'openai' (см. requirements.txt): pip install openai", file=sys.stderr)
        raise

    client = OpenAI(base_url=base_url, api_key=api_key or "not-needed")

    examples = _load_eval_examples(eval_path, n)
    results = []
    category_correct = 0
    priority_correct = 0
    exact_correct = 0
    valid_json_count = 0

    for idx, record in enumerate(examples):
        system = record["messages"][0]["content"]
        user = record["messages"][1]["content"]
        gold = _extract_gold(record)

        try:
            raw_response = _call_chat(client, model, system, user)
        except Exception as exc:  # сетевой/API-сбой одного примера не должен ронять весь прогон
            raw_response = ""
            print(f"[{idx}] ошибка вызова модели: {type(exc).__name__}", file=sys.stderr)

        parsed = _parse_model_json(raw_response)
        pred_category = parsed.get("category") if parsed else None
        pred_priority = parsed.get("priority") if parsed else None

        if parsed is not None:
            valid_json_count += 1
        cat_ok = pred_category == gold["category"]
        prio_ok = pred_priority == gold["priority"]
        if cat_ok:
            category_correct += 1
        if prio_ok:
            priority_correct += 1
        if cat_ok and prio_ok:
            exact_correct += 1

        results.append({
            "index": idx,
            "user": user,
            "gold": gold,
            "raw_response": raw_response,
            "parsed": parsed,
            "category_correct": cat_ok,
            "priority_correct": prio_ok,
            "exact_correct": cat_ok and prio_ok,
        })

    n_total = len(examples)
    summary = {
        "model": model,
        "base_url": base_url,
        "n": n_total,
        "valid_json_ratio": valid_json_count / n_total if n_total else 0.0,
        "category_accuracy": category_correct / n_total if n_total else 0.0,
        "priority_accuracy": priority_correct / n_total if n_total else 0.0,
        "exact_match_accuracy": exact_correct / n_total if n_total else 0.0,
        "results": results,
    }

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="OpenAI-совместимый base_url (Ollama/DeepSeek/OpenAI)")
    parser.add_argument("--model", required=True, help="Имя модели у провайдера")
    parser.add_argument("--api-key-env", default="", help="Имя env-переменной с API-ключом (пусто — без ключа, напр. локальный Ollama)")
    parser.add_argument("--api-key-arg", default="", help="Ключ напрямую аргументом (не рекомендуется — виден в истории шелла); предпочитайте --api-key-env")
    parser.add_argument("--n", type=int, default=DEFAULT_N, help=f"Сколько примеров из eval.jsonl взять (по умолчанию {DEFAULT_N})")
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL_PATH, help="Путь к eval.jsonl")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_PATH, help="Путь для сохранения ответов+метрик")
    args = parser.parse_args()

    api_key = ""
    if args.api_key_env:
        api_key = os.environ.get(args.api_key_env, "")
        if not api_key:
            print(f"Предупреждение: env {args.api_key_env} пуст или не задан.", file=sys.stderr)
    elif args.api_key_arg:
        api_key = args.api_key_arg

    summary = run(args.base_url, args.model, api_key, args.eval, args.n, args.out)

    print(f"model={summary['model']} base_url={summary['base_url']} n={summary['n']}")
    print(f"  доля валидного JSON:     {summary['valid_json_ratio']:.1%}")
    print(f"  category accuracy:       {summary['category_accuracy']:.1%}")
    print(f"  priority accuracy:       {summary['priority_accuracy']:.1%}")
    print(f"  exact-match accuracy:    {summary['exact_match_accuracy']:.1%}")
    print(f"  ответы сохранены в:      {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
