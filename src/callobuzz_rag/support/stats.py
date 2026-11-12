"""First-response time and the share answered within the promise (TV3), pure."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median


@dataclass(frozen=True)
class Row:
    created_at: datetime
    first_response_at: datetime | None


@dataclass(frozen=True)
class Stats:
    answered: int
    median_first_response_minutes: int | None
    within_sla_pct: int | None


def minutes_between(start: datetime, end: datetime) -> int:
    return max(0, int((end - start).total_seconds() // 60))


def compute(rows: Iterable[Row], *, sla_hours: int | None, now: datetime) -> Stats:
    """The median minutes to a first reply over the answered requests, and the percent (rounded
    down) of requests answered within `sla_hours`: a request still unanswered after the promised
    time counts as a miss, one still inside it does not count yet."""
    waits: list[int] = []
    within = missed = 0
    for row in rows:
        if row.first_response_at is not None:
            wait = minutes_between(row.created_at, row.first_response_at)
            waits.append(wait)
            if sla_hours is not None:
                if wait <= sla_hours * 60:
                    within += 1
                else:
                    missed += 1
        elif sla_hours is not None and now - row.created_at > timedelta(hours=sla_hours):
            missed += 1
    pct = within * 100 // (within + missed) if sla_hours is not None and within + missed else None
    return Stats(
        answered=len(waits),
        median_first_response_minutes=int(median(waits)) if waits else None,
        within_sla_pct=pct,
    )
