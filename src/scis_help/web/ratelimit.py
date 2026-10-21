"""Per-client rate limit: a sliding 60-second window of request times per IP.

IPs are held in memory only, for at most one window, and are never logged or stored.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str) -> int | None:
        """Count one request from ``client``. Returns None if it is allowed, otherwise the whole
        seconds until the oldest request in the window expires (for ``Retry-After``); a refused
        request is not counted."""
        now = self._clock()
        with self._lock:
            self._prune(now)
            hits = self._hits.setdefault(client, deque())
            if len(hits) >= self._limit:
                return max(1, math.ceil(hits[0] + WINDOW_SECONDS - now))
            hits.append(now)
            return None

    def _prune(self, now: float) -> None:
        cutoff = now - WINDOW_SECONDS
        for client in list(self._hits):
            hits = self._hits[client]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[client]

    def __len__(self) -> int:
        """Clients currently tracked (for tests)."""
        with self._lock:
            return len(self._hits)
