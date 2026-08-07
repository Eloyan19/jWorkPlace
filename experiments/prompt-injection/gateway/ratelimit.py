"""In-memory rate limiter: скользящее окно на IP. Для одного процесса/учебного демо —
без Redis. N запросов за window секунд; N+1 → отказ (429)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit = limit
        self.window = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Зарегистрировать попытку. Возврат (allowed, retry_after_s). allowed=False → 429."""
        now = time.time() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window - (now - q[0])) + 1
                return False, max(retry, 1)
            q.append(now)
            return True, 0
