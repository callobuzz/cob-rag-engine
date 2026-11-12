"""Freshness checks (BIZ2): cheap checks first, removals after two answers, the scheduler.

Everything runs on a fake database and a fake site (no network, no vector store, no embedder).
"""

import uuid
from datetime import timedelta
from typing import Any

import httpx
import pytest

from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.website import WebsitePage, WebsiteRun, WebsiteSettings, WebsiteSource
from callobuzz_rag.website.check import check_pages, schedule_checks
from callobuzz_rag.website.freshness import jitter
from callobuzz_rag.website.learn import learn_source
from tests.unit.test_website_learn_runs import (  # noqa: F401  (autouse fixture)
    NOW,
    ORIGIN,
    World,
    _no_cache,
    long_page,
)
from tests.unit.website_fixtures import html_page, words

PRICING = f"{ORIGIN}/pricing"


def sitemap(*entries: tuple[str, str]) -> str:
    rows = "".join(f"<url><loc>{u}</loc><lastmod>{m}</lastmod></url>" for u, m in entries)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{rows}</urlset>'


def base_pages(**extra: Any) -> dict[str, Any]:
    pages: dict[str, Any] = {
        "/": long_page("Home", "home", links=("/pricing",)),
        "/pricing": long_page("Pricing", "price"),
        "/sitemap.xml": sitemap((PRICING, "2026-09-20")),
    }
    pages.update(extra)
    return pages


async def learned(pages: dict[str, Any], **kw: Any) -> World:
    w = World(pages, **kw)
    result = await learn_source(w.source.id, deps=w.deps())
    assert result.status == "done"
    assert w.pages()[PRICING].status == "learned"
    return w


async def check(w: World, *urls: str, trigger: str = "schedule") -> Any:
    ids = [w.pages()[u].id for u in urls]
    return await check_pages(w.source.id, ids, trigger=trigger, deps=w.deps())


def requested(w: World, since: int) -> list[str]:
    return w.transport.urls[since:]


async def test_sitemap_lastmod_not_newer_than_the_last_change_means_no_page_request() -> None:
    w = await learned(base_pages())
    page = w.pages()[PRICING]
    before, batches = len(w.transport.requests), len(w.batches)
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert requested(w, before) == [f"{ORIGIN}/sitemap.xml"]
    assert out.outcomes[page.id] == "unchanged" and len(w.batches) == batches
    assert page.next_check_at == w.clock + timedelta(seconds=jitter(129_600, page.url_hash))
    assert page.interval_seconds == 129_600


async def test_conditional_get_304_costs_nothing() -> None:
    def pricing(request: httpx.Request) -> httpx.Response:
        if request.headers.get("if-none-match") == '"v7"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=long_page("Pricing", "price"),
            headers={"content-type": "text/html", "etag": '"v7"'},
        )

    w = await learned(base_pages(**{"/pricing": pricing, "/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    assert page.etag == '"v7"'
    before, batches = len(w.transport.requests), len(w.batches)
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "unchanged"
    assert requested(w, before) == [f"{ORIGIN}/robots.txt", PRICING]
    assert len(w.batches) == batches and page.http_status == 304


async def test_same_text_new_markup_is_unchanged_and_nothing_is_embedded() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = long_page(
        "Pricing",
        "price",
        head='<meta name="csrf" content="rotating-token-91823"><!-- build 4471 -->',
    )
    batches = len(w.batches)
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "unchanged"
    assert len(w.batches) == batches and page.change_count == 0


async def test_changed_text_reimports_once_and_the_interval_comes_back_down() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.clock = NOW + timedelta(days=1)
    assert (await check(w, PRICING)).outcomes[page.id] == "unchanged"
    assert page.interval_seconds == 129_600  # quiet pages are looked at less often
    w.transport.pages["/pricing"] = long_page("Pricing", "newprice")
    batches = len(w.batches)
    w.clock = NOW + timedelta(days=3)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "changed"
    assert len(w.batches) == batches + 1 and len(w.batches[-1]) == 1
    assert page.change_count == 1 and page.status == "learned"
    assert page.interval_seconds == 86_400  # halved, but never under the schedule
    assert page.last_changed_at == w.clock
    assert page.next_check_at == w.clock + timedelta(seconds=jitter(86_400, page.url_hash))
    versions = [v for v in w.db.all(DocumentVersion) if v.status == "active"]
    assert len(versions) == 2  # one per page, and only the new text of the pricing page


async def test_non_adaptive_pages_keep_the_schedule() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    w.db.add(
        WebsiteSettings(
            corpus_id=w.corpus.id, tenant_id=w.tenant.id, refresh_mode="weekly", adaptive=False
        )
    )
    page = w.pages()[PRICING]
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.interval_seconds == 604_800


async def test_two_404s_an_hour_apart_archive_the_page() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = httpx.Response(404, text="gone")
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.status == "learned" and page.missing_count == 1
    assert page.next_check_at is not None and page.next_check_at > w.clock + timedelta(hours=1)
    w.clock += timedelta(minutes=20)
    await check(w, PRICING)
    assert page.missing_count == 1  # too soon to count again
    assert w.vector_store.deleted == []
    w.clock += timedelta(hours=2)
    out = await check(w, PRICING)
    assert page.status == "removed" and out.outcomes[page.id] == "removed"
    assert len(w.vector_store.deleted) == 1
    assert page.next_check_at is None
    assert w.source.pages_learned == 1  # the home page is still learned


async def test_a_page_that_answers_again_forgets_the_miss() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = httpx.Response(410)
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.missing_count == 1
    w.transport.pages["/pricing"] = long_page("Pricing", "price")
    w.clock += timedelta(hours=3)
    await check(w, PRICING)
    assert page.missing_count == 0 and page.status == "learned"


async def test_leaving_the_sitemap_is_not_removal_but_a_gone_page_still_goes() -> None:
    w = await learned(base_pages())
    page = w.pages()[PRICING]
    assert page.in_sitemap is True
    w.transport.pages["/sitemap.xml"] = sitemap((f"{ORIGIN}/other", "2026-10-01"))
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.in_sitemap is False and page.status == "learned"  # it still loads
    w.transport.pages["/pricing"] = httpx.Response(404)
    for hours in (1, 3):
        w.clock = NOW + timedelta(days=2, hours=hours)
        await check(w, PRICING)
    assert page.status == "removed"


async def test_noindex_now_takes_the_page_out_of_the_answers() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = long_page(
        "Pricing", "price", head='<meta name="robots" content="noindex">'
    )
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert page.status == "skipped" and page.skip_reason == "noindex"
    assert out.outcomes[page.id] == "removed" and len(w.vector_store.deleted) == 1


async def test_robots_disallow_now_takes_the_page_out_of_the_answers() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/robots.txt"] = "User-agent: *\nDisallow: /pricing\n"
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.status == "skipped" and page.skip_reason == "robots_disallowed"
    assert len(w.vector_store.deleted) == 1


async def test_a_site_that_hiccups_changes_nothing() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = httpx.Response(503)
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "failed"
    assert page.status == "learned" and page.missing_count == 0
    assert page.next_check_at is not None and page.next_check_at > w.clock
    assert w.vector_store.deleted == []


async def test_mode_off_checks_nothing() -> None:
    w = await learned(base_pages())
    w.db.add(WebsiteSettings(corpus_id=w.corpus.id, tenant_id=w.tenant.id, refresh_mode="off"))
    before = len(w.transport.requests)
    out = await check(w, PRICING)
    assert out.outcomes == {} and len(w.transport.requests) == before


async def test_learning_disabled_checks_nothing() -> None:
    w = await learned(base_pages())
    w.plan.website_learning_enabled = False
    before = len(w.transport.requests)
    out = await check(w, PRICING)
    assert out.result.status == "disabled" and len(w.transport.requests) == before
    assert w.source.status == "paused" and w.source.pause_reason == "plan"


async def test_the_check_is_recorded_as_a_run() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING, trigger="schedule")
    runs = [r for r in w.db.all(WebsiteRun) if r.kind == "check"]
    assert len(runs) == 1 and runs[0].trigger == "schedule" and runs[0].status == "done"
    assert runs[0].counts["unchanged"] == 1 and runs[0].finished_at is not None


async def test_pages_that_are_not_learned_are_left_alone() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    page.excluded_by_owner = True
    before = len(w.transport.requests)
    out = await check(w, PRICING)
    assert out.outcomes == {} and len(w.transport.requests) == before


# ------------------------------------------------------------------------ the scheduler


class Spy:
    def __init__(self) -> None:
        self.checks: list[tuple[str, list[str], str]] = []
        self.learns: list[tuple[str, str, str]] = []

    def check(self, source_id: str, ids: list[str], trigger: str) -> None:
        self.checks.append((source_id, ids, trigger))

    def learn(self, source_id: str, kind: str, trigger: str) -> None:
        self.learns.append((source_id, kind, trigger))


async def tick(w: World, spy: Spy, now: Any = None) -> dict[str, int]:
    return await schedule_checks(
        w.db.factory(),
        w.settings,
        dispatch_check=spy.check,
        dispatch_learn=spy.learn,
        now=now or w.clock,
    )


def add_pages(w: World, n: int, *, due: bool = True) -> list[WebsitePage]:
    rows = []
    for i in range(n):
        url = f"{ORIGIN}/p{i}"
        rows.append(
            w.db.add(
                WebsitePage(
                    source_id=w.source.id,
                    corpus_id=w.corpus.id,
                    url=url,
                    url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                    status="learned",
                    document_id=uuid.uuid4(),
                    interval_seconds=86_400,
                    next_check_at=NOW - timedelta(minutes=1) if due else NOW + timedelta(days=1),
                )
            )
        )
    return rows


async def test_due_pages_are_pushed_forward_before_they_are_dispatched() -> None:
    w = World({})
    w.source.status = "ready"
    rows = add_pages(w, 3) + add_pages(w, 2, due=False)
    spy = Spy()
    out = await tick(w, spy)
    assert out["due"] == 3 and len(spy.checks) == 1
    source_id, ids, trigger = spy.checks[0]
    assert source_id == str(w.source.id) and trigger == "schedule"
    assert sorted(ids) == sorted(str(r.id) for r in rows[:3])
    assert all(r.next_check_at > NOW + timedelta(hours=20) for r in rows[:3])
    again = Spy()
    await tick(w, again)  # nothing is due any more: the same page is never handed out twice
    assert again.checks == []


async def test_at_most_two_hundred_pages_per_site_per_tick() -> None:
    w = World({})
    w.source.status = "ready"
    add_pages(w, 230)
    spy = Spy()
    await tick(w, spy)
    assert len(spy.checks[0][1]) == 200
    again = Spy()
    await tick(w, again)
    assert len(again.checks[0][1]) == 30


async def test_parked_sites_and_off_schedules_are_not_checked() -> None:
    w = World({})
    w.source.status = "paused"
    rows = add_pages(w, 2)
    spy = Spy()
    await tick(w, spy)
    assert spy.checks == [] and all(r.next_check_at < NOW for r in rows)  # due when it resumes
    w.source.status = "ready"
    w.db.add(WebsiteSettings(corpus_id=w.corpus.id, tenant_id=w.tenant.id, refresh_mode="off"))
    await tick(w, spy)
    assert spy.checks == [] and all(r.next_check_at is None for r in rows)


async def test_a_run_without_a_heartbeat_for_fifteen_minutes_is_closed() -> None:
    w = World({})
    w.source.status = "discovering"
    old = w.db.add(
        WebsiteRun(
            source_id=w.source.id,
            corpus_id=w.corpus.id,
            kind="full",
            trigger="manual",
            started_at=NOW - timedelta(minutes=30),
            heartbeat_at=NOW - timedelta(minutes=16),
        )
    )
    live = w.db.add(
        WebsiteRun(
            source_id=w.source.id,
            corpus_id=w.corpus.id,
            kind="check",
            trigger="schedule",
            started_at=NOW,
            heartbeat_at=NOW - timedelta(minutes=2),
        )
    )
    out = await tick(w, Spy())
    assert out["closed_runs"] == 1
    assert old.status == "failed" and old.finished_at is not None and old.error_code == "stalled"
    assert live.finished_at is None
    assert w.source.status == "failed" and w.source.last_error_code == "stalled"


async def test_a_site_that_never_ran_is_started_after_five_minutes() -> None:
    w = World({})
    w.source.status = "idle"
    w.source.created_at = NOW - timedelta(minutes=2)
    spy = Spy()
    await tick(w, spy)
    assert spy.learns == []
    w.source.created_at = NOW - timedelta(minutes=6)
    await tick(w, spy)
    assert spy.learns == [(str(w.source.id), "first", "schedule")]
    assert w.source.status == "discovering"
    await tick(w, spy)
    assert len(spy.learns) == 1  # not started twice


async def test_the_engine_switch_stops_the_scheduler() -> None:
    w = World({}, website_learning_enabled=False)
    w.source.status = "ready"
    add_pages(w, 2)
    spy = Spy()
    out = await tick(w, spy)
    assert spy.checks == [] and out["due"] == 0


def test_the_fixtures_import_is_used() -> None:
    assert html_page("a", words(1)) and WebsiteSource and pytest


# ------------------------------------------------- a push or a "read again" finds a page gone
async def test_a_pushed_page_that_is_gone_twice_leaves_the_answers() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = httpx.Response(410)
    w.clock = NOW + timedelta(days=1)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.status == "learned" and page.missing_count == 1 and w.vector_store.deleted == []
    w.clock += timedelta(hours=2)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.status == "removed" and len(w.vector_store.deleted) == 1
    assert page.next_check_at is None


async def test_a_pushed_page_on_a_site_that_hiccups_stays_learned_and_scheduled() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    scheduled = page.next_check_at
    w.transport.pages["/pricing"] = httpx.Response(503)
    w.clock = NOW + timedelta(hours=1)
    await learn_urls(w.source.id, [PRICING], kind="sensor", trigger="sensor", deps=w.deps())
    assert page.status == "learned" and page.missing_count == 0
    assert page.next_check_at == scheduled and w.vector_store.deleted == []


# ------------------------------------- a maintenance page is a failed read, never a removal
MAINTENANCE = html_page("Down", "We are updating our website. Back soon.")


async def test_a_maintenance_answer_keeps_a_learned_page_and_looks_again_soon() -> None:
    from callobuzz_rag.website.learn import MISS_RECHECK_SECONDS

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = MAINTENANCE
    w.clock = NOW + timedelta(days=1)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "failed"
    assert page.status == "learned" and page.document_id is not None
    assert page.title == "Pricing" and w.vector_store.deleted == []
    assert page.next_check_at == w.clock + timedelta(seconds=MISS_RECHECK_SECONDS)
    assert page.missing_count == 1


async def test_a_second_thin_answer_an_hour_later_does_archive_the_page() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = MAINTENANCE
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    w.clock += timedelta(hours=2)
    out = await check(w, PRICING)
    assert out.outcomes[page.id] == "removed" and page.status == "skipped"
    assert page.skip_reason == "thin" and len(w.vector_store.deleted) == 1


async def test_a_long_maintenance_text_on_every_page_removes_none_of_the_others() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    home, pricing = w.pages()[f"{ORIGIN}/"], w.pages()[PRICING]
    for path in ("/", "/pricing"):
        w.transport.pages[path] = long_page("Back soon", "maintenance")
    w.clock = NOW + timedelta(days=1)
    await check(w, f"{ORIGIN}/", PRICING)
    assert home.status == pricing.status == "learned" and w.vector_store.deleted == []
    outcomes = sorted([home.missing_count, pricing.missing_count])
    assert outcomes == [0, 1]  # one took the text as its change, the other kept what it had


async def test_an_answering_page_forgets_a_thin_miss() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = MAINTENANCE
    w.clock = NOW + timedelta(days=1)
    await check(w, PRICING)
    assert page.missing_count == 1
    w.transport.pages["/pricing"] = long_page("Pricing", "price")
    w.clock += timedelta(hours=2)
    await check(w, PRICING)
    assert page.missing_count == 0 and page.status == "learned"


# ---------------------------------------- the sitemap short-cut and a date-only <lastmod>
async def test_a_date_only_lastmod_on_the_day_of_the_last_change_still_fetches_the_page() -> None:
    w = await learned(base_pages(**{"/sitemap.xml": sitemap((PRICING, "2026-10-05"))}))
    page = w.pages()[PRICING]
    assert page.last_changed_at == NOW  # learned at 10:00 UTC on 5 October
    w.transport.pages["/pricing"] = long_page("Pricing", "edited-later-that-day")
    w.clock = NOW + timedelta(hours=6)
    before = len(w.transport.requests)
    out = await check(w, PRICING)
    assert PRICING in requested(w, before) and out.outcomes[page.id] == "changed"


# ------------------------------------------------------------- the scheduler is not starved
def add_site(w: World, name: str, *, tenant_status: str = "active") -> Any:
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    tenant = w.db.add(Tenant(name=name, plan_id=w.plan.id, status=tenant_status))
    corpus = w.db.add(
        Corpus(
            name=name,
            tenant_id=tenant.id,
            qdrant_collection=f"corpus_{uuid.uuid4().hex[:8]}",
            api_key_hash="",
            is_system=False,
        )
    )
    source = w.db.add(
        WebsiteSource(
            corpus_id=corpus.id,
            tenant_id=tenant.id,
            origin=f"https://{name}.example",
            scope=f"{name}.example",
            status="ready",
        )
    )
    return tenant, corpus, source


def add_site_page(w: World, corpus: Any, source: Any, n: int = 1) -> list[WebsitePage]:
    return [
        w.db.add(
            WebsitePage(
                source_id=source.id,
                corpus_id=corpus.id,
                url=f"{source.origin}/p{i}",
                url_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                status="learned",
                document_id=uuid.uuid4(),
                interval_seconds=86_400,
                next_check_at=NOW - timedelta(days=2),
            )
        )
        for i in range(n)
    ]


async def test_a_parked_site_with_thousands_of_due_pages_does_not_starve_the_others() -> None:
    w = World({})
    w.source.status = "paused"
    for row in add_pages(w, 2_100):  # the oldest due pages, on a paused site
        row.next_check_at = NOW - timedelta(days=30)
    _t, corpus, source = add_site(w, "ranchi")
    wanted = add_site_page(w, corpus, source)
    spy = Spy()
    await tick(w, spy)
    assert spy.checks == [(str(source.id), [str(wanted[0].id)], "schedule")]


async def test_a_long_first_crawl_does_not_starve_the_others() -> None:
    w = World({})
    w.source.status = "learning"
    for row in add_pages(w, 2_100):
        row.next_check_at = NOW - timedelta(days=30)
    _t, corpus, source = add_site(w, "nashik")
    wanted = add_site_page(w, corpus, source)
    spy = Spy()
    await tick(w, spy)
    assert [c[0] for c in spy.checks] == [str(source.id)]
    assert spy.checks[0][1] == [str(wanted[0].id)]


async def test_a_suspended_or_pending_workspace_is_not_checked() -> None:
    w = World({})
    _t1, c1, s1 = add_site(w, "kochi", tenant_status="suspended")
    _t2, c2, s2 = add_site(w, "mysuru", tenant_status="pending")
    _t3, c3, s3 = add_site(w, "jaipur")
    rows = add_site_page(w, c1, s1, 2) + add_site_page(w, c2, s2, 2)
    live = add_site_page(w, c3, s3)
    spy = Spy()
    await tick(w, spy)
    assert [c[0] for c in spy.checks] == [str(s3.id)]
    assert all(r.next_check_at == NOW - timedelta(days=2) for r in rows)  # due when they are back
    assert live[0].next_check_at > NOW


async def test_a_suspended_workspace_learns_nothing_even_when_a_run_was_queued() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    w.tenant.status = "suspended"
    before = len(w.transport.requests)
    result = await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert result.status == "disabled" and len(w.transport.requests) == before
    assert w.source.status != "paused"  # nothing is parked: it resumes with the workspace


# ------------------------------------------------ switching learning off and on again
async def test_turning_learning_off_and_on_again_restores_the_checks() -> None:
    from callobuzz_rag.website import service

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    await service.update_settings(
        w.db.session(),
        w.corpus,
        enabled=False,
        exclude_patterns=None,
        vector_store=w.vector_store,
        request=None,
    )
    w.source.status = "ready"  # a run that finished after the switch put the site back
    w.clock = NOW + timedelta(days=3)
    await tick(w, Spy())
    assert page.next_check_at is None  # nothing to check while it is off
    await service.update_settings(
        w.db.session(),
        w.corpus,
        enabled=True,
        exclude_patterns=None,
        vector_store=w.vector_store,
        request=None,
    )
    assert page.next_check_at is not None and page.next_check_at > NOW


async def test_a_page_without_a_next_check_gets_one_when_it_is_read_unchanged() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    page.next_check_at = page.interval_seconds = None  # learned before BIZ2, or paused once
    w.clock = NOW + timedelta(days=1)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.next_check_at is not None and page.next_check_at > w.clock
    assert page.interval_seconds == 86_400


# ---------------------------------- a hinted or pushed URL cannot add a duplicate page
async def test_a_url_that_serves_the_text_of_a_known_page_is_not_learned_again() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    # a soft 404 or catch-all route: any path answers with the pricing page's text
    w.transport.pages["/x1"] = long_page("Pricing", "price")
    w.transport.pages["/x2"] = long_page("Pricing", "price")
    batches = len(w.batches)
    await learn_urls(
        w.source.id,
        [f"{ORIGIN}/x1", f"{ORIGIN}/x2"],
        kind="sensor",
        trigger="sensor",
        deps=w.deps(),
    )
    pages = w.pages()
    assert pages[f"{ORIGIN}/x1"].status == pages[f"{ORIGIN}/x2"].status == "skipped"
    assert pages[f"{ORIGIN}/x1"].skip_reason == "duplicate" and len(w.batches) == batches


async def test_a_pushed_page_that_is_new_text_is_still_learned() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    w.transport.pages["/offers"] = long_page("Offers", "diwali-offers")
    await learn_urls(w.source.id, [f"{ORIGIN}/offers"], kind="push", trigger="push", deps=w.deps())
    assert w.pages()[f"{ORIGIN}/offers"].status == "learned"


# ----------------------------------- a successful read forgets an earlier "gone"
async def test_a_push_that_finds_the_page_back_forgets_the_miss() -> None:
    from callobuzz_rag.website.learn import learn_urls

    w = await learned(base_pages(**{"/sitemap.xml": ""}))
    page = w.pages()[PRICING]
    w.transport.pages["/pricing"] = httpx.Response(404)
    w.clock = NOW + timedelta(days=1)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.missing_count == 1
    w.transport.pages["/pricing"] = long_page("Pricing", "price")
    w.clock += timedelta(hours=2)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.missing_count == 0
    w.transport.pages["/pricing"] = httpx.Response(404)
    w.clock += timedelta(hours=3)
    await learn_urls(w.source.id, [PRICING], kind="push", trigger="push", deps=w.deps())
    assert page.status == "learned" and page.missing_count == 1  # not "twice": it answered between
