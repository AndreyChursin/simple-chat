"""In-memory sliding-window rate limiter (single backend process, see ARCHITECTURE.md §4.7)."""

import math
import time
from collections import defaultdict, deque

from app.core.errors import AppError

PRUNE_EVERY = 1000


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._windows: dict[str, float] = {}
        self._calls = 0

    def hit(self, key: str, limit: int, window_seconds: float) -> None:
        """Register one attempt for `key`; raise 429 if more than `limit` within the window."""
        now = time.monotonic()
        self._calls += 1
        if self._calls % PRUNE_EVERY == 0:
            self.prune(now)
        self._windows[key] = window_seconds
        hits = self._hits[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            retry_after = max(1, math.ceil(window_seconds - (now - hits[0])))
            raise AppError(
                429,
                "rate_limited",
                "Too many attempts, try again later",
                {"retry_after": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)

    def prune(self, now: float | None = None) -> None:
        """Forget keys with no hit inside their window: keys include client-chosen parts (e-mail of a
        login attempt), so they must not pile up for the lifetime of the process."""
        now = time.monotonic() if now is None else now
        for key in [
            k for k, hits in self._hits.items() if not hits or now - hits[-1] >= self._windows.get(k, 0)
        ]:
            del self._hits[key]
            self._windows.pop(key, None)

    def __len__(self) -> int:
        return len(self._hits)

    def reset(self) -> None:
        self._hits.clear()
        self._windows.clear()


rate_limiter = RateLimiter()
