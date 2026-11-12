"""Per-corpus agent rate limiting via in-memory sliding window."""

import logging
import time

logger = logging.getLogger(__name__)


class AgentRateLimiter:
    """Sliding window rate limiter using in-memory tracking.

    For production: replace with Redis-based counter.
    """

    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._windows: dict[str, list[float]] = {}

    def check(self, corpus_id: str) -> bool:
        """Return True if request is allowed, False if rate limited."""
        now = time.monotonic()
        window = self._windows.setdefault(corpus_id, [])
        window[:] = [t for t in window if now - t < 60.0]
        return len(window) < self._max

    def record(self, corpus_id: str) -> None:
        """Record an agent request."""
        now = time.monotonic()
        self._windows.setdefault(corpus_id, []).append(now)

    def is_rate_limited(self, corpus_id: str) -> bool:
        """Check and record in one call. Returns True if LIMITED."""
        if not self.check(corpus_id):
            return True
        self.record(corpus_id)
        return False
