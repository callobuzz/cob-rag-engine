"""Fair use on the website side (PIP3 task 5): pages per day, the fetch budget, the crawl rail,
the resume job and the business's own page total. Learning runs on the fake database and the
fake site of `test_website_learn_runs.py`; nothing touches the internet."""

import asyncio
import itertools
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.pipeline_profile import TenantPipelineSettings
from callobuzz_rag.db.models.website import WebsiteRun
from callobuzz_rag.fairuse import budget as budget_mod
from callobuzz_rag.fairuse import rails
from callobuzz_rag.fairuse.budget import FetchBudget, FetchBudgetExhausted
from callobuzz_rag.fairuse.limits import EffectiveFairUse, FairUseLimits, FairUsePolicy
from callobuzz_rag.tiers.lanes import LaneTimeout
from callobuzz_rag.website import archive as archive_mod
from callobuzz_rag.website.fetch import WebsiteFetcher
from callobuzz_rag.website.learn import learn_source, learn_urls
from callobuzz_rag.website.policy import website_policy
from callobuzz_rag.website.resume import resume_limited
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.test_website_learn_runs import ORIGIN, World, flat_site, long_page


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    async def nothing(corpus: Any) -> None:
        return None

    monkeypatch.setattr(archive_mod, "_invalidate_cache", nothing)


@pytest.fixture(autouse=True)
def usage_alerts(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """The 80 % / 100 % notices (FU-6) a budget schedules in the background, recorded."""
    from callobuzz_rag.fairuse import alerts

    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(
        alerts,
        "schedule_usage",
        lambda factory, tenant, metrics, settings=None: calls.append((tenant, tuple(metrics))),
    )
    return calls


class Meter:
    """The day counters in memory: what `fair_use_counters` would hold, per tenant and metric."""

    def __init__(self) -> None:
        self.values: dict[tuple[uuid.UUID, str], int] = {}
        self.days: dict[tuple[uuid.UUID, str, Any], int] = {}
        self.days_enabled = False  # `True`: a day's counter is read per day (the midnight test)
        self.bumps: list[tuple[str, int]] = []

    async def bump(
        self, session: Any, tenant_id: uuid.UUID, metric: str, n: int = 1, day: Any = None
    ) -> None:
        self.bumps.append((metric, n))
        self.days[(tenant_id, metric, day)] = self.days.get((tenant_id, metric, day), 0) + n
        self.values[(tenant_id, metric)] = self.values.get((tenant_id, metric), 0) + n

    async def read_counters(
        self, session: Any, tenant_id: uuid.UUID, day: Any = None, metrics: Any = ()
    ) -> dict[str, int]:
        if self.days_enabled:
            return {m: self.days.get((tenant_id, m, day), 0) for m in metrics}
        return {m: self.values.get((tenant_id, m), 0) for m in metrics}

    async def reserve(
        self,
        session: Any,
        tenant_id: uuid.UUID,
        metric: str,
        n: int,
        limit: int,
        day: Any = None,
    ) -> int:
        """`counters.reserve` in memory: take up to `n`, never past `limit`."""
        used = (await self.read_counters(session, tenant_id, day, [metric]))[metric]
        want = min(n, limit - used)
        if want <= 0:
            return 0
        await self.bump(session, tenant_id, metric, want, day)
        return want

    async def release(
        self, session: Any, tenant_id: uuid.UUID, metric: str, n: int, day: Any = None
    ) -> None:
        if n <= 0:
            return
        key = (tenant_id, metric, day)
        self.days[key] = max(self.days.get(key, 0) - n, 0)
        self.values[(tenant_id, metric)] = max(self.values.get((tenant_id, metric), 0) - n, 0)
        self.bumps.append((metric, -n))

    def get(self, tenant_id: uuid.UUID, metric: str) -> int:
        return self.values.get((tenant_id, metric), 0)


@pytest.fixture
def meter(monkeypatch: pytest.MonkeyPatch) -> Meter:
    m = Meter()
    monkeypatch.setattr(budget_mod.counters, "bump", m.bump)
    monkeypatch.setattr(budget_mod.counters, "read_counters", m.read_counters)
    monkeypatch.setattr(budget_mod.counters, "reserve", m.reserve)
    monkeypatch.setattr(budget_mod.counters, "release", m.release)
    return m


def limited(monkeypatch: pytest.MonkeyPatch, **limits: int) -> None:
    eff = EffectiveFairUse(FairUseLimits(**limits), FairUsePolicy(), False, frozenset())
    monkeypatch.setattr(budget_mod, "effective_fair_use", AsyncMock(return_value=eff))


class Env:
    """A learning world whose fetcher spends a budget and whose room is bounded by a daily
    pages limit, both from the same meter."""

    def __init__(
        self, world: World, meter: Meter, *, pages_per_day: int | None = None, flush_every: int = 20
    ) -> None:
        self.world, self.meter, self.pages_per_day = world, meter, pages_per_day
        self.flush_every = flush_every
        self.budgets: list[FetchBudget] = []

    def deps(self) -> Any:
        def fetcher(source: Any) -> WebsiteFetcher:
            spend = FetchBudget(
                self.world.db.factory(), source.tenant_id, flush_every=self.flush_every
            )
            self.budgets.append(spend)
            return WebsiteFetcher(
                self.world.settings,
                origin=source.origin,
                scope=source.scope,
                transport=self.world.transport,
                allow_private=False,
                budget=spend,
            )

        async def pages_left(session: Any, corpus: Any) -> int | None:
            if self.pages_per_day is None:
                return None
            used = self.meter.get(corpus.tenant_id, "pages_learned")
            return max(self.pages_per_day - used, 0)

        async def on_pages(tenant_id: uuid.UUID, n: int) -> None:
            await self.meter.bump(None, tenant_id, "pages_learned", n)

        async def reserve_pages(tenant_id: uuid.UUID, n: int) -> int:
            assert self.pages_per_day is not None
            return await self.meter.reserve(None, tenant_id, "pages_learned", n, self.pages_per_day)

        async def release_pages(tenant_id: uuid.UUID, n: int) -> None:
            await self.meter.release(None, tenant_id, "pages_learned", n)

        return self.world.deps(
            fetcher=fetcher,
            pages_left=pages_left,
            on_pages=on_pages,
            reserve_pages=reserve_pages,
            release_pages=release_pages,
        )

    @property
    def tenant(self) -> uuid.UUID:
        return self.world.tenant.id


def runs(world: World) -> list[WebsiteRun]:
    return sorted(world.db.all(WebsiteRun), key=lambda r: r.started_at)


# ---------------------------------------------------------------------------- pages per day


async def test_pages_per_day_stops_learning_and_resumes_the_next_day(meter: Meter) -> None:
    world = World(flat_site(30))
    env = Env(world, meter, pages_per_day=10)
    first = await learn_source(world.source.id, kind="first", deps=env.deps())
    assert first.status == "done"
    assert first.counts["limited_by"] == "daily_limit"
    assert sum(1 for p in world.pages().values() if p.status == "learned") == 10
    # a day's limit only delays: nothing is marked as beyond the limit
    assert not [p for p in world.pages().values() if p.skip_reason == "over_limit"]
    assert meter.get(env.tenant, "pages_learned") == 10

    meter.values[(env.tenant, "pages_learned")] = 0  # the next UTC day: a fresh counter
    second = await learn_source(world.source.id, kind="full", deps=env.deps())
    assert second.counts["limited_by"] == "daily_limit"
    assert sum(1 for p in world.pages().values() if p.status == "learned") == 20


async def test_no_daily_limit_learns_everything_and_says_nothing_about_limits(meter: Meter) -> None:
    world = World(flat_site(12))
    result = await learn_source(world.source.id, kind="first", deps=Env(world, meter).deps())
    assert "limited_by" not in result.counts
    assert sum(1 for p in world.pages().values() if p.status == "learned") == 13  # home + 12


async def test_a_pushed_page_by_page_read_obeys_the_daily_pages_too(meter: Meter) -> None:
    world = World(flat_site(6))
    env = Env(world, meter, pages_per_day=2)
    urls = [f"{ORIGIN}/p{i}" for i in range(5)]
    result = await learn_urls(world.source.id, urls, kind="push", deps=env.deps())
    assert result.counts["limited_by"] == "daily_limit"
    assert sum(1 for p in world.pages().values() if p.status == "learned") == 2


async def test_the_document_room_and_the_daily_pages_are_one_bound(meter: Meter) -> None:
    world = World(flat_site(20))
    world.room = 4  # the plan's document limit is tighter than today's pages
    env = Env(world, meter, pages_per_day=10)
    result = await learn_source(world.source.id, kind="first", deps=env.deps())
    assert result.reason == "document_limit"  # a real limit, reported as before
    assert "limited_by" not in result.counts


# ---------------------------------------------------------------------------- fetch budget


async def test_the_budget_counts_every_real_request_and_only_those(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch, fetches_per_day=500)
    world = World(flat_site(5))
    env = Env(world, meter)
    await learn_source(world.source.id, kind="first", deps=env.deps())
    assert meter.get(env.tenant, "fetches") == len(world.transport.requests)  # robots included
    assert len(world.transport.requests) >= 7


async def test_the_budget_wraps_the_guarded_client_and_never_counts_a_refused_url(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch, fetches_per_day=50)
    world = World(
        {
            "/robots.txt": "User-agent: *\nDisallow: /private",
            "/ok": long_page("Ok", "ok"),
            "/private/x": long_page("Private", "private"),
        }
    )
    env = Env(world, meter)
    result = await learn_urls(
        world.source.id,
        ["http://10.0.0.7/admin", f"{ORIGIN}/private/x", f"{ORIGIN}/ok"],
        kind="page",
        deps=env.deps(),
    )
    assert result.status == "done"
    assert world.transport.paths == ["/robots.txt", "/ok"]  # nothing refused was requested
    assert meter.get(env.tenant, "fetches") == 2  # robots.txt + /ok
    (budget,) = env.budgets
    assert budget._state is not None and budget._state.spent == 2


async def test_a_run_past_the_fetch_limit_ends_cleanly_and_keeps_what_it_read(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch, fetches_per_day=6)
    world = World(flat_site(25))
    env = Env(world, meter)
    result = await learn_source(world.source.id, kind="first", deps=env.deps())
    assert result.counts["limited_by"] == "fetch_limit"
    assert len(world.transport.requests) <= 6
    assert meter.get(env.tenant, "fetches") == len(world.transport.requests)
    learned = sum(1 for p in world.pages().values() if p.status == "learned")
    assert learned >= 1 and result.status == "done"
    assert not [p for p in world.pages().values() if p.skip_reason == "over_limit"]


async def test_a_budget_already_used_up_fails_a_run_with_nothing_to_show(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch, fetches_per_day=3)
    world = World(flat_site(5))
    meter.values[(world.tenant.id, "fetches")] = 3
    result = await learn_source(world.source.id, kind="first", deps=Env(world, meter).deps())
    assert result.counts.get("limited_by") == "fetch_limit"
    assert result.status == "failed" and result.reason == "fetch_limit"
    assert world.transport.requests == []  # not one request went out


# ------------------------------------------------------------------------ the budget itself


def budget_with(
    limit: int | None,
    meter: Meter,
    monkeypatch,
    *,
    flush_every: int = 20,
    used: int = 0,
    today=None,
):
    limited(monkeypatch, **({"fetches_per_day": limit} if limit else {}))
    tenant = uuid.uuid4()
    meter.values[(tenant, "fetches")] = used
    kwargs: dict[str, Any] = {"flush_every": flush_every}
    if today is not None:
        kwargs["today"] = today
    return FetchBudget(lambda: SimpleNamespace(), tenant, **kwargs), tenant


class _Ctx:
    async def __aenter__(self) -> Any:
        return SimpleNamespace(commit=AsyncMock())

    async def __aexit__(self, *exc: Any) -> None:
        return None


async def test_spending_without_a_limit_is_batched_and_flushed_at_the_end(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch)
    tenant = uuid.uuid4()
    spend = FetchBudget(lambda: _Ctx(), tenant, flush_every=5)
    for _ in range(12):
        await spend.spend()
    assert meter.bumps == [("fetches", 5), ("fetches", 5)]  # a write every 5, not one per fetch
    await spend.flush()
    assert meter.bumps[-1] == ("fetches", 2)
    await spend.flush()
    assert len(meter.bumps) == 3  # nothing pending: no write


async def test_a_limited_budget_takes_blocks_from_the_shared_counter_and_returns_the_rest(
    meter: Meter, monkeypatch
) -> None:
    """FU-5: the day's counter is the business's, not the run's: the fetches are taken from it
    (never past the limit) in blocks, and what the run did not spend goes back on close."""
    limited(monkeypatch, fetches_per_day=100)
    tenant = uuid.uuid4()
    spend = FetchBudget(lambda: _Ctx(), tenant, flush_every=5)
    for _ in range(12):
        await spend.spend()
    assert meter.bumps == [("fetches", 5)] * 3  # three blocks of five
    assert meter.get(tenant, "fetches") == 15
    await spend.close()
    assert meter.get(tenant, "fetches") == 12  # the three unspent went back
    await spend.close()
    assert meter.bumps[-1] == ("fetches", -3) and len(meter.bumps) == 4


async def test_two_runs_of_one_business_cannot_spend_past_the_limit_together(
    meter: Meter, monkeypatch
) -> None:
    """FU-5: each run used to load the day's count once and spend the full allowance."""
    limited(monkeypatch, fetches_per_day=8)
    tenant = uuid.uuid4()
    first = FetchBudget(lambda: _Ctx(), tenant, flush_every=4)
    second = FetchBudget(lambda: _Ctx(), tenant, flush_every=4)
    done = 0
    for _ in range(8):
        for run in (first, second):
            try:
                await run.spend()
                done += 1
            except FetchBudgetExhausted:
                pass
    assert done == 8 and meter.get(tenant, "fetches") == 8
    with pytest.raises(FetchBudgetExhausted):
        await first.spend()
    await first.close()
    await second.close()
    assert meter.get(tenant, "fetches") == 8  # all of it was spent: nothing goes back


async def test_the_limit_raises_on_the_next_fetch_not_before(meter: Meter, monkeypatch) -> None:
    limited(monkeypatch, fetches_per_day=3)
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4())
    for _ in range(3):
        await spend.spend()
    assert not spend.exhausted  # used, but nothing has been refused yet
    with pytest.raises(FetchBudgetExhausted):
        await spend.spend()
    assert spend.exhausted


async def test_a_business_without_a_limit_is_counted_but_never_stopped(
    meter: Meter, monkeypatch
) -> None:
    limited(monkeypatch)
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4(), flush_every=2)
    for _ in range(7):
        await spend.spend()
    assert not spend.exhausted and sum(n for _, n in meter.bumps) == 6


async def test_the_exempt_workspaces_are_never_stopped(meter: Meter, monkeypatch) -> None:
    eff = EffectiveFairUse(FairUseLimits(fetches_per_day=1), FairUsePolicy(), True, frozenset())
    monkeypatch.setattr(budget_mod, "effective_fair_use", AsyncMock(return_value=eff))
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4())
    for _ in range(5):
        await spend.spend()


async def test_midnight_starts_a_new_day(meter: Meter, monkeypatch) -> None:
    limited(monkeypatch, fetches_per_day=2)
    meter.days_enabled = True
    today = {"d": date(2026, 10, 5)}
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4(), today=lambda: today["d"])
    await spend.spend()
    await spend.spend()
    with pytest.raises(FetchBudgetExhausted):
        await spend.spend()
    today["d"] = date(2026, 10, 6)
    await spend.spend()  # the old day's count was written to the old day; the new day starts at 0
    assert meter.days[(spend._tenant_id, "fetches", date(2026, 10, 5))] == 2


async def test_a_failed_write_never_stops_a_crawl(meter: Meter, monkeypatch, caplog) -> None:
    limited(monkeypatch)

    async def broken(*a: Any, **k: Any) -> None:
        raise ConnectionError("database is down")

    monkeypatch.setattr(budget_mod.counters, "bump", broken)
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4(), flush_every=1)
    await spend.spend()
    await spend.flush()  # does not raise
    assert "Could not write the website fetch count" in caplog.text


async def test_the_fetcher_closes_by_writing_what_it_spent(meter: Meter, monkeypatch) -> None:
    limited(monkeypatch, fetches_per_day=100)
    world = World({"/": long_page("Home", "home")})
    env = Env(world, meter)
    fetcher = env.deps().fetcher(world.source)
    await fetcher.get(f"{ORIGIN}/")
    assert meter.get(env.tenant, "fetches") == 20  # a block is taken from the day's counter
    await fetcher.aclose()
    assert meter.get(env.tenant, "fetches") == 2  # robots.txt + the page: the rest went back


# ---------------------------------------------------------------------------- the crawl rail


async def test_no_crawl_limit_takes_no_slot_and_needs_no_redis() -> None:
    async with rails.crawl_slot(None, None, tenant_id=uuid.uuid4()) as ok:
        assert ok is True


async def test_the_crawl_rail_lets_one_run_in_at_a_time() -> None:
    redis = FakeRedis()
    kw = {"wait_s": 0.15, "poll_ms": (10, 20)}
    first_in, release = asyncio.Event(), asyncio.Event()

    async def holder() -> None:
        async with rails.crawl_slot(redis, 1, tenant_id=uuid.uuid4(), **kw):
            first_in.set()
            await release.wait()

    task = asyncio.ensure_future(holder())
    await first_in.wait()
    with pytest.raises(LaneTimeout):  # the second site waits, then goes back on the queue
        async with rails.crawl_slot(redis, 1, tenant_id=uuid.uuid4(), **kw):
            pass
    release.set()
    await task
    async with rails.crawl_slot(redis, 1, tenant_id=uuid.uuid4(), **kw) as ok:  # freed again
        assert ok is True


async def test_two_slots_let_two_runs_in() -> None:
    redis = FakeRedis()
    kw = {"wait_s": 0.1, "poll_ms": (5, 10)}
    async with (
        rails.crawl_slot(redis, 2, tenant_id=uuid.uuid4(), **kw),
        rails.crawl_slot(redis, 2, tenant_id=uuid.uuid4(), **kw),
    ):
        with pytest.raises(LaneTimeout):
            async with rails.crawl_slot(redis, 2, tenant_id=uuid.uuid4(), **kw):
                pass


async def test_a_slot_is_released_when_the_crawl_fails() -> None:
    redis = FakeRedis()
    with pytest.raises(RuntimeError):
        async with rails.crawl_slot(redis, 1, tenant_id=uuid.uuid4(), wait_s=0.1, poll_ms=(5, 10)):
            raise RuntimeError("the crawl broke")
    async with rails.crawl_slot(
        redis, 1, tenant_id=uuid.uuid4(), wait_s=0.1, poll_ms=(5, 10)
    ) as ok:
        assert ok is True


async def test_redis_trouble_never_stops_a_crawl() -> None:
    redis = FakeRedis()
    redis.down = True
    async with rails.crawl_slot(
        redis, 1, tenant_id=uuid.uuid4(), wait_s=0.1, poll_ms=(5, 10)
    ) as ok:
        assert ok is True


# ---------------------------------------------------------------------------- the resume job


class _Session:
    """What `resume_limited` reads and writes, without a database."""

    def __init__(self, source_ids: list[uuid.UUID]) -> None:
        self.ids = source_ids
        self.sources = {
            i: SimpleNamespace(id=i, status="ready", pause_reason="x") for i in source_ids
        }
        self.statements: list[str] = []
        self.committed = False

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        from sqlalchemy.dialects import postgresql

        self.statements.append(
            str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        )
        return SimpleNamespace(all=lambda: [(i,) for i in self.ids])

    async def get(self, model: Any, key: Any) -> Any:
        return self.sources.get(key)

    async def commit(self) -> None:
        self.committed = True


async def test_the_resume_job_restarts_the_sites_a_daily_limit_cut_short() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]
    session = _Session(ids)
    dispatched: list[tuple[str, str, str]] = []
    started = await resume_limited(
        lambda: session,
        dispatch_learn=lambda sid, kind, trigger: dispatched.append((sid, kind, trigger)),
        now=datetime(2026, 10, 6, 1, 20, tzinfo=UTC),
    )
    assert sorted(started) == sorted(str(i) for i in ids)
    assert sorted(d[0] for d in dispatched) == sorted(started)
    assert {(d[1], d[2]) for d in dispatched} == {("full", "schedule")}
    assert all(
        s.status == "discovering" and s.pause_reason is None for s in session.sources.values()
    )
    assert session.committed


async def test_the_resume_query_picks_only_the_right_runs() -> None:
    session = _Session([])
    await resume_limited(
        lambda: session,
        dispatch_learn=lambda *a: None,
        now=datetime(2026, 10, 6, 1, 20, tzinfo=UTC),
    )
    sql = session.statements[0]
    assert "(website_runs.counts ->> 'limited_by') IN ('daily_limit', 'fetch_limit')" in sql
    assert "website_runs.finished_at < '2026-10-06 00:00:00+00:00'" in sql  # an earlier UTC day
    assert "website_runs.kind IN ('first', 'full')" in sql  # a page-by-page read is not resumed
    assert "NOT (EXISTS" in sql  # only the site's LAST re-read counts ...
    assert "website_runs_1.kind IN ('first', 'full')" in sql  # ... not a check or a push after it
    assert "tenants.status = 'active'" in sql  # a suspended workspace resumes with the workspace
    assert "NOT IN ('paused', 'blocked'" in sql


async def test_nothing_to_resume_dispatches_nothing() -> None:
    dispatched: list[Any] = []
    started = await resume_limited(
        lambda: _Session([]), dispatch_learn=lambda *a: dispatched.append(a), now=datetime.now(UTC)
    )
    assert started == [] and dispatched == []


# ------------------------------------------------------------ the business's own page total


async def test_a_business_page_total_beats_the_plans_and_null_means_the_engine_ceiling() -> None:
    world = World({}, plan={"website_learning_enabled": True, "website_pages_max": 50})
    settings = Settings(_env_file=None, website_max_pages=300)  # type: ignore[call-arg]
    corpus = world.corpus

    async def cap() -> int:
        return (await website_policy(world.db.session(), corpus, settings)).pages_cap  # type: ignore[arg-type]

    assert await cap() == 50  # the plan's own (D9: website_pages_max)
    row = world.db.add(
        TenantPipelineSettings(
            tenant_id=world.tenant.id, fair_use_overrides={"website_pages_total": 80}
        )
    )
    assert await cap() == 80
    row.fair_use_overrides = {"website_pages_total": 500}
    assert await cap() == 300  # the engine's ceiling always applies
    row.fair_use_overrides = {"website_pages_total": None}
    assert await cap() == 300  # null = unlimited, still under the engine's ceiling
    row.fair_use_overrides = {}
    assert await cap() == 50  # cleared: follows the plan again


@contextmanager
def _unused() -> Iterator[None]:  # keeps `httpx` imported for the transport type in this module
    assert httpx is not None
    yield


# ------------------------------------------------------------------------- the task wiring


async def _noop_ingest(batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    return {"status": "completed"}


def test_the_workers_fetcher_spends_a_budget_and_pages_are_counted() -> None:
    from callobuzz_rag.worker import website_tasks

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    deps = website_tasks.build_deps(settings, lambda: None, object(), _noop_ingest)
    source = SimpleNamespace(origin=ORIGIN, scope="shreedental.in", tenant_id=uuid.uuid4())
    assert deps.fetcher(source)._budget is not None
    assert deps.pages_left is not None and deps.on_pages is not None


def test_with_fair_use_off_nothing_is_budgeted() -> None:
    from callobuzz_rag.worker import website_tasks

    settings = Settings(_env_file=None, fair_use_enabled=False)  # type: ignore[call-arg]
    deps = website_tasks.build_deps(settings, lambda: None, object(), _noop_ingest)
    source = SimpleNamespace(origin=ORIGIN, scope="shreedental.in", tenant_id=uuid.uuid4())
    assert deps.fetcher(source)._budget is None
    assert deps.pages_left is None and deps.on_pages is None


def test_the_resume_task_is_on_the_periodic_queue() -> None:
    from callobuzz_rag.worker.celery_app import celery_app

    task = celery_app.tasks["callobuzz_rag.website.resume_limited"]
    assert task.queue == "periodic" and task.acks_late is True


async def test_a_resumed_read_under_a_fetch_limit_reaches_new_pages_every_day(
    meter: Meter, monkeypatch
) -> None:
    """FU-3: a resumed full read used to spend the day's fetches on the pages it had already
    learned (best first), stop at the limit again and never get past its first slice."""
    limited(monkeypatch, fetches_per_day=8)
    world = World(flat_site(39))
    env = Env(world, meter)
    totals: list[int] = []
    for day in range(12):
        meter.values[(env.tenant, "fetches")] = 0  # a fresh UTC day
        await learn_source(world.source.id, kind="first" if day == 0 else "full", deps=env.deps())
        totals.append(sum(1 for p in world.pages().values() if p.status == "learned"))
        if totals[-1] == 40:
            break
    assert totals[-1] == 40, totals  # home + 39: the whole site, a slice a day
    growing = totals[: totals.index(40) + 1]
    assert all(later > earlier for earlier, later in itertools.pairwise(growing)), totals
    assert not [p for p in world.pages().values() if p.skip_reason == "over_limit"]


async def test_pages_under_the_cap_keep_their_place_when_a_fetch_limited_run_skips_them(
    meter: Meter, monkeypatch
) -> None:
    """A learned page that a fetch-limited run did not re-read still counts against the page
    cap and is not dropped as over the limit."""
    limited(monkeypatch, fetches_per_day=8)
    world = World(flat_site(12), plan={"website_pages_max": 10})
    env = Env(world, meter)
    for day in range(6):
        meter.values[(env.tenant, "fetches")] = 0
        await learn_source(world.source.id, kind="first" if day == 0 else "full", deps=env.deps())
    learned = sum(1 for p in world.pages().values() if p.status == "learned")
    assert learned == 10  # the plan's cap, reached and kept


async def test_a_run_that_fails_half_way_still_counts_the_pages_it_learned(
    meter: Meter,
) -> None:
    """FU-5: the pages were only counted by a run that ended normally."""
    world = World(flat_site(30))
    env = Env(world, meter, pages_per_day=100)
    calls = {"n": 0}

    def boom() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("the worker hiccuped")

    world.on_ingest = boom
    result = await learn_source(world.source.id, kind="first", deps=env.deps())
    assert result.status == "failed" and result.reason == "unexpected"
    learned = sum(1 for p in world.pages().values() if p.status == "learned")
    assert learned >= 10
    assert meter.get(env.tenant, "pages_learned") >= learned  # counted, and only what was taken


async def test_pages_taken_and_not_learned_go_back_to_the_day(meter: Meter) -> None:
    """FU-5: a run takes its pages from the shared counter as it needs them and returns the
    ones it did not use, so a small site does not hold back another run's allowance."""
    world = World(flat_site(3))
    env = Env(world, meter, pages_per_day=50)
    result = await learn_source(world.source.id, kind="first", deps=env.deps())
    assert result.status == "done" and "limited_by" not in result.counts
    assert meter.get(env.tenant, "pages_learned") == 4  # home + 3, no more


async def test_the_fetch_limit_tells_the_owner_when_it_is_reached_and_when_the_run_ends(
    meter: Meter, monkeypatch, usage_alerts
) -> None:
    """FU-6: fetches_per_day was never evaluated by the alert code."""
    limited(monkeypatch, fetches_per_day=3)
    tenant = uuid.uuid4()
    spend = FetchBudget(lambda: _Ctx(), tenant)
    for _ in range(3):
        await spend.spend()
    assert usage_alerts == []  # nothing yet: the limit has not been refused
    for _ in range(2):
        with pytest.raises(FetchBudgetExhausted):
            await spend.spend()
    assert usage_alerts == [(tenant, ("fetches_per_day",))]  # once, not once per refusal
    await spend.close()
    assert usage_alerts[1:] == [(tenant, ("fetches_per_day",))]  # and at the end of the run


async def test_a_budget_without_a_limit_tells_nobody(
    meter: Meter, monkeypatch, usage_alerts
) -> None:
    limited(monkeypatch)
    spend = FetchBudget(lambda: _Ctx(), uuid.uuid4())
    await spend.spend()
    await spend.close()
    assert usage_alerts == []


async def test_a_website_run_tells_the_owner_about_the_pages_of_the_day(meter: Meter) -> None:
    world = World(flat_site(30))
    env = Env(world, meter, pages_per_day=10)
    told: list[uuid.UUID] = []
    deps = env.deps()
    deps.usage_alert = told.append
    result = await learn_source(world.source.id, kind="first", deps=deps)
    assert result.counts["limited_by"] == "daily_limit"
    assert told == [env.tenant]  # once, when the run ended
