"""Learning a business's website (BIZ1): discovery runs and single-page reads.

`learn_source` reads a whole site: the start page and the sitemap seed a best-first frontier
(`discover.Frontier`), pages are fetched under the deployer's cap (`policy`), turned into records
(`extract`, near-duplicates skipped by `dedupe`) and ingested ten at a time (`ingest`). Every page
leaves a `website_pages` row saying what became of it, in words the owner can read.
`learn_urls` reads just some URLs (a page re-read now, a pushed URL, a widget hint); it is also
what BIZ2's freshness checks call.

The collaborators (`fetcher`, `ingest`, `notify`, `room`) are arguments of `LearnDeps`, so the
logic is tested without a network, a vector store or an embedder; `worker/website_tasks.py` wires
the real ones. A run is recorded in `website_runs` with a heartbeat: a run silent for 15 minutes
is closed as failed by the next start, and only one discovery run (`first` or `full`) can be open
per site (a partial unique index), so a double click or a repeated trigger is harmless.
"""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.email.dispatch import enqueue_email
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.website import (
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)
from callobuzz_rag.website import archive as archive_mod
from callobuzz_rag.website import ingest as ingest_mod
from callobuzz_rag.website.dedupe import NearDuplicateIndex, from_db, simhash, to_db
from callobuzz_rag.website.discover import Cand, Frontier, path_depth
from callobuzz_rag.website.extract import extract_page
from callobuzz_rag.website.fetch import (
    FetchBudgetExhausted,  # PIP3 fair use
    FetchOutcome,
    WebsiteFetcher,
    fetch_all,  # PIP3 fair use
)
from callobuzz_rag.website.freshness import RefreshMode, after_check, first_check  # BIZ2 freshness
from callobuzz_rag.website.netguard import BlockedURLError
from callobuzz_rag.website.policy import WebsitePolicy, website_policy
from callobuzz_rag.website.rank import score
from callobuzz_rag.website.sitemap import collect_sitemaps
from callobuzz_rag.website.urls import (
    DEFAULT_EXCLUDES,
    canonical,
    in_scope,
    is_excluded,
    url_hash,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.pipeline.records import TextRecord

logger = logging.getLogger(__name__)

STALE_AFTER: Final = timedelta(minutes=15)
THIN_WORDS: Final = 40
DISCOVERY_KINDS: Final = ("first", "full")
Status = Literal["done", "busy", "stopped", "paused", "disabled", "failed", "blocked"]
# BIZ2 freshness: what a "the site says it is gone" is worth (D8)
MISS_GAP: Final = timedelta(hours=1)  # two "gone" answers must be this far apart
MISS_RECHECK_SECONDS: Final = 3_900  # look again a little over an hour after a "gone"
# /BIZ2 freshness

# Fetch skip reasons that are a page's own state, not a failure to read it.
_SKIPPED: Final = {
    "robots_disallowed": "robots_disallowed",
    "redirect_off_site": "redirect_off_site",
    "not_html": "not_html",
    "needs_browser": "needs_browser",
    "out_of_scope": "out_of_scope",
}
_FAILED: Final = ("blocked_address", "too_large", "timeout", "http_error")
# A learned page that now ends in one of these states leaves the answers; a failed read (a
# timeout, a 500) keeps what was learned, because the site may only be down for a moment.
_LEAVES_ANSWERS: Final = (
    "robots_disallowed",
    "redirect_off_site",
    "canonical_off_site",
    "not_html",
    "out_of_scope",
    "private_page",
    "pattern",
    "owner",
    "thin",
    "duplicate",
    "noindex",
)
# BIZ2 freshness: what a page's *text* says, not the site: a maintenance or placeholder page, a
# soft 404, an empty JS shell. On a page that was already learned and is read again (a check, a
# push, a sensor hint, "read again") one such answer is a failed read; only a second one, an
# hour or more later, takes the page out of the answers.
_CONTENT_DROPS: Final = ("thin", "duplicate", "needs_browser", "canonical_off_site")
# /BIZ2 freshness


@dataclass
class LearnResult:
    status: Status
    run_id: uuid.UUID | None = None
    counts: dict[str, int | str] = field(default_factory=dict)
    reason: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC)


async def notify_first_pass(
    session_factory: Callable[[], Any],
    settings: Settings,
    source_id: uuid.UUID,
    learned: int,
    skipped: int,
) -> None:
    """Tell the workspace's owners their website has been read (once per site)."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.settings_store import get_engine_settings

    async with session_factory() as session:
        source = await session.get(WebsiteSource, source_id)
        if source is None:
            return
        tenant = await session.get(Tenant, source.tenant_id)
        members = (
            (
                await session.execute(
                    select(TenantMember).where(
                        TenantMember.tenant_id == source.tenant_id, TenantMember.role == "owner"
                    )
                )
            )
            .scalars()
            .all()
        )
        addresses = []
        for member in members:
            user = await session.get(User, member.user_id)
            if user is not None and user.email:
                addresses.append(user.email)
        scope, business = source.scope, tenant.name if tenant else source.scope
    engine_settings = await get_engine_settings(session_factory)
    context = {
        "business_name": business,
        "product_name": engine_settings.branding.product_name,
        "site": scope,
        "pages_learned": learned,
        "pages_skipped": skipped,
        "knowledge_url": dashboard_link(settings.dashboard_url, "knowledge"),
    }
    for address in addresses:
        try:
            enqueue_email("website_learned", address, context, expires_s=24 * 3600)
        except Exception:
            logger.warning("Could not queue the website_learned e-mail", exc_info=True)


@dataclass
class LearnDeps:
    settings: Settings
    session_factory: Callable[[], Any]
    fetcher: Callable[[WebsiteSource], WebsiteFetcher]
    ingest: ingest_mod.IngestFn
    vector_store: Any = None  # for archiving a page that became noindex
    notify: Callable[[uuid.UUID, int, int], Awaitable[None]] | None = None
    room: Callable[["AsyncSession", Corpus], Awaitable[int | None]] = ingest_mod.document_room
    clock: Callable[[], datetime] = _utcnow
    # PIP3 fair use: new pages the business may still learn today (None: no daily limit). With
    # a limit a run TAKES its pages from the business's shared counter as it needs them
    # (`reserve_pages`, atomic: runs at the same time never pass the limit together) and gives
    # back what it did not learn (`release_pages`); without one `on_pages` counts what it learned
    pages_left: Callable[["AsyncSession", Corpus], Awaitable[int | None]] | None = None
    on_pages: Callable[[uuid.UUID, int], Awaitable[None]] | None = None
    reserve_pages: Callable[[uuid.UUID, int], Awaitable[int]] | None = None
    release_pages: Callable[[uuid.UUID, int], Awaitable[None]] | None = None
    # called once when a run that learned something ends: the owner and the deployer are told
    # when the day's pages or the plan's page total reached 80 % or the limit
    usage_alert: Callable[[uuid.UUID], None] | None = None
    # /PIP3 fair use


_PAGE_COLUMNS: Final = [
    "status",
    "skip_reason",
    "duplicate_of",
    "title",
    "depth",
    "rank_score",
    "in_sitemap",
    "sitemap_lastmod",
    "sitemap_priority",
    "http_status",
    "etag",
    "last_modified",
    "content_hash",
    "simhash",
    "words",
    "document_id",
    "excluded_by_owner",
    "last_seen_at",
    "last_checked_at",
    "last_changed_at",
    "check_count",
    "change_count",
    "missing_count",
    "interval_seconds",
    "next_check_at",
]


@dataclass
class PageState:
    """One `website_pages` row, in memory while a run works on it."""

    id: uuid.UUID
    url: str
    url_hash: str
    depth: int = 0
    status: str = "discovered"
    skip_reason: str | None = None
    duplicate_of: uuid.UUID | None = None
    title: str | None = None
    rank_score: int = 0
    in_sitemap: bool = False
    sitemap_lastmod: datetime | None = None
    sitemap_priority: int | None = None
    http_status: int | None = None
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    simhash: int | None = None
    words: int | None = None
    document_id: uuid.UUID | None = None
    excluded_by_owner: bool = False
    last_seen_at: datetime | None = None
    last_checked_at: datetime | None = None
    last_changed_at: datetime | None = None
    check_count: int = 0
    change_count: int = 0
    missing_count: int = 0
    interval_seconds: int | None = None
    next_check_at: datetime | None = None
    existing: bool = False  # a row exists already (so it is updated, not inserted)
    dirty: bool = True

    @classmethod
    def from_row(cls, row: WebsitePage) -> "PageState":
        state = cls(id=row.id, url=row.url, url_hash=row.url_hash, existing=True, dirty=False)
        for name in _PAGE_COLUMNS:
            setattr(state, name, getattr(row, name))
        return state

    def apply(self, row: WebsitePage) -> None:
        for name in _PAGE_COLUMNS:
            setattr(row, name, getattr(self, name))


# BIZ2 freshness
def register_miss(page: "PageState", now: datetime) -> bool:
    """Count one "the site says it is gone" for `page`; False when the last one is under an
    hour old (it is the same moment, not a second opinion)."""
    if (
        page.missing_count >= 1
        and page.last_checked_at is not None
        and now - page.last_checked_at < MISS_GAP
    ):
        return False
    page.missing_count += 1
    page.last_checked_at = now
    return True


# /BIZ2 freshness


class _GoneError(Exception):
    """The site (or its knowledge base) was deleted while the run was working."""


class _Run:
    def __init__(
        self,
        deps: LearnDeps,
        source_id: uuid.UUID,
        *,
        kind: str,
        trigger: str,
        discovery: bool,
    ) -> None:
        self.deps = deps
        self.source_id = source_id
        self.kind = kind
        self.trigger = trigger
        self.discovery = discovery
        self.run_id: uuid.UUID | None = None
        self.pages: dict[str, PageState] = {}
        self.counts: dict[str, int] = {
            "learned": 0,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
            "files_found": 0,
        }
        self.error_code: str | None = None
        # PIP3 fair use: why a run stopped early without dropping anything (`daily_limit` or
        # `fetch_limit`); written into the run's counts for the resume job
        self.limited_by: str | None = None
        self.files: set[str] = set()
        self.excludes: list[str] = []
        self.near = NearDuplicateIndex()
        # snapshots taken at the start (the ORM rows belong to a session that has closed)
        self.corpus_id: uuid.UUID
        self.tenant_id: uuid.UUID
        self.qdrant_collection: str | None = None
        self.origin = ""
        self.scope = ""
        self.source: WebsiteSource
        self.policy: WebsitePolicy
        self.batch: list[tuple[PageState, TextRecord]] = []
        self.new_accepted = 0  # accepted pages that are not documents yet
        self.pages_reserved = 0  # PIP3 fair use: new pages taken from today's shared counter
        self.pages_daily_bound = False  # ... because the business has a daily pages limit
        self.pages_settled = False
        self.deferred_urls: set[str] = set()  # learned pages a fetch-bound run did not re-read
        self.accepted = 0  # accepted pages in all: they count against the page cap
        self.visited: set[str] = set()
        self.fetcher: WebsiteFetcher | None = None  # set by `_execute`; robots for canonicals
        # BIZ2 freshness: the knowledge base's schedule, read at the start of the run
        self.refresh_mode: RefreshMode = "daily"
        self.adaptive = True
        self.kept: set[uuid.UUID] = set()  # learned pages whose text did not read (see drop)
        # /BIZ2 freshness

    # ------------------------------------------------------------------ start

    async def start(self) -> LearnResult | None:
        d = self.deps
        now = d.clock()
        async with d.session_factory() as session:
            source = await session.get(WebsiteSource, self.source_id)
            if source is None:
                return LearnResult("stopped", reason="gone")
            corpus = await session.get(Corpus, source.corpus_id)
            if corpus is None:
                return LearnResult("stopped", reason="gone")
            self.source, self.corpus_id, self.tenant_id = source, corpus.id, corpus.tenant_id
            self.qdrant_collection = corpus.qdrant_collection
            self.origin, self.scope = source.origin.rstrip("/"), source.scope

            runs = (
                (
                    await session.execute(
                        select(WebsiteRun).where(
                            WebsiteRun.source_id == source.id, WebsiteRun.finished_at.is_(None)
                        )
                    )
                )
                .scalars()
                .all()
            )
            open_discovery = False
            for run in runs:
                if run.heartbeat_at < now - STALE_AFTER:
                    run.status, run.finished_at, run.error_code = "failed", now, "stalled"
                elif run.kind in DISCOVERY_KINDS:
                    open_discovery = True
            if self.discovery and open_discovery:
                await session.commit()
                return LearnResult("busy")

            self.policy = await website_policy(
                session, corpus, d.settings, exclude_source_id=source.id
            )
            if not self.policy.enabled and self.policy.reason == "tenant":
                await session.commit()  # BIZ2: a suspended workspace parks nothing; it resumes
                return LearnResult("disabled", reason="tenant")
            if not self.policy.enabled:
                source.status = "paused"
                source.pause_reason = "plan" if self.policy.reason == "plan" else "disabled"
                await session.commit()
                return LearnResult("disabled", reason=self.policy.reason)
            if source.status == "paused" and source.pause_reason == "origin_removed":
                await session.commit()
                return LearnResult("paused", reason="origin_removed")
            if source.pause_reason in ("plan", "disabled"):
                source.pause_reason = None  # the policy allows it again

            run = WebsiteRun(
                id=uuid.uuid4(),
                source_id=source.id,
                corpus_id=corpus.id,
                kind=self.kind,
                trigger=self.trigger,
                started_at=now,
                heartbeat_at=now,
                counts={},
            )
            session.add(run)
            if self.discovery:
                source.status = "discovering"
                source.last_run_started_at = now
                source.last_error_code = None
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return LearnResult("busy")  # another worker opened the discovery run first
            self.run_id = run.id

            settings_row = (
                await session.execute(
                    select(WebsiteSettings).where(WebsiteSettings.corpus_id == corpus.id)
                )
            ).scalar_one_or_none()
            self.excludes = list(settings_row.exclude_patterns) if settings_row else []
            if settings_row is not None:  # BIZ2 freshness
                self.refresh_mode = cast("RefreshMode", settings_row.refresh_mode)
                self.adaptive = settings_row.adaptive
            rows = (
                (
                    await session.execute(
                        select(WebsitePage).where(WebsitePage.source_id == source.id)
                    )
                )
                .scalars()
                .all()
            )
            self.pages = {r.url: PageState.from_row(r) for r in rows}
        if not self.discovery:  # BIZ2: a single URL is compared with what the site already holds
            for url, page in self.pages.items():
                if page.status == "learned" and page.simhash is not None:
                    self.near.add_hash(url, from_db(page.simhash))
        return None

    # ------------------------------------------------------------------ persistence

    async def save(self, *, status: str | None = None, final: bool = False) -> bool:
        """Write dirty pages, the run's heartbeat and the site's counters. Returns True when
        this call made the site's first pass complete (the caller sends the e-mail)."""
        d = self.deps
        now = d.clock()
        first_pass_done = False
        async with d.session_factory() as session:
            source = await session.get(WebsiteSource, self.source_id)
            run = await session.get(WebsiteRun, self.run_id) if self.run_id else None
            if source is None:
                raise _GoneError
            dirty = [p for p in self.pages.values() if p.dirty]
            existing: dict[str, WebsitePage] = {}
            if dirty:
                rows = (
                    (
                        await session.execute(
                            select(WebsitePage).where(
                                WebsitePage.source_id == self.source_id,
                                WebsitePage.url_hash.in_([p.url_hash for p in dirty]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                existing = {r.url_hash: r for r in rows}
            for page in dirty:
                row = existing.get(page.url_hash)
                if row is None:
                    row = WebsitePage(
                        id=page.id,
                        source_id=self.source_id,
                        corpus_id=self.corpus_id,
                        url=page.url,
                        url_hash=page.url_hash,
                    )
                    session.add(row)
                page.apply(row)
                row.updated_at = now
                page.dirty, page.existing = False, True

            states = [p.status for p in self.pages.values()]
            if self.discovery or final:
                source.pages_found = len(states)
                source.pages_learned = states.count("learned")
                source.pages_skipped = sum(1 for s in states if s in ("skipped", "excluded"))
            if self.discovery and status is not None:
                source.status = status
            if self.discovery and final:
                source.last_run_finished_at = now
                source.last_error_code = self.error_code
                if (
                    source.first_pass_done_at is None
                    and source.pages_learned >= 1
                    and status == "ready"
                ):
                    source.first_pass_done_at = now
                    first_pass_done = True
            if run is not None:
                run.heartbeat_at = now
                run.counts = {**self.counts, "found": len(states)}
                if self.limited_by:  # PIP3 fair use
                    run.counts["limited_by"] = self.limited_by
                if final:
                    run.finished_at = now
                    run.status = "done" if status in ("ready",) else "failed"
                    run.error_code = self.error_code
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _GoneError from exc
        return first_pass_done

    # ------------------------------------------------------------------ pages

    def state(self, url: str, *, depth: int | None = None) -> PageState:
        page = self.pages.get(url)
        if page is None:
            page = PageState(
                id=uuid.uuid4(),
                url=url,
                url_hash=url_hash(url),
                depth=path_depth(url) if depth is None else depth,
            )
            self.pages[url] = page
        return page

    def mark(
        self,
        page: PageState,
        status: str,
        reason: str | None = None,
        *,
        duplicate_of: PageState | None = None,
    ) -> None:
        page.status, page.skip_reason, page.dirty = status, reason, True
        page.duplicate_of = duplicate_of.id if duplicate_of else None
        now = self.deps.clock()
        page.last_checked_at = now
        if status == "skipped":
            self.counts["skipped"] += 1
        elif status == "failed":
            self.counts["failed"] += 1

    async def drop(
        self,
        page: PageState,
        status: str,
        reason: str,
        *,
        duplicate_of: PageState | None = None,
    ) -> bool:
        """Mark a page as not learned; one that was learned also leaves the answers. Returns
        True when a learned page was kept instead (BIZ2: its text did not read once; see
        `_CONTENT_DROPS`), and then nothing about the page changed."""
        held = page.document_id is not None and page.status == "learned"
        if held and not self.discovery and reason in _CONTENT_DROPS:  # BIZ2 freshness
            now = self.deps.clock()
            register_miss(page, now)
            if page.missing_count < 2:
                self.kept.add(page.id)
                if self.refresh_mode != "off":
                    page.next_check_at = now + timedelta(seconds=MISS_RECHECK_SECONDS)
                page.dirty = True
                self.counts["failed"] += 1
                return True
        self.mark(page, status, reason, duplicate_of=duplicate_of)
        if held and reason in _LEAVES_ANSWERS:
            await self.archive([page])
        return False

    async def drop_over_limit(self, page: PageState) -> None:
        held = page.document_id is not None
        self.mark(page, "skipped", "over_limit")
        if held:
            await self.archive([page])

    async def refusal_for(self, url: str) -> str | None:
        """Why a page's canonical or redirect target must not be learned (a private page, the
        owner's patterns, a page left out, robots.txt), or None. The target gets the same
        checks as a link that points straight at it."""
        hit = is_excluded(url, self.excludes)
        if hit is not None:
            self.is_blocked(url)  # records the target, so the owner sees why
            return "private_page" if hit in DEFAULT_EXCLUDES else "pattern"
        known = self.pages.get(url)
        if known is not None and known.excluded_by_owner:
            return "owner"
        if self.fetcher is not None:
            try:
                if not await self.fetcher.allowed(url):
                    return "robots_disallowed"
            except BlockedURLError:
                return "robots_disallowed"
        return None

    async def archive(self, pages: Sequence[PageState]) -> None:
        """Take pages that are no longer wanted out of the answers (they keep their history)."""
        with_docs = [p for p in pages if p.document_id is not None]
        if not with_docs or self.deps.vector_store is None:
            return
        async with self.deps.session_factory() as session:
            corpus = await session.get(Corpus, self.corpus_id)
            if corpus is None:
                raise _GoneError
            rows = (
                (
                    await session.execute(
                        select(WebsitePage).where(WebsitePage.id.in_([p.id for p in with_docs]))
                    )
                )
                .scalars()
                .all()
            )
            await archive_mod.archive_pages(session, self.deps.vector_store, corpus, rows)
            await session.commit()

    # ------------------------------------------------------------------ one fetched page

    async def handle(
        self,
        cand_url: str,
        cand_depth: int,
        out: FetchOutcome,
        frontier: Frontier | None,
        *,
        from_start_page: bool = False,
    ) -> None:
        """Decide what became of one fetched URL; queue its links when `frontier` is given."""
        d = self.deps
        now = d.clock()
        page = self.state(cand_url, depth=cand_depth)
        self.visited.add(cand_url)
        page.http_status = out.status or page.http_status
        page.last_seen_at = now
        page.check_count += 1
        if out.skip_reason is not None:
            reason = out.skip_reason
            if reason in _SKIPPED:
                await self.drop(page, "skipped", _SKIPPED[reason])
            else:
                self.mark(page, "failed", reason)
            if reason == "not_html":
                self.counts["files_found"] += 1
            return
        if out.body is None:
            self.mark(page, "failed", "http_error")
            return

        ext = extract_page(out.body, out.url)
        old_title = page.title
        page.title = (ext.title or None) and ext.title[:500]
        self.files.update(ext.files)
        if "noindex" in out.x_robots or ext.noindex:
            await self.drop(page, "skipped", "noindex")
            return

        # The page's own address: where it ended after redirects, or its declared canonical.
        target = cand_url
        effective = ext.canonical_url or out.url
        if effective != cand_url:
            if not in_scope(effective, self.scope, port=_port(self.origin)):
                if await self.drop(page, "skipped", "canonical_off_site"):
                    page.title = old_title
                return
            refusal = await self.refusal_for(effective)
            if refusal is not None:
                await self.drop(page, "skipped", refusal)
                return
            target = effective
        if target != cand_url:
            original = page
            page = self.state(target, depth=cand_depth)
            if await self.drop(original, "skipped", "duplicate", duplicate_of=page):
                original.title = old_title
            if target in self.visited:
                return  # the canonical page was already handled in this run
            self.visited.add(target)
            page.http_status, page.last_seen_at = out.status, now
            page.check_count += 1
            page.title = (ext.title or None) and ext.title[:500]

        if ext.words < THIN_WORDS:
            if await self.drop(page, "skipped", "thin"):
                page.title = old_title
            return

        dup = self.near.find(ext.markdown)
        if dup is not None:
            owner = self.pages.get(str(dup[0]))
            if owner is not None and owner is not page:
                if await self.drop(page, "skipped", "duplicate", duplicate_of=owner):
                    page.title = old_title
                return
        self.near.add(page.url, ext.markdown)
        page.missing_count = 0  # BIZ2: it answered with a page of its own: no longer "gone"

        if frontier is not None and not ext.nofollow:
            self.queue_links(frontier, ext, depth=page.depth, from_start=from_start_page)

        page.etag, page.last_modified = out.etag, out.last_modified
        page.words = ext.words
        page.simhash = to_db(simhash(ext.markdown))
        unchanged = (
            page.status == "learned"
            and page.document_id is not None
            and page.content_hash == ext.content_hash
        )
        page.content_hash = ext.content_hash
        if unchanged:
            page.dirty = True
            page.last_checked_at = now
            if page.next_check_at is None:  # BIZ2: a page that lost its schedule gets one back
                page.interval_seconds, page.next_check_at = first_check(
                    self.refresh_mode, self.adaptive, now, page.url_hash
                )
            self.counts["unchanged"] += 1
            self.accepted += 1
            return
        page.status, page.skip_reason, page.dirty = "queued", None, True
        self.accepted += 1
        if page.document_id is None:
            self.new_accepted += 1
        record = ingest_mod.page_record(
            page.url, ext.title, ext.markdown, self.scope, fallback_title=page.url
        )
        self.batch.append((page, record))

    def queue_links(self, frontier: Frontier, ext: Any, *, depth: int, from_start: bool) -> None:
        port = _port(self.origin)
        for link in ext.links:
            if link.nofollow or depth + 1 > self.deps.settings.website_max_depth:
                continue
            if frontier.known >= self.deps.settings.website_max_discovered:
                break
            url = link.url
            if not in_scope(url, self.scope, port=port):
                continue
            if self.is_blocked(url):
                continue
            frontier.add(
                url,
                depth=depth + 1,
                in_nav=from_start and link.region == "nav",
                in_footer=from_start and link.region == "footer",
            )

    def is_blocked(self, url: str) -> bool:
        """Excluded URLs are recorded (so the owner sees why) and never requested."""
        hit = is_excluded(url, self.excludes)
        if hit is None:
            return False
        page = self.state(url)
        if page.status in ("learned", "queued") and page.document_id is not None:
            return True  # handled by the settings route (archived there)
        if hit in DEFAULT_EXCLUDES:
            self.mark(page, "skipped", "private_page")
        else:
            self.mark(page, "excluded", "pattern")
        return True

    # ------------------------------------------------------------------ batches

    async def flush(self, *, force: bool = False) -> None:
        """Ingest full batches of ten (all that is left when `force`)."""
        size = ingest_mod.BATCH_SIZE
        while len(self.batch) >= size or (force and self.batch):
            batch, self.batch = self.batch[:size], self.batch[size:]
            await self._flush_batch(batch)

    async def _flush_batch(self, batch: list[tuple[PageState, "TextRecord"]]) -> None:
        d = self.deps
        async with d.session_factory() as session:
            corpus = await session.get(Corpus, self.corpus_id)
            if corpus is None:
                raise _GoneError
            room = await d.room(session, corpus)
            known = await ingest_mod.known_documents(
                session, self.corpus_id, [r.id for _, r in batch]
            )
        keep: list[tuple[PageState, TextRecord]] = []
        spent = 0
        for page, record in batch:
            new = record.id not in known
            if new and room is not None and spent >= room:
                self.mark(page, "skipped", "over_limit")
                self.accepted -= 1
                self.error_code = "document_limit"
                continue
            spent += 1 if new else 0
            keep.append((page, record))
        if keep:
            await self.save(status="learning")  # progress before the slow part
            outcome = await ingest_mod.ingest_batch(
                d.session_factory, d.ingest, self.corpus_id, [r for _, r in keep]
            )
            now = d.clock()
            for page, record in keep:
                doc_id = outcome.documents.get(record.id)
                if outcome.ok and doc_id is not None:
                    existing = page.document_id is not None
                    if existing:  # an existing page whose text changed
                        page.change_count += 1
                    page.status, page.skip_reason, page.document_id = "learned", None, doc_id
                    page.last_checked_at = page.last_changed_at = now
                    # BIZ2 freshness: when this page is looked at next
                    page.interval_seconds, page.next_check_at = (
                        after_check(
                            self.refresh_mode,
                            self.adaptive,
                            page.interval_seconds,
                            True,
                            now,
                            page.url_hash,
                        )
                        if existing
                        else first_check(self.refresh_mode, self.adaptive, now, page.url_hash)
                    )
                    # /BIZ2 freshness
                    self.counts["learned"] += 1
                else:
                    self.mark(page, "failed", "ingest_failed")
                    self.error_code = "ingest_failed"
                    self.accepted -= 1
                page.dirty = True
        await self.save(status="learning")

    # ------------------------------------------------------------------ discovery

    async def discover(self, fetcher: WebsiteFetcher) -> str:
        """Read the site best-first. Returns the site's resulting status."""
        d, s = self.deps, self.deps.settings
        port = _port(self.origin)
        start = canonical(self.origin + "/") or self.origin + "/"
        frontier = Frontier(start)
        frontier.add(start, depth=0)
        try:
            robots = await fetcher.robots()
        except BlockedURLError:
            self.error_code = "website_blocked"
            return "blocked"

        starts = [u for u in robots.sitemaps if in_scope(u, self.scope, port=port)] or [
            self.origin + "/sitemap.xml"
        ]

        async def fetch_sitemap(url: str) -> bytes | None:
            out = await fetcher.get_document(url)
            return out.body if out.status == 200 else None

        entries = await collect_sitemaps(
            fetch_sitemap,
            starts,
            scope=self.scope,
            excludes=self.excludes,
            max_urls=s.website_max_discovered,
            max_bytes=s.website_max_response_bytes,
            port=port,
        )
        for entry in entries:
            page = self.pages.get(entry.url)
            if page is not None and page.excluded_by_owner:
                continue
            frontier.add(
                entry.url,
                depth=path_depth(entry.url),
                in_sitemap=True,
                priority=entry.priority,
                lastmod=entry.lastmod,
            )
        async with d.session_factory() as session:
            source = await session.get(WebsiteSource, self.source_id)
            if source is None:
                raise _GoneError
            source.sitemap_urls = list(starts)
            await session.commit()

        cap = self.policy.pages_cap
        async with d.session_factory() as session:
            corpus = await session.get(Corpus, self.corpus_id)
            if corpus is None:
                raise _GoneError
            room = await d.room(session, corpus)
            # PIP3 fair use: the day's new pages are one more bound on the room, taken from the
            # business's shared counter as the run needs them (see `_reserve_new_pages`)
            left = await d.pages_left(session, corpus) if d.pages_left is not None else None
        self.pages_daily_bound = left is not None and d.reserve_pages is not None
        # a run with a fetch limit reads the pages it has not learned first: the learned ones
        # would use up the day's fetches again and again and the site would never grow
        defer_learned = await fetcher.budget_limited()
        deferred: list[Cand] = []
        # /PIP3 fair use
        limited_by: str | None = None
        start_blocked = False

        while True:
            if self.accepted >= cap:
                limited_by = "cap"
                break
            if room is not None and self.new_accepted >= room:
                limited_by = "room"
                break
            take = min(s.website_concurrency, cap - self.accepted)
            if room is not None:
                take = min(take, room - self.new_accepted)
            if self.pages_daily_bound:  # PIP3 fair use
                take = min(take, await self._reserve_new_pages(take))
                if take <= 0:
                    limited_by = "daily_limit"
                    break
            popped: list[Cand] = []
            # (a learned page that waits behind the new ones still holds its place under the cap)
            while len(popped) < take and self.accepted + len(popped) < cap:
                cand = frontier.pop()
                if cand is None:
                    break
                page = self.pages.get(cand.url)
                if page is not None and page.excluded_by_owner:
                    self.mark(page, "excluded", "owner")
                    continue
                if (  # PIP3 fair use: a learned page waits behind the new ones
                    defer_learned
                    and page is not None
                    and page.status == "learned"
                    and page.document_id is not None
                    and cand.url != start
                ):
                    deferred.append(cand)
                    self.deferred_urls.add(cand.url)
                    self.accepted += 1  # it keeps its place under the page cap
                    continue
                popped.append(cand)
            if not popped and self.accepted >= cap:
                continue  # the pages that waited filled the cap: the top of the loop says so
            if not popped and deferred:
                # nothing new is left to read: re-read the known pages (their links may lead
                # to new ones, which then go first again)
                popped, deferred = deferred[:take], deferred[take:]
                for cand in popped:
                    self.deferred_urls.discard(cand.url)
                    self.accepted -= 1  # `handle` counts it again
            if not popped:
                break
            try:  # PIP3 fair use: the day's fetches ran out; what was read is kept
                outcomes = await fetch_all(*(fetcher.get(c.url) for c in popped))
            except FetchBudgetExhausted:
                limited_by = "fetch_limit"
                break
            for cand, out in zip(popped, outcomes, strict=True):
                page = self.state(cand.url, depth=cand.depth)
                page.rank_score = score(cand.ranked(), start_url=start)
                page.in_sitemap = cand.in_sitemap or page.in_sitemap
                page.sitemap_lastmod = cand.lastmod or page.sitemap_lastmod
                page.sitemap_priority = (
                    round(cand.priority * 100)
                    if cand.priority is not None
                    else page.sitemap_priority
                )
                if cand.url == start and out.skip_reason == "blocked_address":
                    start_blocked = True
                await self.handle(
                    cand.url, cand.depth, out, frontier, from_start_page=cand.url == start
                )
            await self.flush()
            await self.save(status="learning" if self.accepted else "discovering")
            if start_blocked:
                self.error_code = "website_blocked"
                break

        await self.flush(force=True)
        # PIP3 fair use: a day's limit only delays pages, so they are left unmarked for the
        # resume run; the cap and the document room are real limits and still mark them
        delayed = limited_by in ("daily_limit", "fetch_limit")
        if delayed:
            self.limited_by = str(limited_by)
        for cand in frontier.remaining():  # found but not read: beyond the limit
            if delayed:
                break
            page = self.state(cand.url, depth=cand.depth)
            page.rank_score = score(cand.ranked(), start_url=start)
            page.in_sitemap = cand.in_sitemap
            if page.status not in ("learned", "queued"):
                self.mark(page, "skipped", "over_limit")
        if self.accepted >= cap:  # pages learned before that this run did not make room for
            unread = [
                p
                for p in self.pages.values()
                if p.status == "learned"
                and p.url not in self.visited
                and p.url not in self.deferred_urls
            ]
            for page in unread:
                await self.drop_over_limit(page)
        if limited_by == "room":
            self.error_code = "document_limit"
        self.counts["files_found"] = max(
            self.counts["files_found"], len(self.files | fetcher.files)
        )
        if start_blocked:
            return "blocked"
        learned = sum(1 for p in self.pages.values() if p.status == "learned")
        if learned == 0:
            self.error_code = self.error_code or (
                "page_limit"
                if limited_by == "cap"
                else limited_by  # PIP3 fair use: daily_limit / fetch_limit
                if delayed
                else _empty_reason(self.pages.get(start))
            )
            return "failed"
        return "ready"

    def result_counts(self) -> dict[str, Any]:  # PIP3 fair use
        """The run's counts as its result reports them: `limited_by` joins the numbers."""
        return {**self.counts, **({"limited_by": self.limited_by} if self.limited_by else {})}

    async def _reserve_new_pages(self, wanted: int) -> int:  # PIP3 fair use
        """How many new pages the run may take on now (at most `wanted`): what it already holds
        and has not used, plus what it can take from the business's shared daily counter."""
        spare = self.pages_reserved - self.new_accepted
        if spare < wanted and self.deps.reserve_pages is not None:
            got = await self.deps.reserve_pages(self.tenant_id, wanted - spare)
            self.pages_reserved += got
            spare += got
        return max(spare, 0)

    async def settle_pages(self) -> None:  # PIP3 fair use
        """Square the business's pages-today count with what this run learned, once, however
        the run ended (a crashed run counts what it had learned too): pages taken and not
        learned go back; without a limit the learned pages are added."""
        if self.pages_settled:
            return
        self.pages_settled = True
        d = self.deps
        if self.pages_daily_bound:
            unused = self.pages_reserved - self.new_accepted
            if unused > 0 and d.release_pages is not None:
                await d.release_pages(self.tenant_id, unused)
        elif d.on_pages is not None and self.new_accepted > 0:
            await d.on_pages(self.tenant_id, self.new_accepted)
        if d.usage_alert is not None and (self.new_accepted > 0 or self.limited_by):
            try:
                d.usage_alert(self.tenant_id)
            except Exception:
                logger.warning("The website usage alert could not be scheduled", exc_info=True)

    # BIZ2 freshness
    async def _keep_or_forget(self, page: PageState, out: FetchOutcome) -> bool:
        """A learned page that a push, a sensor hint or an owner's "read again" found gone or
        unreachable. Gone twice (404/410, an hour apart) archives it (D8); a failed read (a
        timeout, a 5xx) changes nothing, because the site may only be down for a moment. True
        when the answer was handled here."""
        now = self.deps.clock()
        if out.status in (404, 410):
            page.check_count += 1
            page.http_status = out.status
            register_miss(page, now)
            if page.missing_count >= 2:
                await self.archive([page])
                page.status, page.skip_reason = "removed", None
                page.next_check_at = page.interval_seconds = None
            else:
                page.next_check_at = now + timedelta(seconds=MISS_RECHECK_SECONDS)
            page.dirty = True
            return True
        if out.body is None and out.status != 304 and out.skip_reason not in _SKIPPED:
            page.last_checked_at = now
            page.dirty = True
            self.counts["failed"] += 1
            return True
        return False

    # /BIZ2 freshness

    # ------------------------------------------------------------------ single pages

    async def read_urls(self, fetcher: WebsiteFetcher, urls: Sequence[str]) -> str:
        """Read just these URLs (no link following). Conditional when validators are stored."""
        port = _port(self.origin)
        cap = self.policy.pages_cap
        chosen: list[str] = []
        for raw in urls:
            url = canonical(raw)
            if url is None or not in_scope(url, self.scope, port=port):
                continue
            if self.is_blocked(url):
                continue
            page = self.state(url)
            if page.excluded_by_owner:
                continue
            chosen.append(url)
        learned_now = sum(1 for p in self.pages.values() if p.status == "learned")
        # PIP3 fair use: the day's new pages bound a page-by-page read too
        if self.deps.pages_left is not None and self.deps.reserve_pages is not None:
            async with self.deps.session_factory() as session:
                corpus = await session.get(Corpus, self.corpus_id)
                left = await self.deps.pages_left(session, corpus) if corpus is not None else None
            self.pages_daily_bound = left is not None
        # /PIP3 fair use
        for url in dict.fromkeys(chosen):
            page = self.state(url)
            if page.status != "learned" and learned_now >= cap:
                self.mark(page, "skipped", "over_limit")
                continue
            if (
                page.status != "learned"
                and self.pages_daily_bound
                and await self._reserve_new_pages(1) < 1
            ):
                self.limited_by = "daily_limit"  # PIP3 fair use: delayed, not dropped
                break
            conditional = page.status == "learned" and page.document_id is not None
            try:  # PIP3 fair use
                out = await fetcher.get(
                    url,
                    etag=page.etag if conditional else None,
                    last_modified=page.last_modified if conditional else None,
                )
            except FetchBudgetExhausted:
                self.limited_by = "fetch_limit"
                break
            # /PIP3 fair use
            if out.status == 304:
                page.last_checked_at = self.deps.clock()
                page.check_count += 1
                page.missing_count = 0  # BIZ2: it answered
                page.dirty = True
                self.counts["unchanged"] += 1
                continue
            was_learned = page.status == "learned"
            if was_learned and await self._keep_or_forget(page, out):  # BIZ2 freshness
                continue
            await self.handle(url, page.depth, out, None)
            if page.status == "queued" and not was_learned:
                learned_now += 1
            await self.flush()
        await self.flush(force=True)
        return "ready"


def _port(origin: str) -> int | None:
    from urllib.parse import urlsplit

    return urlsplit(origin).port


def _empty_reason(start: PageState | None) -> str:
    if start is None:
        return "no_pages"
    if start.skip_reason in ("robots_disallowed", "noindex"):
        return "robots_disallowed"
    if start.skip_reason == "needs_browser":
        return "needs_browser"
    return "no_pages"


async def _execute(run: _Run, work: Callable[[WebsiteFetcher], Awaitable[str]]) -> LearnResult:
    refused = await run.start()
    if refused is not None:
        return refused
    fetcher = run.deps.fetcher(run.source)
    run.fetcher = fetcher
    try:
        try:
            status = await work(fetcher)
        except FetchBudgetExhausted:
            # PIP3 fair use: the robots file, a sitemap or a check ran into the day's limit; what
            # was read stays, and the resume job reads on after midnight UTC
            run.limited_by = "fetch_limit"
            learned_so_far = any(p.status == "learned" for p in run.pages.values())
            status = "ready" if learned_so_far else "failed"
            run.error_code = run.error_code or (None if learned_so_far else "fetch_limit")
            await run.flush(force=True)
        first_pass = await run.save(status=status, final=True)
    except _GoneError:
        return LearnResult("stopped", run_id=run.run_id, counts=run.result_counts(), reason="gone")
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Website learning failed for source %s", run.source_id)
        run.error_code = "unexpected"
        with contextlib.suppress(_GoneError):
            await run.save(status="failed", final=True)
        return LearnResult(
            "failed", run_id=run.run_id, counts=run.result_counts(), reason="unexpected"
        )
    finally:
        await fetcher.aclose()
        await run.settle_pages()  # PIP3 fair use: also for a run that failed half way
    if first_pass:
        notify = run.deps.notify
        learned = sum(1 for p in run.pages.values() if p.status == "learned")
        skipped = sum(1 for p in run.pages.values() if p.status in ("skipped", "excluded"))
        try:
            if notify is not None:
                await notify(run.source_id, learned, skipped)
            else:
                await notify_first_pass(
                    run.deps.session_factory, run.deps.settings, run.source_id, learned, skipped
                )
        except Exception:
            logger.warning("The first-pass e-mail failed", exc_info=True)
    outcome: Status = (
        "blocked" if status == "blocked" else "failed" if status == "failed" else "done"
    )
    return LearnResult(
        outcome, run_id=run.run_id, counts=run.result_counts(), reason=run.error_code
    )


async def learn_source(
    source_id: uuid.UUID, *, kind: str = "first", trigger: str = "manual", deps: LearnDeps
) -> LearnResult:
    """Read a whole site. `kind` is `first` (the first pass) or `full` (a re-read)."""
    run = _Run(deps, source_id, kind=kind, trigger=trigger, discovery=kind in DISCOVERY_KINDS)
    return await _execute(run, run.discover)


async def learn_urls(
    source_id: uuid.UUID,
    urls: Sequence[str],
    *,
    kind: str = "page",
    trigger: str = "manual",
    deps: LearnDeps,
) -> LearnResult:
    """Read just `urls` of a site: a page re-read now, a pushed URL, a widget hint."""
    run = _Run(deps, source_id, kind=kind, trigger=trigger, discovery=False)

    async def work(fetcher: WebsiteFetcher) -> str:
        return await run.read_urls(fetcher, list(urls))

    return await _execute(run, work)
