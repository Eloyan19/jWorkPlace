#!/usr/bin/env python3
"""Grounded-точность Этапа 2b: валидные line-based цитаты + корректный abstain.

Индексирует golden-репо (или переиспользует по --project-id), затем для каждого вопроса
гоняет НАСТОЯЩИЙ путь генерации: retrieve → should_abstain гейт → build_context →
llm.chat(JSON) → parse_and_validate. Метрика: доля ответов с ≥1 валидной цитатой.
Для negatives — доля корректного abstain (без генерации).

Требует DEEPSEEK_API_KEY (или готовый индекс + --project-id). Если ключ пуст —
печатает понятное сообщение и exit(0) (нужен прогон на VPS).

Запуск (из backend/ с активным .venv, Ollama на :11434):
    JWP_DATA_DIR=/tmp/jwp-eval python ../eval/grounded_accuracy.py \\
        --golden ../eval/golden_markupsafe.json --k 5

Или А/Б на готовом индексе:
    ... --project-id <id>
"""
import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

# backend/ в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import db  # noqa: E402
from app.chat import grounding  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.indexing import hybrid, pipeline, validation  # noqa: E402
from app.llm.deepseek import get_llm, LlmError  # noqa: E402


def _index_repo(url: str) -> str:
    """Проиндексировать репо, вернуть project_id."""
    ref = validation.parse_github_url(url)
    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    t0 = time.time()
    pipeline._run_full(project_id, ref.url, reindex=False)
    print(f"  проиндексировано за {time.time() - t0:.1f} с, чанков: {db.chunk_count(project_id)}")
    return project_id


async def _answer_question(project_id: str, question: str) -> tuple[str, list[dict], bool]:
    """Прогнать один вопрос через полный путь генерации.

    Возвращает (answer, sources, abstain):
      - answer — текст ответа (может быть пустым)
      - sources — список валидных цитат
      - abstain — был ли гейт should_abstain
    """
    hits = hybrid.hybrid_search(project_id, question, k=8)
    abstain, _reason = hybrid.should_abstain(hits)

    if abstain:
        return "", [], True

    context_hits = hits[:6]
    context = grounding.build_context(context_hits, n=6)
    system = {
        "role": "system",
        "content": f"{grounding.SYSTEM_PROMPT}\n\nФрагменты:\n{context}"
    }
    messages = [system, {"role": "user", "content": question}]

    try:
        llm = get_llm(get_settings())
        raw = await llm.chat(messages, response_format={"type": "json_object"})
        answer, sources, _dropped = grounding.parse_and_validate(raw, context_hits, project_id)

        # Downgrade на пустых валидных цитатах — см. api/chat.py
        if not sources and answer:
            return "", [], True

        return answer, sources, False
    except LlmError as exc:
        # Сетевая ошибка, API недоступен и т.п. — логируем, не падаем
        print(f"    [LLM ERROR] {exc}")
        return "", [], False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--project-id", default=None, help="переиспользовать готовый индекс")
    args = parser.parse_args()

    # Проверка ключа перед полным запуском
    settings = get_settings()
    if not settings.deepseek_api_key:
        print(
            "DEEPSEEK_API_KEY не задан в .env или переменной окружения.\n"
            "Эта метрика требует ключ для реального вызова DeepSeek API.\n"
            "Прогон с реальным ключом возможен только на VPS (jwork.jorchik.com).\n"
            "Для локальной разработки используйте тесты (pytest) с мокированным LLM.\n"
        )
        return 0

    golden = json.loads(Path(args.golden).read_text())
    db.init_db()

    project_id = args.project_id
    if project_id is None:
        print(f"Индексация {golden['repo']} …")
        project_id = _index_repo(golden["repo"])

    # Async-цикл для генерации ответов
    import asyncio

    cases = golden.get("cases", [])
    negatives = golden.get("negatives", [])

    print(f"\n[grounded-accuracy] {len(cases)} cases + {len(negatives)} negatives")
    print("=" * 80)

    # Позитивные кейсы: ожидаем ≥1 валидную цитату
    cases_with_valid_quotes = 0
    print(f"\nПозитивные кейсы (ожидаем валидные цитаты):")
    for i, case in enumerate(cases, start=1):
        question = case["question"]
        answer, sources, abstain = asyncio.run(_answer_question(project_id, question))
        has_valid = len(sources) > 0
        cases_with_valid_quotes += has_valid
        mark = "✓" if has_valid else "✗"
        abstain_mark = " [ABSTAIN]" if abstain else ""
        print(f"  {mark} {question[:50]:50} → {len(sources)} цитат{abstain_mark}")

    # Негативные кейсы: ожидаем abstain БЕЗ генерации
    neg_correct_abstain = 0
    print(f"\nНегативные кейсы (ожидаем abstain без генерации):")
    for q in negatives:
        answer, sources, abstain = asyncio.run(_answer_question(project_id, q))
        is_correct = abstain and len(sources) == 0
        neg_correct_abstain += is_correct
        mark = "✓" if is_correct else "✗"
        print(f"  {mark} abstain={abstain!s:5} · {q[:50]}")

    # Итоговая статистика
    print("\n" + "=" * 80)
    total_cases = len(cases)
    total_neg = len(negatives)

    print(f"\nметрики:")
    print(f"  cases с ≥1 валидной цитатой: {cases_with_valid_quotes}/{total_cases} "
          f"= {cases_with_valid_quotes / total_cases if total_cases else 0:.2f}")

    if total_neg:
        print(f"  negatives с корректным abstain: {neg_correct_abstain}/{total_neg} "
              f"= {neg_correct_abstain / total_neg:.2f}")

    print("\n✓ Метрика grounded-точности вычислена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
