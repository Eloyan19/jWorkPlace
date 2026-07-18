#!/usr/bin/env python3
"""Retrieval recall@k / MRR — метрика Этапов 1–2 (паттерн ../rag/eval, ../webchat/eval).

Индексирует golden-репо (или переиспользует уже проиндексированный проект по id), затем для
каждого вопроса проверяет, попал ли ожидаемый файл (и опц. символ) в top-k. Два режима:
  --mode dense   — только FAISS/nomic (baseline Этапа 1);
  --mode hybrid  — dense + FTS5/bm25 через RRF (Этап 2a).
Печатает recall@k, MRR (позиция первого верного файла — показывает выигрыш ранжирования там,
где recall уже насыщен) и корректность abstain на negatives (вопросы не по репо).

Запуск (из backend/ с активным .venv, Ollama на :11434):
    JWP_DATA_DIR=/tmp/jwp-eval python ../eval/recall_at_k.py --golden ../eval/golden_markupsafe.json --k 5 --mode hybrid
    # A/B на одном индексе (без переиндексации):
    ... --project-id <id> --mode dense
    ... --project-id <id> --mode hybrid
"""
import argparse
import json
import sys
import time
import uuid
from pathlib import Path

# backend/ в sys.path, чтобы импортировать app.* при запуске из eval/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app import db  # noqa: E402
from app.indexing import embeddings, faiss_store, hybrid, pipeline, validation  # noqa: E402


def _index_repo(url: str) -> str:
    ref = validation.parse_github_url(url)
    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    t0 = time.time()
    pipeline._run_full(project_id, ref.url, reindex=False)
    print(f"  проиндексировано за {time.time() - t0:.1f} с, чанков: {db.chunk_count(project_id)}")
    return project_id


def _retrieve(project_id: str, question: str, k: int, mode: str) -> list[tuple[str, str | None]]:
    """Ранжированный список (file, symbol) top-k в выбранном режиме."""
    if mode == "hybrid":
        return [(h["file"], h["symbol"]) for h in hybrid.hybrid_search(project_id, question, k)]
    # dense-only (baseline)
    q = embeddings.embed_query(question)
    hits = faiss_store.search(project_id, q, k)
    faiss_ids = [fid for fid, _ in hits]
    rows = db.chunks_by_faiss_ids(project_id, faiss_ids)
    out: list[tuple[str, str | None]] = []
    for fid in faiss_ids:
        row = rows.get(fid)
        if row:
            out.append((row["file"], row["symbol"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--mode", choices=["dense", "hybrid"], default="hybrid")
    parser.add_argument("--project-id", default=None, help="переиспользовать готовый индекс")
    args = parser.parse_args()

    golden = json.loads(Path(args.golden).read_text())
    db.init_db()

    project_id = args.project_id
    if project_id is None:
        print(f"Индексация {golden['repo']} …")
        project_id = _index_repo(golden["repo"])

    hits_file = 0
    hits_symbol = 0
    rr_sum = 0.0  # сумма reciprocal rank первого верного файла
    total = len(golden["cases"])
    print(f"\n[{args.mode}] Recall@{args.k} / MRR по {total} вопросам:")
    for case in golden["cases"]:
        retrieved = _retrieve(project_id, case["question"], args.k, args.mode)
        files = [f for f, _ in retrieved]
        file_ok = case["file"] in files
        symbol_ok = case.get("symbol") in {s for _, s in retrieved} if case.get("symbol") else file_ok
        rank = files.index(case["file"]) + 1 if file_ok else 0
        rr_sum += 1.0 / rank if rank else 0.0
        hits_file += file_ok
        hits_symbol += symbol_ok
        mark = "✓" if file_ok else "✗"
        pos = f"@{rank}" if rank else "—"
        print(f"  {mark} {case['question'][:46]:46} → {case['file']} (symbol {'✓' if symbol_ok else '✗'}, pos {pos})")

    print(f"\nrecall@{args.k} (файл):   {hits_file}/{total} = {hits_file / total:.2f}")
    print(f"recall@{args.k} (символ): {hits_symbol}/{total} = {hits_symbol / total:.2f}")
    print(f"MRR (файл):              {rr_sum / total:.3f}")

    # Гейт abstain — только для hybrid (пороги живут в hybrid.should_abstain).
    if args.mode == "hybrid":
        # Позитивы НЕ должны абстейнить (иначе ответ не дойдёт до пользователя).
        pos_ok = 0
        print("\nAbstain на позитивах (ожидаем abstain=False):")
        for case in golden["cases"]:
            hits = hybrid.hybrid_search(project_id, case["question"], args.k)
            abstain, _ = hybrid.should_abstain(hits)
            pos_ok += not abstain
            print(f"  {'✓' if not abstain else '✗'} abstain={abstain!s:5} · {case['question'][:50]}")
        print(f"позитивы-не-abstain: {pos_ok}/{total} = {pos_ok / total:.2f}")

        negatives = golden.get("negatives", [])
        if negatives:
            neg_ok = 0
            print(f"\nAbstain на {len(negatives)} negative-вопросах (ожидаем abstain=True):")
            for q in negatives:
                hits = hybrid.hybrid_search(project_id, q, args.k)
                abstain, _ = hybrid.should_abstain(hits)
                neg_ok += abstain
                print(f"  {'✓' if abstain else '✗'} abstain={abstain!s:5} · {q[:50]}")
            print(f"negatives-abstain: {neg_ok}/{len(negatives)} = {neg_ok / len(negatives):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
