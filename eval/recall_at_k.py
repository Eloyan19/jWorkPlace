#!/usr/bin/env python3
"""Retrieval recall@k — метрика Этапа 1 (паттерн ../rag/eval, ../webchat/eval).

Индексирует golden-репо (или переиспользует уже проиндексированный проект по id), затем для
каждого вопроса из golden-набора проверяет, попал ли ожидаемый файл (и опц. символ) в top-k
извлечённых чанков. Печатает recall@k как baseline.

Запуск (из backend/ с активным .venv, Ollama на :11434):
    JWP_DATA_DIR=/tmp/jwp-eval python ../eval/recall_at_k.py --golden ../eval/golden_markupsafe.json --k 5
    # переиспользовать готовый индекс без переиндексации:
    ... --project-id <id>
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
from app.indexing import embeddings, faiss_store, pipeline, validation  # noqa: E402


def _index_repo(url: str) -> str:
    ref = validation.parse_github_url(url)
    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    t0 = time.time()
    pipeline._run_full(project_id, ref.url, reindex=False)
    print(f"  проиндексировано за {time.time() - t0:.1f} с, чанков: {db.chunk_count(project_id)}")
    return project_id


def _retrieved_files(project_id: str, question: str, k: int) -> list[tuple[str, str | None]]:
    q = embeddings.embed_query(question)
    hits = faiss_store.search(project_id, q, k)
    out: list[tuple[str, str | None]] = []
    with db.get_conn() as conn:
        for faiss_id, _score in hits:
            row = conn.execute(
                "SELECT file, symbol FROM chunks WHERE project_id = ? AND faiss_id = ?",
                (project_id, faiss_id),
            ).fetchone()
            if row:
                out.append((row["file"], row["symbol"]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", required=True)
    parser.add_argument("--k", type=int, default=5)
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
    total = len(golden["cases"])
    print(f"\nRecall@{args.k} по {total} вопросам:")
    for case in golden["cases"]:
        retrieved = _retrieved_files(project_id, case["question"], args.k)
        files = {f for f, _ in retrieved}
        file_ok = case["file"] in files
        symbol_ok = case.get("symbol") in {s for _, s in retrieved} if case.get("symbol") else file_ok
        hits_file += file_ok
        hits_symbol += symbol_ok
        mark = "✓" if file_ok else "✗"
        print(f"  {mark} {case['question'][:50]:50} → {case['file']} (symbol {'✓' if symbol_ok else '✗'})")

    print(f"\nrecall@{args.k} (файл):   {hits_file}/{total} = {hits_file / total:.2f}")
    print(f"recall@{args.k} (символ): {hits_symbol}/{total} = {hits_symbol / total:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
