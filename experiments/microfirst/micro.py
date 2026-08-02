"""Уровень 1 — **micro-model** двухуровневого инференса: классификатор интента тикета, построенный
НЕ на LLM, а на **эмбеддингах** (nomic-embed-text через локальный Ollama :11434 — то же ядро, на
котором стоит retrieval самого jWorkPlace).

Идея: у каждой из 6 категорий есть несколько размеченных примеров-прототипов (`data/seed_examples.jsonl`).
Усредняем их эмбеддинги → **центроид** категории. Классификация запроса = ближайший центроид по косинусу.
Никакого вызова большой LLM: один локальный embed-вызов + матричное умножение 6×768 — доли миллисекунды.

**Сигнал уверенности (ключевое).** Наивно взять «абсолютную близость top-1» — но nomic это
retrieval-эмбеддинг: он жмёт ВСЕ косинусы в узкую полосу ~0.7 (эмпирически: даже off-topic «рецепт
борща» даёт top-1≈0.73). Значит абсолютный порог плохо отделяет уверенное от неуверенного. Реальный
дискриминирующий сигнал — **margin = cos(top1) − cos(top2)**: у ясного тикета margin крупный
(0.06–0.10+), у по-настоящему трудного (инъекция, транслит-обрывок, двусмысленный borderline) он
схлопывается к ~0.03 и ниже. Поэтому:

  - `OK`     — margin ≥ TAU_MARGIN И top1 ≥ TAU_ABS → отвечаем прямо, БЕЗ большой LLM;
  - `UNSURE` — иначе → эскалация на Уровень 2 (DeepSeek). Micro-model «знает, чего не знает» через margin.

Что микромодель НЕ делает (осознанно, для чистоты урока):
  - классифицирует только **категорию** (6-way интент) — семантическую ось, где эмбеддинги сильны;
    приоритет (high/medium/low) — ортогональная ось «срочности», её эмбеддинги ловят плохо (см. EXPLAINER);
  - не отличает «инструкцию» от «данных» — prompt-injection для неё просто текст; её защита здесь = не
    «распознать атаку», а выдать низкий margin на нетипичном входе и эскалировать (разбор — в EXPLAINER).

Сайдкар: НЕ импортирует `backend.app`. Контракт Ollama (endpoint `/api/embeddings`, префиксы
`search_document:`/`search_query:`, L2-нормализация → косинус = скалярное произведение) повторяет
`backend/app/indexing/embeddings.py` байт-в-байт по смыслу.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import numpy as np

logger = logging.getLogger("microfirst.micro")

# --- контракт Ollama (зеркалит backend/app/indexing/embeddings.py) ---
OLLAMA_URL = "http://127.0.0.1:11434"
EMBED_MODEL = "nomic-embed-text"
_DOC_PREFIX = "search_document: "   # прототипы = «документы»
_QUERY_PREFIX = "search_query: "    # входной тикет = «запрос»
_MAX_EMBED_CHARS = 6000
_EMBED_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# --- закрытое множество категорий (общий словарь с треками confidence/routing/decomposition) ---
CATEGORIES = ("indexing", "pr_edits", "chat_grounding", "repo_connect", "account", "other")

# --- пороги уверенности (откалиброваны по РАСПРЕДЕЛЕНИЮ margin реального прогона, см. EXPLAINER §калибровка) ---
# margin — ОСНОВНОЙ сигнал. Полный прогон 20 тикетов показал чистую границу ~0.03: ниже неё лежат
# по-настоящему трудные входы (prompt-injection 0.024, транслит-обрывок 0.007, двусмысленные
# borderline 0.003–0.016), выше — уверенные (верный argmax). Порог 0.03 → микромодель забирает
# БОЛЬШИНСТВО, инъекцию/мусор корректно эскалирует. Caveat: калибровка на тест-наборе — в проде
# порог берут на held-out val-set (в EXPLAINER явно оговорено). Выше порог → точность↑/экономия↓.
TAU_MARGIN = 0.03
# абсолютная близость — ВТОРИЧНЫЙ, слабый для nomic (всё в полосе ~0.7): держим низко, редко связывает.
TAU_ABS = 0.50


class EmbeddingError(Exception):
    """Сбой обращения к Ollama (сеть/HTTP/формат). Сообщение безопасно для лога (без тела ответа)."""


def _l2(vec: np.ndarray) -> np.ndarray:
    return vec / (np.linalg.norm(vec) + 1e-9)


def _embed(client: httpx.Client, text: str) -> np.ndarray:
    """Один embed-вызов Ollama → L2-нормализованный вектор float32. Префикс задачи (`search_document:`/
    `search_query:`) уже должен быть в `text` (как в backend/app/indexing/embeddings.py)."""
    try:
        resp = client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text[:_MAX_EMBED_CHARS], "options": {"num_ctx": 8192}},
        )
        resp.raise_for_status()
        vec = np.asarray(resp.json()["embedding"], dtype="float32")
    except httpx.HTTPError as exc:
        logger.error("Ollama embed недоступен: %s", type(exc).__name__)
        raise EmbeddingError("Ollama embed недоступен") from None
    except (KeyError, ValueError, TypeError):
        raise EmbeddingError("Ollama вернул неожиданный формат embedding") from None
    return _l2(vec)


class EmbeddingMicroModel:
    """Nearest-centroid классификатор поверх nomic-эмбеддингов. Строится один раз из размеченных
    прототипов, дальше `classify()` дёшев (1 локальный embed + умножение 6×d).

    Атрибуты после build: `categories` (порядок строк матрицы), `centroids` (K×d, L2-норм)."""

    def __init__(self, categories: tuple[str, ...], centroids: np.ndarray) -> None:
        self.categories = categories
        self.centroids = centroids  # (K, d), каждая строка L2-нормализована

    # --- построение центроидов из сид-примеров ---
    @classmethod
    def from_seeds(cls, seeds: dict[str, list[str]]) -> "EmbeddingMicroModel":
        """Центроид категории = L2-норм(среднее L2-норм эмбеддингов её прототипов). Порядок категорий —
        по CATEGORIES (детерминирован). Пустая категория без примеров запрещена (ошибка конфигурации)."""
        cats = tuple(c for c in CATEGORIES if seeds.get(c))
        if len(cats) != len(CATEGORIES):
            missing = set(CATEGORIES) - set(cats)
            raise ValueError(f"нет сид-примеров для категорий: {sorted(missing)}")
        rows = []
        with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
            for c in cats:
                mat = np.stack([_embed(client, _DOC_PREFIX + s) for s in seeds[c]])
                rows.append(_l2(mat.mean(axis=0)))
        return cls(cats, np.stack(rows))

    @classmethod
    def from_seeds_file(cls, path: Path) -> "EmbeddingMicroModel":
        seeds: dict[str, list[str]] = {}
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                seeds.setdefault(row["category"], []).append(row["text"])
        return cls.from_seeds(seeds)

    # --- классификация одного входа ---
    def classify(self, text: str) -> dict:
        """Вернуть структурированный результат Уровня 1:
        `{answer, status, top1, margin, scores, note}` — где `answer` = {"category": <str>} или None,
        `status` ∈ {OK, UNSURE}, `top1`/`margin` — сигналы уверенности, `scores` — косинусы по всем
        категориям (для отчёта/отладки). Сбой Ollama → status=UNSURE, note=embed_error (fail-closed:
        не знаем — эскалируем, а не угадываем)."""
        try:
            with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
                q = _embed(client, _QUERY_PREFIX + text)
        except EmbeddingError as exc:
            return {"answer": None, "status": "UNSURE", "top1": None, "margin": None,
                    "scores": {}, "note": f"embed_error: {exc}"}

        sims = self.centroids @ q  # косинусы (обе стороны L2-норм)
        order = np.argsort(-sims)
        i1, i2 = int(order[0]), int(order[1])
        top1, top2 = float(sims[i1]), float(sims[i2])
        margin = round(top1 - top2, 4)
        scores = {self.categories[j]: round(float(sims[j]), 4) for j in order}

        confident = margin >= TAU_MARGIN and top1 >= TAU_ABS
        if confident:
            return {"answer": {"category": self.categories[i1]}, "status": "OK",
                    "top1": round(top1, 4), "margin": margin, "scores": scores, "note": "confident"}
        # неуверенно: причина — узкий margin (двусмысленно) и/или низкий top1 (далеко от всего)
        why = "low_margin" if margin < TAU_MARGIN else "low_top1"
        return {"answer": {"category": self.categories[i1]}, "status": "UNSURE",
                "top1": round(top1, 4), "margin": margin, "scores": scores, "note": why}
