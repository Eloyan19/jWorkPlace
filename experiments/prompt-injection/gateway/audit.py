"""Аудит и учёт стоимости гейтвея.

Пишем ДВА JSONL-журнала:
  audit.jsonl              — каждый запрос/ответ (для аудита трафика);
  intercepted_secrets.jsonl — только события с секретами (перехваченные/замаскированные).

🔑 Инвариант: в журналы кладём ТОЛЬКО preview находок (обрезка + звёздочки), никогда сырой секрет —
иначе аудит-лог сам станет свалкой утечек. Оценка токенов — эвристика (chars/4), ставки —
иллюстративные; для реального биллинга сверять с актуальным прайсом провайдера.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

# Иллюстративные ставки DeepSeek, USD за 1M токенов (свериться с актуальным прайсом провайдера).
PRICE_PER_1M = {"input": 0.27, "output": 1.10}


def estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов (нет tokenizer'а провайдера): ~4 символа на токен."""
    return max(1, round(len(text) / 4))


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """Стоимость запроса по эвристике токенов и иллюстративным ставкам, USD."""
    cost = tokens_in / 1_000_000 * PRICE_PER_1M["input"] + tokens_out / 1_000_000 * PRICE_PER_1M["output"]
    return round(cost, 8)


class AuditLog:
    """Потокобезопасный аппендер JSONL. Один экземпляр на процесс гейтвея."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.audit_path = self.dir / "audit.jsonl"
        self.secrets_path = self.dir / "intercepted_secrets.jsonl"
        self._lock = threading.Lock()

    def _append(self, path: Path, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def log_request(self, record: dict) -> None:
        """Полная запись о запросе (без сырых секретов — вызывающий кладёт только preview)."""
        record.setdefault("ts", time.time())
        self._append(self.audit_path, record)

    def log_interception(self, record: dict) -> None:
        """Отдельный журнал перехваченных секретов (deliverable: «логи перехваченных секретов»)."""
        record.setdefault("ts", time.time())
        self._append(self.secrets_path, record)
