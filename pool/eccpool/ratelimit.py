"""In-process token buckets keyed by string (API key id, client IP, ...).

Single-process by design (one uvicorn worker owns the SQLite database). To
scale out, put the buckets in Redis with the same interface.
"""

from __future__ import annotations

import threading
import time


class TokenBuckets:
    def __init__(self, max_entries: int = 100_000) -> None:
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def take(self, key: str, rate_per_minute: float, burst: float,
             cost: float = 1.0, now: float | None = None) -> float:
        """Spend ``cost`` tokens. Returns 0 on success, else seconds to wait."""
        now = time.monotonic() if now is None else now
        rate = rate_per_minute / 60.0
        with self._lock:
            tokens, last = self._state.get(key, (burst, now))
            tokens = min(burst, tokens + (now - last) * rate)
            if cost > burst:
                return float("inf")
            if tokens >= cost:
                self._state[key] = (tokens - cost, now)
                return 0.0
            self._state[key] = (tokens, now)
            if len(self._state) > self._max:
                self._evict(now)
            return (cost - tokens) / rate if rate > 0 else float("inf")

    def _evict(self, now: float) -> None:
        stale = [k for k, (_, last) in self._state.items() if now - last > 3600]
        for k in stale:
            del self._state[k]
