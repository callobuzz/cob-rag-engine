"""How much of each limit a business has used (PIP3 task 2): where each number comes from, that
only limited metrics are read, and what fails open (rate counters) and what fails closed (spend).

The Redis windows run on `FakeRedis`; the database reads on a scripted session that records the
statements, so the SQL (day, source, payer) is checked as text. The sums themselves run against
PostgreSQL in `tests/integration/test_fair_use_flow.py`."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from callobuzz_rag.fairuse import counters
from callobuzz_rag.fairuse.usage import FairUseUsage, read_usage
from tests.unit.accounts_fakes import FakeRedis

TENANT = uuid.uuid4()
NOW = datetime(2026, 10, 5, 14, 20, 30, tzinfo=UTC)


class Rows:
    def __init__(self, *rows: Any, scalar: Any = None) -> None:
        self._rows, self._scalar = list(rows), scalar

    def one(self) -> Any:
        return self._rows[0]

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar


class Session:
    """Answers each `execute` from a script, remembers the statements, and has a savepoint."""

    def __init__(self, *results: Any, fail: bool = False) -> None:
        self.results = list(results)
        self.statements: list[str] = []
        self.fail = fail

    @asynccontextmanager
    async def begin_nested(self) -> Any:
        yield self

    async def execute(self, stmt: Any) -> Any:
        self.statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
        if self.fail:
            raise ConnectionError("database is down")
        return self.results.pop(0)


# --------------------------------------------------------------- the question windows


async def test_a_question_counts_in_the_minute_and_the_hour() -> None:
    redis = FakeRedis()
    for _ in range(3):
        assert await counters.hit_question(redis, TENANT, NOW) is True
    assert await counters.read_window(redis, TENANT, "minute", NOW) == 3
    assert await counters.read_window(redis, TENANT, "hour", NOW) == 3


async def test_sliding_window_counts_the_previous_window_weighted() -> None:
    redis = FakeRedis()
    last_minute = datetime(2026, 10, 5, 14, 19, 50, tzinfo=UTC)
    for _ in range(10):
        await counters.hit_question(redis, TENANT, last_minute)
    # 30 s into the next minute: half of the previous window still overlaps the last 60 s
    assert await counters.read_window(redis, TENANT, "minute", NOW) == 5
    # a minute and a half later nothing of it is left
    later = datetime(2026, 10, 5, 14, 22, 0, tzinfo=UTC)
    assert await counters.read_window(redis, TENANT, "minute", later) == 0


async def test_minute_window_fails_open(caplog) -> None:
    redis = FakeRedis()
    redis.down = True
    assert await counters.hit_question(redis, TENANT, NOW) is False
    assert "fair-use counters unavailable" in caplog.text
    usage = await read_usage(Session(), redis, TENANT, NOW, metrics={"questions_per_minute"})
    assert usage.questions_per_minute == 0 and usage.windows_unavailable is True
    assert usage.spend_unknown is False  # a rate counter never reads as an unknown spend


async def test_each_business_has_its_own_window() -> None:
    redis = FakeRedis()
    await counters.hit_question(redis, TENANT, NOW)
    assert await counters.read_window(redis, uuid.uuid4(), "hour", NOW) == 0


# ------------------------------------------------------------------- database readers


async def test_only_limited_metrics_are_read() -> None:
    session = Session()
    usage = await read_usage(session, FakeRedis(), TENANT, NOW, metrics=[])
    assert session.statements == []  # an unlimited business costs no query
    assert usage == FairUseUsage()


async def test_day_questions_exclude_cob_and_cob_counts_apart() -> None:
    session = Session(Rows((4, 2)))
    usage = await read_usage(
        session,
        FakeRedis(),
        TENANT,
        NOW,
        metrics={"questions_per_day", "cob_questions_per_day"},
    )
    assert (usage.questions_per_day, usage.cob_questions_per_day) == (4, 2)
    assert len(session.statements) == 1  # both from one statement
    sql = session.statements[0]
    assert "source != 'cob'" in sql and "source = 'cob'" in sql and "day = '2026-10-05'" in sql


async def test_spend_counts_the_operators_money_only_for_today_and_this_month() -> None:
    session = Session(Rows((600, 4_600)))
    usage = await read_usage(
        session, FakeRedis(), TENANT, NOW, metrics={"ai_spend_per_day", "ai_spend_per_month"}
    )
    assert (usage.spend_day_usd_micro, usage.spend_month_usd_micro) == (600, 4_600)
    sql = session.statements[0]
    assert "payer = 'operator'" in sql
    assert "day >= '2026-10-01'" in sql and "day <= '2026-10-05'" in sql
    assert usage.used("ai_spend_per_day") == 600 and usage.used("ai_spend_per_month") == 4_600


async def test_spend_read_failure_is_reported_not_zero(caplog) -> None:
    session = Session(fail=True)
    usage = await read_usage(session, FakeRedis(), TENANT, NOW, metrics={"ai_spend_per_day"})
    assert usage.spend_unknown is True  # the decision pauses or downgrades (fail closed)
    assert usage.spend_day_usd_micro == 0
    assert "failing closed" in caplog.text


async def test_the_counters_the_engine_keeps_itself() -> None:
    session = Session(Rows(("pages_learned", 12), ("fetches", 40), ("uploads", 3)))
    usage = await read_usage(
        session,
        FakeRedis(),
        TENANT,
        NOW,
        metrics={"website_pages_per_day", "fetches_per_day", "uploads_per_day"},
    )
    assert (usage.website_pages_per_day, usage.fetches_per_day, usage.uploads_per_day) == (
        12,
        40,
        3,
    )
    assert "fair_use_counters" in session.statements[0]


async def test_storage_is_reported_in_whole_megabytes_rounded_up() -> None:
    usage = FairUseUsage(storage_bytes=1)
    assert usage.used("storage_mb") == 1
    assert FairUseUsage(storage_bytes=3 * 1024 * 1024).used("storage_mb") == 3
    assert FairUseUsage(storage_bytes=3 * 1024 * 1024 + 1).used("storage_mb") == 4
    session = Session(Rows(scalar=5 * 1024 * 1024))
    got = await read_usage(session, FakeRedis(), TENANT, NOW, metrics={"storage_mb"})
    assert got.used("storage_mb") == 5
    assert "documents" in session.statements[0] and "corpora" in session.statements[0]


async def test_storage_counts_uploads_still_in_flight_not_only_stored_documents() -> None:
    """FU-4: a document row (and its size) is written by the worker, so a queued upload must
    count through its job, or twenty files dropped at once each see an empty business."""
    session = Session(Rows(scalar=40 * 1024 * 1024))
    await read_usage(session, FakeRedis(), TENANT, NOW, metrics={"storage_mb"})
    sql = session.statements[0]
    assert "ingest_jobs" in sql and "size_bytes" in sql and len(session.statements) == 1
    assert "'queued'" in sql and "'pending_review'" not in sql


async def test_pages_in_total_come_from_the_websites_the_business_holds() -> None:
    session = Session(Rows(scalar=77))
    got = await read_usage(session, FakeRedis(), TENANT, NOW, metrics={"website_pages_total"})
    assert got.website_pages_total == 77 and "website_sources" in session.statements[0]


async def test_a_day_counter_that_was_never_bumped_reads_zero() -> None:
    session = Session(Rows())
    values = await counters.read_counters(session, TENANT, NOW.date(), ["fetches", "uploads"])
    assert values == {"fetches": 0, "uploads": 0}


def test_unknown_counter_names_are_refused() -> None:
    import pytest

    async def go() -> None:
        await counters.bump(SimpleNamespace(), TENANT, "typo", 1)  # type: ignore[arg-type]

    import asyncio

    with pytest.raises(AssertionError):
        asyncio.run(go())


class _First:
    def __init__(self, row: Any) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


async def test_reserve_takes_only_what_fits_in_one_conditional_upsert() -> None:
    """FU-5: the day's counter is shared by every run of a business, so taking from it is one
    statement that only applies where the new value still fits the limit."""
    session = Session(Rows(("fetches", 46)), _First(("row",)))
    got = await counters.reserve(session, TENANT, "fetches", 20, 50, NOW.date())
    assert got == 4  # 46 used of 50: four are left
    sql = session.statements[1]
    assert "ON CONFLICT" in sql and "RETURNING" in sql
    assert (
        "WHERE fair_use_counters.value + excluded.value <= 50" in sql
    )  # the condition is in the statement


async def test_reserve_retries_when_another_run_took_the_units_first() -> None:
    session = Session(Rows(("fetches", 40)), _First(None), Rows(("fetches", 48)), _First(("row",)))
    got = await counters.reserve(session, TENANT, "fetches", 20, 50, NOW.date())
    assert got == 2  # the race was lost once; what was left after it was taken


async def test_reserve_gives_nothing_when_the_day_is_used() -> None:
    session = Session(Rows(("fetches", 50)))
    assert await counters.reserve(session, TENANT, "fetches", 20, 50, NOW.date()) == 0
    assert len(session.statements) == 1  # nothing was written


async def test_release_never_goes_below_zero() -> None:
    session = Session(Rows())
    await counters.release(session, TENANT, "fetches", 5, NOW.date())
    assert "greatest(fair_use_counters.value - 5, 0)" in session.statements[0]
