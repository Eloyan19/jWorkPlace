#!/usr/bin/env python3
"""PR-качество Этапа 3a: доля предложенных правок, чей diff проходит `git apply --check`.

Индексирует golden-репо (или переиспользует по --project-id), затем для каждой инструкции
гоняет НАСТОЯЩИЙ путь генерации правки: retrieve → should_abstain гейт → build_edit_context →
llm.chat(JSON) → parse_and_validate_edits → assemble_diff → check_apply. Метрика: доля cases,
давших непустой diff, проходящий git apply --check. Для negatives — доля корректного отказа
(гейт «не могу»: abstain ИЛИ пустые edits, без валидного патча).

Требует DEEPSEEK_API_KEY (или готовый индекс + --project-id). Если ключ пуст — печатает
понятное сообщение и exit(0) (нужен прогон на VPS).

Запуск (из backend/ с активным .venv, Ollama на :11434):
    JWP_DATA_DIR=/tmp/jwp-eval python ../eval/pr_quality.py \\
        --golden ../eval/golden_edits.json

Или на готовом индексе:
    ... --project-id <id>
"""
import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

# backend/ в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.edit import patcher  # noqa: E402
from app.indexing import hybrid, pipeline, validation  # noqa: E402
from app.llm.deepseek import LlmError, get_llm  # noqa: E402

_K = 8
_CONTEXT_N = 6


def _index_repo(url: str) -> str:
    ref = validation.parse_github_url(url)
    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    t0 = time.time()
    pipeline._run_full(project_id, ref.url, reindex=False)
    print(f"  проиндексировано за {time.time() - t0:.1f} с, чанков: {db.chunk_count(project_id)}")
    return project_id


async def _propose(project_id: str, instruction: str) -> tuple[bool, str]:
    """Прогнать инструкцию через полный путь правки. Возвращает (patch_ok, diff).

    patch_ok — непустой diff, прошедший git apply --check. Гейт/отсутствие edits → (False, "").
    """
    hits = hybrid.hybrid_search(project_id, instruction, k=_K)
    abstain, _reason = hybrid.should_abstain(hits)
    if abstain:
        return False, ""

    context_hits = hits[:_CONTEXT_N]
    context = patcher.build_edit_context(context_hits, project_id, n=_CONTEXT_N)
    system = {"role": "system", "content": f"{patcher.EDIT_SYSTEM_PROMPT}\n\nФрагменты:\n{context}"}
    messages = [system, {"role": "user", "content": instruction}]

    try:
        llm = get_llm(get_settings())
        raw = await llm.chat(messages, response_format={"type": "json_object"},
                             temperature=0.0, max_tokens=2048)
        _summary, edits, _dropped = patcher.parse_and_validate_edits(raw, context_hits, project_id)
        if not edits:
            return False, ""
        diff = patcher.assemble_diff(edits, project_id)
        return patcher.check_apply(project_id, diff), diff
    except LlmError as exc:
        print(f"    [LLM ERROR] {exc}")
        return False, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True)
    parser.add_argument("--project-id", default=None, help="переиспользовать готовый индекс")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.deepseek_api_key:
        print(
            "DEEPSEEK_API_KEY не задан в .env или переменной окружения.\n"
            "Эта метрика требует ключ для реального вызова DeepSeek API.\n"
            "Прогон с реальным ключом возможен только на VPS (jwork.jorchik.com).\n"
            "Для локальной разработки используйте тесты (pytest) с синтетическими данными.\n"
        )
        return 0

    golden = json.loads(Path(args.golden).read_text())
    db.init_db()

    project_id = args.project_id
    if project_id is None:
        print(f"Индексация {golden['repo']} …")
        project_id = _index_repo(golden["repo"])

    cases = golden.get("cases", [])
    negatives = golden.get("negatives", [])

    print(f"\n[pr-quality] {len(cases)} cases + {len(negatives)} negatives")
    print("=" * 80)

    valid_patches = 0
    print("\nПозитивные кейсы (ожидаем diff, проходящий git apply --check):")
    for case in cases:
        instruction = case["instruction"]
        patch_ok, _diff = asyncio.run(_propose(project_id, instruction))
        valid_patches += patch_ok
        mark = "✓" if patch_ok else "✗"
        print(f"  {mark} {instruction[:60]}")

    correct_refusals = 0
    print("\nНегативные кейсы (ожидаем отказ «не могу»):")
    for instruction in negatives:
        patch_ok, _diff = asyncio.run(_propose(project_id, instruction))
        refused = not patch_ok
        correct_refusals += refused
        mark = "✓" if refused else "✗"
        print(f"  {mark} refused={refused!s:5} · {instruction[:50]}")

    print("\n" + "=" * 80)
    total_cases = len(cases)
    total_neg = len(negatives)
    print("\nметрики:")
    print(f"  cases с валидным патчем (git apply --check): {valid_patches}/{total_cases} "
          f"= {valid_patches / total_cases if total_cases else 0:.2f}")
    if total_neg:
        print(f"  negatives с корректным отказом: {correct_refusals}/{total_neg} "
              f"= {correct_refusals / total_neg:.2f}")
    print("\n✓ Метрика PR-качества вычислена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
