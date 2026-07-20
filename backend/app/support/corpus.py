"""FAQ-корпус ассистента поддержки (Задание 2): чанкинг доков продукта + мини FAISS-индекс.

Отдельный крошечный индекс под `project_id="__support__"` — НЕ per-project пайплайн (без clone/
scan/gitleaks/FTS): корпус доверенный и статичный (наши собственные доки в `docs/`). Переиспользуем
`indexing/embeddings` (nomic) и `indexing/faiss_store` (запись/поиск/кэш FAISS). Метаданные чанков
(текст + цитата + диапазон строк) держим в сайдкаре `support/corpus.json`, выровненном по faiss_id.

Цитаты в поддержке валидируются по тексту чанка (корпус доверенный), а не по файлу на диске — в
отличие от grounded-чата по чужому коду (там disk-based line-guard против галлюцинаций).
"""
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.indexing import embeddings, faiss_store

logger = logging.getLogger("jworkplace.support.corpus")

# Зарезервированный id «проекта» для FAISS-индекса поддержки (каталог indexes/__support__/).
SUPPORT_ID = "__support__"

_DOCS_DIR = Path(__file__).parent / "docs"


def _corpus_meta_path() -> Path:
    return get_settings().support_dir / "corpus.json"


def _chunk_markdown(path: Path, rel_name: str) -> list[dict]:
    """Разбить markdown-док на секции по заголовкам `## ` с отслеживанием диапазона строк.

    Преамбула до первого `## ` (title `# ...` + вводный текст) — отдельный чанк. Каждый чанк несёт
    file/section/start_line/end_line/text/citation для последующей валидации цитаты и показа источника.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    # Границы секций: индексы строк, начинающихся с "## ".
    boundaries = [i for i, ln in enumerate(lines) if ln.startswith("## ")]
    # Первый блок (преамбула) начинается со строки 0, если он не пуст.
    starts = ([0] if not boundaries or boundaries[0] != 0 else []) + boundaries
    chunks: list[dict] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1] if idx + 1 < len(starts) else len(lines)
        block = lines[start:end]
        text = "\n".join(block).strip()
        if not text:
            continue
        # Заголовок секции: строка "## X" → "X"; преамбула → строка "# Title" без решёток.
        heading = next((ln for ln in block if ln.startswith("#")), "")
        section = heading.lstrip("#").strip() or rel_name
        start_line = start + 1                  # 1-based
        end_line = end                          # включительно (последняя строка блока)
        chunks.append(
            {
                "file": rel_name,
                "section": section,
                "start_line": start_line,
                "end_line": end_line,
                "text": text,
                "citation": f"{rel_name}::{section}::L{start_line}-{end_line}",
            }
        )
    return chunks


def _collect_chunks() -> list[dict]:
    chunks: list[dict] = []
    for path in sorted(_DOCS_DIR.glob("*.md")):
        chunks.extend(_chunk_markdown(path, path.name))
    return chunks


def build_corpus() -> int:
    """Собрать FAQ-индекс с нуля: чанкинг доков → эмбеддинги → FAISS + сайдкар метаданных.

    Возвращает число проиндексированных чанков. Идемпотентно (перезаписывает индекс и сайдкар).
    Чанки, не влезшие в контекст эмбеддера, embed_documents отбрасывает — метаданные выравниваем
    по `kept`, чтобы faiss_id == индекс в сайдкаре.
    """
    chunks = _collect_chunks()
    if not chunks:
        raise RuntimeError("FAQ-корпус пуст: нет .md в app/support/docs/")

    texts = [c["text"] for c in chunks]
    # blob_sha="" → не засоряем глобальный embed_cache служебным корпусом (он мал, ребилд дёшев).
    vectors, kept = embeddings.embed_documents([""] * len(texts), texts)
    kept_chunks = [chunks[i] for i in kept]

    faiss_store.build_index(SUPPORT_ID, vectors)
    settings = get_settings()
    settings.support_dir.mkdir(parents=True, exist_ok=True)
    _corpus_meta_path().write_text(
        json.dumps(kept_chunks, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("FAQ-корпус собран: %d чанков", len(kept_chunks))
    return len(kept_chunks)


def load_meta() -> list[dict]:
    """Метаданные чанков (по порядку faiss_id). Пусто, если корпус ещё не собран."""
    path = _corpus_meta_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_corpus() -> None:
    """Собрать корпус, если сайдкар отсутствует. Ленивая инициализация на первый запрос."""
    if not _corpus_meta_path().exists():
        build_corpus()


def retrieve(query: str, k: int) -> list[dict]:
    """top-k чанков FAQ по косинусной близости. Каждый hit несёт поля для build_context/валидации
    (file/symbol=section/start_line/end_line/text/citation/lang=None) + score (близость)."""
    ensure_corpus()
    meta = load_meta()
    if not meta:
        return []
    qvec = embeddings.embed_query(query)
    ranked = faiss_store.search(SUPPORT_ID, qvec, k)
    hits: list[dict] = []
    for faiss_id, score in ranked:
        if faiss_id < 0 or faiss_id >= len(meta):
            continue
        c = meta[faiss_id]
        hits.append(
            {
                "file": c["file"],
                "symbol": c["section"],
                "lang": None,               # проза → нормализация пробелов при валидации цитаты
                "start_line": c["start_line"],
                "end_line": c["end_line"],
                "text": c["text"],
                "citation": c["citation"],
                "score": score,
            }
        )
    return hits
