"""Refresh intervals (BIZ2): the schedule's base, adaptive growth and shrinking, jitter."""

import hashlib
import random
from datetime import UTC, datetime, timedelta

import pytest

from callobuzz_rag.website.freshness import (
    BASE_SECONDS,
    MAX_SECONDS,
    first_check,
    jitter,
    next_interval,
)


@pytest.mark.parametrize(
    "mode,adaptive,current,changed,expected",
    [
        ("off", True, None, False, None),
        ("daily", False, 500_000, False, 86_400),
        ("daily", True, None, False, 129_600),
        ("daily", True, 86_400, False, 129_600),
        ("daily", True, 2_000_000, False, 2_592_000),
        ("daily", True, 2_592_000, True, 1_296_000),
        ("daily", True, 100_000, True, 86_400),
        ("hourly", True, 2_592_000, False, 2_592_000),
        ("monthly", True, 3_600, True, 2_592_000),  # clamped up to the schedule
        ("6h", True, None, True, 21_600),
    ],
)
def test_next_interval(
    mode: str, adaptive: bool, current: int | None, changed: bool, expected: int | None
) -> None:
    assert next_interval(mode, adaptive, current, changed) == expected  # type: ignore[arg-type]


@pytest.mark.parametrize("seed", range(200))
def test_interval_always_stays_between_schedule_and_thirty_days(seed: int) -> None:
    rng = random.Random(seed)
    mode = rng.choice(["hourly", "6h", "daily", "weekly", "monthly"])
    cur: int | None = None
    for _ in range(60):
        cur = next_interval(mode, True, cur, rng.random() < 0.3)  # type: ignore[arg-type]
        assert cur is not None and BASE_SECONDS[mode] <= cur <= MAX_SECONDS


def test_jitter_is_deterministic_and_within_ten_percent() -> None:
    h = hashlib.sha256(b"https://shreedental.in/pricing").hexdigest()
    assert jitter(86_400, h) == jitter(86_400, h)
    values = {jitter(86_400, hashlib.sha256(str(i).encode()).hexdigest()) for i in range(500)}
    assert min(values) >= 77_760 and max(values) <= 95_040 and len(values) > 50


def test_first_check_is_the_schedule_not_the_adaptive_step() -> None:
    now = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    h = hashlib.sha256(b"x").hexdigest()
    interval, when = first_check("daily", True, now, h)
    assert interval == 86_400 and when == now + timedelta(seconds=jitter(86_400, h))
    assert first_check("off", True, now, h) == (None, None)
