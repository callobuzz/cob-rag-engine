"""How often a learned page is checked (BIZ2): the schedule, adaptive intervals and jitter.

The knowledge base picks a schedule (`refresh_mode`); with `adaptive` on, each page gets its own
interval from how often it really changes: a page that changed is looked at sooner (half the
interval), one that did not later (one and a half times), always between the schedule and 30
days. Pure functions, so the rule is pinned by tests and the scheduler stays simple.
"""

from datetime import datetime, timedelta
from typing import Final, Literal

RefreshMode = Literal["off", "monthly", "weekly", "daily", "6h", "hourly"]

BASE_SECONDS: Final[dict[str, int]] = {
    "hourly": 3_600,
    "6h": 21_600,
    "daily": 86_400,
    "weekly": 604_800,
    "monthly": 2_592_000,
}
MAX_SECONDS: Final = 2_592_000  # 30 days
JITTER: Final = 0.10


def next_interval(
    mode: RefreshMode, adaptive: bool, current: int | None, changed: bool
) -> int | None:
    """The page's next interval in seconds (None = never check: the schedule is `off`)."""
    base = BASE_SECONDS.get(mode)
    if base is None:
        return None
    if not adaptive:
        return base
    cur = current if current is not None else base
    stepped = max(base, cur // 2) if changed else min(MAX_SECONDS, cur * 3 // 2)
    return min(MAX_SECONDS, max(base, stepped))


def jitter(seconds: int, url_hash: str) -> int:
    """`seconds` spread by up to +-10 %, the same way every time for one URL, so a site's pages
    do not all fall due in the same minute. `url_hash` is the page's SHA-256 hex digest."""
    fraction = int(url_hash[:8], 16) / 0xFFFFFFFF  # 0..1, from the hash
    return round(seconds * (1 - JITTER + 2 * JITTER * fraction))


def first_check(
    mode: RefreshMode, adaptive: bool, now: datetime, url_hash: str
) -> tuple[int | None, datetime | None]:
    """(interval, when) for a page that was just learned: the schedule's own interval."""
    del adaptive  # a new page has no history to adapt to
    base = BASE_SECONDS.get(mode)
    if base is None:
        return None, None
    return base, now + timedelta(seconds=jitter(base, url_hash))


def after_check(
    mode: RefreshMode,
    adaptive: bool,
    current: int | None,
    changed: bool,
    now: datetime,
    url_hash: str,
) -> tuple[int | None, datetime | None]:
    """(interval, when) after a page was checked."""
    interval = next_interval(mode, adaptive, current, changed)
    if interval is None:
        return None, None
    return interval, now + timedelta(seconds=jitter(interval, url_hash))
