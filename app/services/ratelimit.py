"""A small in-process sliding-window limiter.

Single-worker by design, like the rest of the app: it bounds cost and abuse per process, and
resets on restart. A multi-worker deployment would need a shared store instead.
"""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self.hits: dict[object, deque[float]] = {}

    def allow(self, key: object) -> bool:
        """Record one attempt for `key`; False once it has used `limit` within `window`."""
        now = self.clock()
        recent = self.hits.get(key)
        if recent is None:
            if len(self.hits) >= self.max_keys:
                self.prune(now)
            recent = self.hits[key] = deque()
        while recent and recent[0] <= now - self.window:
            recent.popleft()
        if len(recent) >= self.limit:
            return False
        recent.append(now)
        return True

    def prune(self, now: float):
        stale = [k for k, d in self.hits.items() if not d or d[-1] <= now - self.window]
        for key in stale:
            del self.hits[key]
        if len(self.hits) >= self.max_keys:  # still full of live keys: drop the oldest half
            for key in list(self.hits)[: self.max_keys // 2]:
                del self.hits[key]
