"""The demo's day: it starts at `RAG_DEMO_RESET_AT_UTC`, the moment the data is put back.

Every daily counter (`budget.py`, `limits.py`) is keyed by the date its day began on, so the
limits start again exactly when the demo resets and "resets at 21:30 UTC (03:00 IST)" is true
for both. With a reset time of `00:00` this is the plain UTC day.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Final

IST: Final = timedelta(hours=5, minutes=30)


def day_start(now: datetime, reset_at: str) -> datetime:
    """When the demo day containing `now` began (the latest `HH:MM` UTC at or before it)."""
    hour, minute = (int(part) for part in reset_at.split(":"))
    moment = now.astimezone(UTC)
    start = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return start if start <= moment else start - timedelta(days=1)


def day_key(now: datetime, reset_at: str = "00:00") -> date:
    """The demo day `now` belongs to, named by the date it began on."""
    return day_start(now, reset_at).date()


def next_boundary(now: datetime, reset_at: str) -> datetime:
    return day_start(now, reset_at) + timedelta(days=1)


def when_text(at: datetime) -> str:
    """`21:30 UTC (03:00 IST)`: the moment in both zones, the same on every screen."""
    utc = at.astimezone(UTC)
    return f"{utc:%H:%M} UTC ({utc + IST:%H:%M} IST)"
