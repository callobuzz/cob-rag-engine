"""Keeping learned pages fresh, cheaply (BIZ2): one check of a page, and the scheduler.

Cheap checks come first (spec 2.2). The site's sitemap is read once per check run, conditionally
(its own ETag / Last-Modified), and a page whose `<lastmod>` is not newer than the last time its
text changed is **not requested at all**. Otherwise the page is fetched conditionally (`304` =
unchanged, no body). A `200` is extracted and its Markdown hashed: the same text under new markup
is unchanged (only the validators are refreshed). Only different text goes through the records
pipeline again, as a new version of the same document. So an unchanged page costs no embedding
and no ingest job.

A page leaves the answers (archived, history kept, D8) when the site says it is gone twice, at
least an hour apart (404/410), or when it now forbids reading (`noindex`, robots.txt, a
redirect off the site). A failed read (timeout, 5xx) changes nothing: the site may only be down
for a moment, and so does a page whose *text* does not read (a maintenance or placeholder page,
a soft 404): that is one miss, looked at again in about an hour, and only a second one removes
the page. A page that is no longer in the sitemap is no longer counted as a sitemap page, but
only the site's own answer removes it: a page that still loads is a page the owner still has.

`schedule_checks` is the beat job: it moves every due page's `next_check_at` forward *before*
handing the pages to a worker (so a lost task can never cause a storm), closes runs whose worker
died, and restarts sites that were never read. Which sites may be checked is decided first (not
paused, blocked or still being read; an active workspace), and the due pages are then taken site
by site, so a site that cannot be checked can never use up the room another one needs.
"""

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.website import (
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)
from callobuzz_rag.website.fetch import (
    FetchBudgetExhausted,  # PIP3 fair use
    FetchOutcome,
    WebsiteFetcher,
    fetch_all,  # PIP3 fair use
)
from callobuzz_rag.website.freshness import BASE_SECONDS, RefreshMode, after_check, jitter
from callobuzz_rag.website.learn import (
    MISS_RECHECK_SECONDS,
    STALE_AFTER,
    LearnDeps,
    LearnResult,
    PageState,
    _execute,
    _Run,
    register_miss,
)
from callobuzz_rag.website.sitemap import (
    SitemapEntry,
    SitemapTooLargeError,
    collect_sitemaps,
    decompress,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MAX_PER_SOURCE_TICK: Final = 200
NEVER_RUN_AFTER: Final = timedelta(minutes=5)
# The site's own answers that take a page out of the answers; other failures change nothing.
_POLICY_SKIPS: Final = ("robots_disallowed", "redirect_off_site", "not_html", "out_of_scope")
_PARKED_SOURCES: Final = ("paused", "blocked", "discovering", "learning")

Outcome = str  # "unchanged" | "changed" | "removed" | "failed"


@dataclass
class CheckResult:
    result: LearnResult
    outcomes: dict[uuid.UUID, Outcome] = field(default_factory=dict)


class _CheckRun(_Run):
    outcomes: dict[uuid.UUID, Outcome]
    before: dict[uuid.UUID, int]  # a page's change_count when its check began


async def _read_sitemap(
    run: _CheckRun, fetcher: WebsiteFetcher
) -> tuple[dict[str, SitemapEntry] | None, bool]:
    """(entries by URL or None, trustworthy). `None` + trustworthy is a 304: nothing changed
    since the stored `lastmod`s were read. Not trustworthy: the sitemap could not be read."""
    deps, settings = run.deps, run.deps.settings
    async with deps.session_factory() as session:
        source = await session.get(WebsiteSource, run.source_id)
        if source is None:
            return None, False
        starts = list(source.sitemap_urls or []) or [run.origin + "/sitemap.xml"]
        etag, modified = source.sitemap_etag, source.sitemap_last_modified
    conditional = len(starts) == 1 and bool(etag or modified)
    seen: dict[str, Any] = {}

    async def fetch(url: str) -> bytes | None:
        first = url == starts[0]
        out = (
            await fetcher.get_document(url, etag=etag, last_modified=modified)
            if first and conditional
            else await fetcher.get_document(url)
        )
        if first:
            seen["first"] = out
        return out.body if out.status == 200 else None

    entries = await collect_sitemaps(
        fetch,
        starts,
        scope=run.scope,
        excludes=run.excludes,
        max_urls=settings.website_max_discovered,
        max_bytes=settings.website_max_response_bytes,
        port=_port(run.origin),
    )
    first: FetchOutcome | None = seen.get("first")
    if first is not None and first.status == 304:
        return None, True
    if not entries:
        return None, False
    is_index = False
    if first is not None and first.body is not None:
        try:
            head = decompress(first.body, settings.website_max_response_bytes)[:4096]
            is_index = b"sitemapindex" in head
        except (SitemapTooLargeError, ValueError):
            is_index = True
    async with deps.session_factory() as session:
        source = await session.get(WebsiteSource, run.source_id)
        if source is not None and first is not None and first.status == 200:
            # An index's children can change without the index changing: never short-cut it.
            source.sitemap_etag = None if is_index else first.etag
            source.sitemap_last_modified = None if is_index else first.last_modified
            await session.commit()
    return {e.url: e for e in entries}, True


def _port(origin: str) -> int | None:
    from urllib.parse import urlsplit

    return urlsplit(origin).port


async def _check(run: _CheckRun, fetcher: WebsiteFetcher, page_ids: Sequence[uuid.UUID]) -> str:
    deps = run.deps
    now = deps.clock()
    tally = {"unchanged": 0, "changed": 0, "removed": 0, "failed": 0}
    run.outcomes, run.before = {}, {}
    mode, adaptive = run.refresh_mode, run.adaptive
    if mode == "off":
        return "ready"
    by_id = {p.id: p for p in run.pages.values()}
    pages = [
        p
        for i in page_ids
        if (p := by_id.get(i)) is not None
        and p.status == "learned"
        and p.document_id is not None
        and not p.excluded_by_owner
    ]
    if not pages:
        return "ready"

    sitemap: dict[str, SitemapEntry] | None = None
    sitemap_ok = False
    if any(p.in_sitemap for p in pages):
        sitemap, sitemap_ok = await _read_sitemap(run, fetcher)

    def settle(page: PageState, outcome: Outcome, *, changed: bool = False) -> None:
        run.outcomes[page.id] = outcome
        tally[outcome] += 1
        page.dirty = True
        if outcome in ("unchanged", "changed"):
            page.missing_count = 0
            page.interval_seconds, page.next_check_at = after_check(
                mode, adaptive, page.interval_seconds, changed, now, page.url_hash
            )
        elif outcome == "failed":  # the site hiccuped: same interval, try again then
            base = page.interval_seconds or BASE_SECONDS.get(mode, 86_400)
            page.next_check_at = now + timedelta(seconds=jitter(base, page.url_hash))
        else:  # removed: the page has left the answers, nothing more to check
            page.next_check_at = page.interval_seconds = None

    to_fetch: list[PageState] = []
    for page in pages:
        entry = sitemap.get(page.url) if sitemap is not None else None
        if entry is not None:
            page.in_sitemap = True
            page.sitemap_lastmod = entry.lastmod or page.sitemap_lastmod
            if entry.priority is not None:
                page.sitemap_priority = round(entry.priority * 100)
        elif sitemap is not None and page.in_sitemap:
            page.in_sitemap = False  # no longer listed; only the site's own answer removes it
        lastmod = page.sitemap_lastmod
        if (
            sitemap_ok
            and page.in_sitemap
            and lastmod is not None
            and page.last_changed_at is not None
            and lastmod <= page.last_changed_at
        ):
            page.last_checked_at = now  # nothing to ask the site: the sitemap says no change
            settle(page, "unchanged")
            continue
        to_fetch.append(page)

    concurrency = max(1, deps.settings.website_concurrency)
    limited = False  # PIP3 fair use: the day's fetches ran out
    for start in range(0, len(to_fetch), concurrency):
        group = to_fetch[start : start + concurrency]
        try:
            outs = await _gather(fetcher, group)
        except FetchBudgetExhausted:
            limited = True
            break
        for page, out in zip(group, outs, strict=True):
            await _apply(run, page, out, now, settle)
        await run.flush()
    await run.flush(force=True)

    for page in to_fetch:
        if page.id in run.outcomes:
            continue
        if limited and page.id not in run.before:
            continue  # PIP3 fair use: never asked, so neither changed nor failed: checked later
        if page.status == "learned" and page.id in run.kept:
            # its text did not read (a maintenance page, a soft 404): a failed read, not a change
            settle(page, "failed")
            page.next_check_at = now + timedelta(seconds=MISS_RECHECK_SECONDS)
        elif page.status == "learned":
            changed = page.change_count > run.before.get(page.id, page.change_count)
            settle(page, "changed" if changed else "unchanged", changed=changed)
        elif page.status in ("queued", "failed"):  # the pipeline did not take it
            settle(page, "failed")
        else:  # skipped: noindex, robots.txt, a redirect off the site... (already archived)
            settle(page, "removed")
    run.counts = dict(tally)
    if limited:
        run.limited_by = "fetch_limit"  # PIP3 fair use
    return "ready"


async def _gather(fetcher: WebsiteFetcher, group: Sequence[PageState]) -> list[FetchOutcome]:
    return await fetch_all(  # PIP3 fair use: the same gather, ending cleanly on the day's limit
        *(fetcher.get(p.url, etag=p.etag, last_modified=p.last_modified) for p in group)
    )


async def _apply(
    run: _CheckRun,
    page: PageState,
    out: FetchOutcome,
    now: datetime,
    settle: Callable[..., None],
) -> None:
    """One fetched page: decide what the answer means."""
    page.http_status = out.status or page.http_status
    if out.status == 304:
        page.last_checked_at = page.last_seen_at = now
        page.check_count += 1
        settle(page, "unchanged")
        return
    if out.status in (404, 410):
        page.check_count += 1
        register_miss(page, now)
        if page.missing_count >= 2:
            await run.archive([page])
            page.status, page.skip_reason = "removed", None
            settle(page, "removed")
            return
        settle(page, "failed")  # not gone yet: it stays, and is looked at again soon
        page.next_check_at = now + timedelta(seconds=MISS_RECHECK_SECONDS)
        return
    if out.body is None and out.skip_reason not in _POLICY_SKIPS:
        settle(page, "failed")  # a timeout, a 5xx, a blocked address, an empty JS shell
        return
    run.before[page.id] = page.change_count
    await run.handle(page.url, page.depth, out, None)


async def check_pages(
    source_id: uuid.UUID,
    page_ids: Sequence[uuid.UUID],
    *,
    trigger: str = "schedule",
    deps: LearnDeps,
) -> CheckResult:
    """Check these learned pages of one site (see the module docstring)."""
    run = _CheckRun(deps, source_id, kind="check", trigger=trigger, discovery=False)
    run.outcomes = {}

    async def work(fetcher: WebsiteFetcher) -> str:
        return await _check(run, fetcher, list(page_ids))

    result = await _execute(run, work)
    return CheckResult(result, dict(run.outcomes))


# ---------------------------------------------------------------------------- the scheduler


Dispatch = Callable[..., Any]


async def schedule_checks(
    session_factory: Callable[[], Any],
    settings: Settings,
    *,
    dispatch_check: Dispatch,
    dispatch_learn: Dispatch,
    now: datetime,
) -> dict[str, int]:
    """One tick: close dead runs, restart sites never read, hand due pages to workers.

    `dispatch_check(source_id, [page ids], trigger)` and `dispatch_learn(source_id, kind,
    trigger)` queue the work. Pages' `next_check_at` is moved forward and committed first.
    """
    out = {"closed_runs": 0, "restarted": 0, "due": 0, "dispatched": 0}
    if not settings.website_learning_enabled:
        return out
    to_learn: list[uuid.UUID] = []
    checks: dict[uuid.UUID, list[uuid.UUID]] = {}
    async with session_factory() as session:
        out["closed_runs"] = await _close_stale_runs(session, now)
        to_learn = await _never_run(session, now)
        out["restarted"] = len(to_learn)
        checks = await _due_pages(session, now)
        out["due"] = sum(len(v) for v in checks.values())
        await session.commit()
    for source_id in to_learn:
        dispatch_learn(str(source_id), "first", "schedule")
    for source_id, ids in checks.items():
        dispatch_check(str(source_id), [str(i) for i in ids], "schedule")
        out["dispatched"] += len(ids)
    return out


async def _close_stale_runs(session: "AsyncSession", now: datetime) -> int:
    rows = (
        (
            await session.execute(
                select(WebsiteRun).where(
                    WebsiteRun.finished_at.is_(None), WebsiteRun.heartbeat_at < now - STALE_AFTER
                )
            )
        )
        .scalars()
        .all()
    )
    for run in rows:
        run.status, run.finished_at, run.error_code = "failed", now, "stalled"
        source = await session.get(WebsiteSource, run.source_id)
        if source is not None and source.status in ("discovering", "learning"):
            source.status, source.last_error_code = "failed", "stalled"
    return len(rows)


async def _never_run(session: "AsyncSession", now: datetime) -> list[uuid.UUID]:
    idle = (
        (
            await session.execute(
                select(WebsiteSource).where(
                    WebsiteSource.status == "idle",
                    WebsiteSource.created_at < now - NEVER_RUN_AFTER,
                )
            )
        )
        .scalars()
        .all()
    )
    started: list[uuid.UUID] = []
    for source in idle:
        has_run = (
            await session.execute(select(WebsiteRun.id).where(WebsiteRun.source_id == source.id))
        ).first()
        if has_run is None:
            source.status, source.pause_reason = "discovering", None
            started.append(source.id)
    return started


async def _due_pages(session: "AsyncSession", now: datetime) -> dict[uuid.UUID, list[uuid.UUID]]:
    sources = [
        s
        for s in (await session.execute(select(WebsiteSource))).scalars().all()
        if s.status not in _PARKED_SOURCES
    ]
    if not sources:
        return {}
    active = {
        t.id
        for t in (
            (
                await session.execute(
                    select(Tenant).where(Tenant.id.in_({s.tenant_id for s in sources}))
                )
            )
            .scalars()
            .all()
        )
        if t.status == "active"  # suspended and pending workspaces are checked again when back
    }
    eligible = {s.id: s for s in sources if s.tenant_id in active}
    if not eligible:
        return {}
    has_due = {
        row[0]
        for row in (
            await session.execute(
                select(WebsitePage.source_id)
                .where(
                    WebsitePage.source_id.in_(set(eligible)),
                    WebsitePage.status == "learned",
                    WebsitePage.next_check_at.is_not(None),
                    WebsitePage.next_check_at <= now,
                )
                .distinct()
            )
        ).all()
    }
    configs = {
        s.corpus_id: s
        for s in (
            (
                await session.execute(
                    select(WebsiteSettings).where(
                        WebsiteSettings.corpus_id.in_({eligible[i].corpus_id for i in has_due})
                    )
                )
            )
            .scalars()
            .all()
        )
    }
    out: dict[uuid.UUID, list[uuid.UUID]] = {}
    for source_id in sorted(has_due, key=str):
        # At most MAX_PER_SOURCE_TICK per site, the longest overdue first: a site with a
        # backlog is worked off a slice at a time and never holds up another site.
        pages = (
            (
                await session.execute(
                    select(WebsitePage)
                    .where(
                        WebsitePage.source_id == source_id,
                        WebsitePage.status == "learned",
                        WebsitePage.next_check_at.is_not(None),
                        WebsitePage.next_check_at <= now,
                    )
                    .order_by(WebsitePage.next_check_at)
                    .limit(MAX_PER_SOURCE_TICK)
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        config = configs.get(eligible[source_id].corpus_id)
        mode: RefreshMode = config.refresh_mode if config is not None else "daily"  # type: ignore[assignment]
        for page in pages:
            if mode == "off" or (config is not None and not config.enabled):
                page.next_check_at = None  # nothing to check until the owner turns it back on
                continue
            interval = page.interval_seconds or BASE_SECONDS[mode]
            page.next_check_at = now + timedelta(seconds=jitter(interval, page.url_hash))
            out.setdefault(source_id, []).append(page.id)
    return out
