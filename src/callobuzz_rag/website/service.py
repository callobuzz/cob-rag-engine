"""What the owner's routes and the widget-key triggers do with website learning (BIZ1).

A site is learned only when its origin is an allowed site of an ACTIVE widget key of the
knowledge base (never the request's Origin header, never `*`, an IP or `localhost`). One source
per knowledge base and site scope; the apex and `www` twin are one site. Everything here is
plain SQL and plain Python on one session, so it runs the same against PostgreSQL and the
in-memory test database; the caller commits unless a function says it did.
"""

import logging
import math
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, cast
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.website import (
    WebsitePage,
    WebsiteRun,
    WebsiteSettings,
    WebsiteSource,
)
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.website import archive
from callobuzz_rag.website.freshness import RefreshMode, first_check  # BIZ2 freshness
from callobuzz_rag.website.netguard import BlockedURLError, check_url
from callobuzz_rag.website.policy import WebsitePolicy, website_policy
from callobuzz_rag.website.urls import (
    DEFAULT_EXCLUDES,
    UrlError,
    is_excluded,
    origin_of,
    site_scope,
)

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

STALE_AFTER: Final = timedelta(minutes=15)
REREAD_COOLDOWN: Final = timedelta(minutes=10)
PAGE_COOLDOWN: Final = timedelta(seconds=60)
QUEUE_DOWN: Final = "The task queue is unavailable; nothing was started. Retry later."
DISCOVERY_KINDS: Final = ("first", "full")


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ allowed sites


def _preference(origin: str) -> tuple[bool, bool, str]:
    parts = urlsplit(origin)
    return (parts.scheme != "https", (parts.hostname or "").startswith("www."), origin)


def learnable(origins: Iterable[str]) -> dict[str, str]:
    """site scope -> the origin to start from, for every origin that is a real website
    (https before http, the bare host before `www`; a subdomain of another allowed site is
    covered by that site)."""
    out: dict[str, str] = {}
    cleaned: list[str] = []
    for raw in origins:
        try:
            cleaned.append(origin_of(raw))
        except UrlError:
            continue
    for origin in sorted(set(cleaned), key=_preference):
        try:
            out.setdefault(site_scope(origin), origin)
        except UrlError:
            continue
    # A subdomain of another allowed site is already inside that site's scope: one source
    # covers both (the scope is the host plus its subdomains).
    return {
        scope: origin
        for scope, origin in out.items()
        if not any(scope.endswith("." + other) for other in out if other != scope)
    }


async def allowed_sites(session: "AsyncSession", corpus_id: uuid.UUID) -> dict[str, str]:
    """The learnable sites among the allowed origins of the knowledge base's active keys."""
    keys = (
        (
            await session.execute(
                select(WidgetKey).where(
                    WidgetKey.corpus_id == corpus_id, WidgetKey.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    sites = learnable(o for key in keys for o in (key.allowed_origins or []))
    # LP1 landing: never learn the engine's own address (the website and the dashboard)
    own = await own_scopes(session)
    return {scope: origin for scope, origin in sites.items() if scope not in own}
    # /LP1 landing


# LP1 landing
async def own_scopes(session: "AsyncSession") -> set[str]:
    """Site scopes of the engine's own addresses: `RAG_PUBLIC_URL` and the public website's
    address. Connecting "Ask it yourself" adds the website's origin to a widget key; without this
    the engine would read its own pages into the knowledge base."""
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.models.landing import LandingSite

    addresses = [get_settings().public_url]
    site = await session.get(LandingSite, 1)
    if site is not None and isinstance(site.address, str):
        addresses.append(site.address)
    scopes: set[str] = set()
    for address in addresses:
        try:
            scopes.add(site_scope(origin_of(address)))
        except UrlError:
            continue
    return scopes


# /LP1 landing


async def get_settings_row(session: "AsyncSession", corpus: Corpus) -> WebsiteSettings | None:
    return (
        await session.execute(select(WebsiteSettings).where(WebsiteSettings.corpus_id == corpus.id))
    ).scalar_one_or_none()


async def ensure_settings(session: "AsyncSession", corpus: Corpus) -> WebsiteSettings:
    """The knowledge base's website settings, created with the defaults on first use."""
    row = await get_settings_row(session, corpus)
    if row is None:
        row = WebsiteSettings(corpus_id=corpus.id, tenant_id=corpus.tenant_id)
        session.add(row)
        await session.flush()
    return row


async def sources_of(session: "AsyncSession", corpus_id: uuid.UUID) -> list[WebsiteSource]:
    rows = (
        (await session.execute(select(WebsiteSource).where(WebsiteSource.corpus_id == corpus_id)))
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda s: (s.created_at or _now(), s.scope))


async def get_source(
    session: "AsyncSession", corpus_id: uuid.UUID, source_id: uuid.UUID
) -> WebsiteSource:
    source = await session.get(WebsiteSource, source_id)
    if source is None or source.corpus_id != corpus_id:
        raise APIError(
            404, "That website is not one of this knowledge base's.", "website_source_not_found"
        )
    return source


# ------------------------------------------------------------------ running


async def has_live_discovery_run(
    session: "AsyncSession", source_id: uuid.UUID, now: datetime | None = None
) -> bool:
    moment = now or _now()
    runs = (
        (
            await session.execute(
                select(WebsiteRun).where(
                    WebsiteRun.source_id == source_id, WebsiteRun.finished_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    return any(r.kind in DISCOVERY_KINDS and r.heartbeat_at >= moment - STALE_AFTER for r in runs)


def dispatch_learn(source_id: uuid.UUID, kind: str, trigger: str) -> bool:
    """Queue a site read. False when the task queue refused it (logged)."""
    try:
        from callobuzz_rag.worker.website_tasks import learn_site_task

        learn_site_task.delay(str(source_id), kind, trigger)
    except Exception:
        logger.error("Could not queue the website read of %s", source_id, exc_info=True)
        return False
    return True


def dispatch_urls(source_id: uuid.UUID, urls: Sequence[str], trigger: str = "manual") -> bool:
    try:
        from callobuzz_rag.worker.website_tasks import learn_urls_task

        learn_urls_task.delay(str(source_id), list(urls), "page", trigger)
    except Exception:
        logger.error("Could not queue the page read of %s", source_id, exc_info=True)
        return False
    return True


async def policy_of(
    session: "AsyncSession", corpus: Corpus, settings: Settings, source_id: uuid.UUID | None = None
) -> WebsitePolicy:
    return await website_policy(session, corpus, settings, exclude_source_id=source_id)


def refuse_disabled(policy: WebsitePolicy) -> None:
    if not policy.enabled:
        raise APIError(
            409,
            "Learning from your website is switched off for this knowledge base or plan.",
            "website_learning_disabled",
            reason=policy.reason,
        )


async def kick(
    session: "AsyncSession",
    corpus: Corpus,
    source: WebsiteSource,
    *,
    trigger: str,
    request: "Request | None",
) -> bool:
    """Start reading `source` unless a read is already running. Commits. True when queued."""
    if await has_live_discovery_run(session, source.id):
        return False
    kind = "first" if source.first_pass_done_at is None else "full"
    source.status, source.pause_reason, source.last_error_code = "discovering", None, None
    await record_audit(
        session,
        request,
        "website.learning_started",
        tenant_id=corpus.tenant_id,
        target_type="website_source",
        target_id=str(source.id),
        details={
            "corpus_id": str(corpus.id),
            "source_id": str(source.id),
            "origin": source.origin,
            "trigger": trigger,
        },
    )
    await session.commit()
    if dispatch_learn(source.id, kind, trigger):
        return True
    source.status = "idle"
    await session.commit()
    return False


async def start_learning(
    session: "AsyncSession",
    corpus: Corpus,
    origin: str,
    *,
    settings: Settings,
    request: "Request | None",
    trigger: str = "manual",  # BIZ4: "widget" for the first load from an allowed site
) -> WebsiteSource:
    """`POST .../website/learn`: validate `origin`, create or find its source, start a read.

    Raises:
        APIError: `website_url_invalid`, `website_not_allowed`, `website_blocked`,
            `website_learning_disabled`; HTTPException 503 when the task queue is down.
    """
    from fastapi import HTTPException

    try:
        typed = origin_of(origin)
        scope = site_scope(typed)
    except UrlError as exc:
        raise APIError(
            422, "Enter your website like shreedental.in", "website_url_invalid"
        ) from exc
    sites = await allowed_sites(session, corpus.id)
    if scope not in sites:
        raise APIError(
            422,
            "Add this site to a widget key of this knowledge base first, so we only read "
            "websites you own.",
            "website_not_allowed",
        )
    try:
        await check_url(sites[scope], settings.website_allow_private_networks)
    except BlockedURLError as exc:
        raise APIError(422, "We cannot reach that address from here.", "website_blocked") from exc
    refuse_disabled(await policy_of(session, corpus, settings))
    source = await _source_for(session, corpus, scope, sites[scope])
    queued = await kick(session, corpus, source, trigger=trigger, request=request)
    if not queued and not await has_live_discovery_run(session, source.id):
        raise HTTPException(status_code=503, detail=QUEUE_DOWN)
    return source


async def _source_for(
    session: "AsyncSession", corpus: Corpus, scope: str, origin: str
) -> WebsiteSource:
    rows = (
        (
            await session.execute(
                select(WebsiteSource).where(
                    WebsiteSource.corpus_id == corpus.id, WebsiteSource.scope == scope
                )
            )
        )
        .scalars()
        .all()
    )
    if rows:
        return rows[0]
    await ensure_settings(session, corpus)
    source = WebsiteSource(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        origin=origin,
        scope=scope,
        status="idle",
    )
    session.add(source)
    try:
        await session.flush()
    except IntegrityError:  # another request made it first
        await session.rollback()
        return await _source_for(session, corpus, scope, origin)
    return source


async def on_origins_changed(
    session: "AsyncSession",
    corpus: Corpus,
    *,
    request: "Request | None" = None,
    settings: Settings | None = None,
    added: Iterable[str] | None = None,
    removed: Iterable[str] | None = None,
) -> None:
    """A widget key was created, changed or deleted: bring the sites in line with the allowed
    origins. A new allowed site starts learning, a site no key allows any more is paused
    (`origin_removed`, its pages stay), a site that is allowed again resumes.

    `added` / `removed` are hints for the caller's log only: the truth is recomputed from the
    active keys. Never raises for a learning problem: a widget-key save must not fail because
    of it (the widget_keys hook also wraps this in a try).
    """
    del added, removed
    from callobuzz_rag.core.config import get_settings

    cfg = settings or get_settings()
    allowed = await allowed_sites(session, corpus.id)
    existing = {s.scope: s for s in await sources_of(session, corpus.id)}
    to_start: list[WebsiteSource] = []

    for scope, source in existing.items():
        if scope not in allowed:
            if not (source.status == "paused" and source.pause_reason == "origin_removed"):
                source.status, source.pause_reason = "paused", "origin_removed"
        elif source.status == "paused" and source.pause_reason == "origin_removed":
            source.status, source.pause_reason = "idle", None
            source.origin = allowed[scope]
            to_start.append(source)
    new_scopes = [s for s in allowed if s not in existing]
    if new_scopes or to_start:
        policy = await policy_of(session, corpus, cfg)
        if policy.enabled:
            for scope in new_scopes:
                to_start.append(await _source_for(session, corpus, scope, allowed[scope]))
    await session.commit()
    for source in to_start:
        await kick(session, corpus, source, trigger="origin_added", request=request)


# ------------------------------------------------------------------ owner actions


async def reread_all(
    session: "AsyncSession", corpus: Corpus, *, settings: Settings, request: "Request | None"
) -> int:
    """`POST .../website/reread`: read every site again (once per 10 minutes per knowledge
    base). Returns how many reads were queued."""
    refuse_disabled(await policy_of(session, corpus, settings))
    row = await ensure_settings(session, corpus)
    now = _now()
    if row.last_reread_at is not None and now - row.last_reread_at < REREAD_COOLDOWN:
        wait = max(1, math.ceil((row.last_reread_at + REREAD_COOLDOWN - now).total_seconds()))
        raise APIError(
            429,
            f"You can read your website again in {math.ceil(wait / 60)} minutes.",
            "website_reread_too_soon",
            headers={"Retry-After": str(wait)},
            retry_after=wait,
        )
    row.last_reread_at = now
    await record_audit(
        session,
        request,
        "website.reread_requested",
        tenant_id=corpus.tenant_id,
        target_type="corpus",
        target_id=str(corpus.id),
        details={"corpus_id": str(corpus.id), "scope": "all"},
    )
    await session.commit()
    queued = 0
    for source in await sources_of(session, corpus.id):
        if source.status == "paused" and source.pause_reason == "origin_removed":
            continue
        if await has_live_discovery_run(session, source.id):
            continue
        source.status = "discovering"
        await session.commit()
        if dispatch_learn(source.id, "full", "manual"):
            queued += 1
        else:
            source.status = "idle"
            await session.commit()
    return queued


async def remove_source(
    session: "AsyncSession",
    corpus: Corpus,
    source: WebsiteSource,
    *,
    vector_store: Any,
    request: "Request | None",
) -> int:
    """`DELETE .../website/sources/{id}`: archive the site's pages (they keep their history)
    and delete the site. Returns how many versions were archived."""
    pages = await _pages_of_source(session, source.id)
    archived = await archive.archive_pages(session, vector_store, corpus, pages)
    await record_audit(
        session,
        request,
        "website.source_removed",
        tenant_id=corpus.tenant_id,
        target_type="website_source",
        target_id=str(source.id),
        details={
            "corpus_id": str(corpus.id),
            "source_id": str(source.id),
            "origin": source.origin,
            "pages_archived": archived,
        },
    )
    await session.delete(source)
    await session.commit()
    return archived


async def _pages_of_source(session: "AsyncSession", source_id: uuid.UUID) -> list[WebsitePage]:
    return list(
        (await session.execute(select(WebsitePage).where(WebsitePage.source_id == source_id)))
        .scalars()
        .all()
    )


async def _pages_of_corpus(session: "AsyncSession", corpus_id: uuid.UUID) -> list[WebsitePage]:
    return list(
        (await session.execute(select(WebsitePage).where(WebsitePage.corpus_id == corpus_id)))
        .scalars()
        .all()
    )


async def update_settings(
    session: "AsyncSession",
    corpus: Corpus,
    *,
    enabled: bool | None,
    exclude_patterns: list[str] | None,
    vector_store: Any,
    request: "Request | None",
    refresh_mode: str | None = None,  # BIZ2 freshness
    adaptive: bool | None = None,  # BIZ2 freshness
) -> list[str]:
    """`PATCH .../website`. Pages that match a new exclusion are archived now; pages an
    exclusion no longer matches are queued to be read again. Returns the changed fields."""
    row = await ensure_settings(session, corpus)
    changed: list[str] = []
    requeue: dict[uuid.UUID, list[str]] = {}
    if enabled is not None and enabled != row.enabled:
        row.enabled = enabled
        changed.append("enabled")
        for source in await sources_of(session, corpus.id):
            if not enabled and source.status != "paused":
                source.status, source.pause_reason = "paused", "disabled"
            elif enabled and source.pause_reason == "disabled":
                source.status, source.pause_reason = "idle", None
    if exclude_patterns is not None and exclude_patterns != list(row.exclude_patterns or []):
        row.exclude_patterns = exclude_patterns
        changed.append("exclude_patterns")
        to_archive: list[WebsitePage] = []
        for page in await _pages_of_corpus(session, corpus.id):
            hit = is_excluded(page.url, exclude_patterns)
            owner_hit = hit is not None and hit not in DEFAULT_EXCLUDES
            if owner_hit and page.status not in ("excluded", "removed"):
                if page.document_id is not None and page.status == "learned":
                    to_archive.append(page)
                page.status, page.skip_reason = "excluded", "pattern"
            elif not owner_hit and page.status == "excluded" and page.skip_reason == "pattern":
                page.status, page.skip_reason = "discovered", None
                requeue.setdefault(page.source_id, []).append(page.url)
        await archive.archive_pages(session, vector_store, corpus, to_archive)
    # BIZ2 freshness: a new schedule applies to every learned page at once
    if refresh_mode is not None and refresh_mode != row.refresh_mode:
        row.refresh_mode = refresh_mode
        changed.append("refresh_mode")
    if adaptive is not None and adaptive != row.adaptive:
        row.adaptive = adaptive
        changed.append("adaptive")
    # Turning learning back on restores the checks that were dropped while it was off.
    if "refresh_mode" in changed or "adaptive" in changed or ("enabled" in changed and row.enabled):
        await reschedule_pages(session, corpus, row)
    # /BIZ2 freshness
    if changed:
        await record_audit(
            session,
            request,
            "website.settings_updated",
            tenant_id=corpus.tenant_id,
            target_type="corpus",
            target_id=str(corpus.id),
            details={"corpus_id": str(corpus.id), "changed": changed},
        )
    await session.commit()
    for source_id, urls in requeue.items():
        dispatch_urls(source_id, urls[:200])
    return changed


# BIZ2 freshness
async def reschedule_pages(
    session: "AsyncSession", corpus: Corpus, row: WebsiteSettings, now: datetime | None = None
) -> int:
    """Give every learned page of the knowledge base the interval and next check time of its
    (new) schedule. `off` stops the checks (`next_check_at` NULL). Returns the pages changed."""
    moment = now or _now()
    mode = cast("RefreshMode", row.refresh_mode)
    pages = [
        p
        for p in await _pages_of_corpus(session, corpus.id)
        if p.status == "learned" and not p.excluded_by_owner
    ]
    for page in pages:
        page.interval_seconds, page.next_check_at = first_check(
            mode, row.adaptive, moment, page.url_hash
        )
    return len(pages)


# /BIZ2 freshness


# ------------------------------------------------------------------ pages


async def get_page(
    session: "AsyncSession", corpus_id: uuid.UUID, page_id: uuid.UUID
) -> WebsitePage:
    page = await session.get(WebsitePage, page_id)
    if page is None or page.corpus_id != corpus_id:
        raise APIError(
            404, "That page is not one of this knowledge base's.", "website_page_not_found"
        )
    return page


async def list_pages(
    session: "AsyncSession",
    corpus_id: uuid.UUID,
    *,
    status: str | None,
    source_id: uuid.UUID | None,
    q: str | None,
    offset: int,
    limit: int,
) -> tuple[list[WebsitePage], int, dict[str, int], dict[str, int]]:
    """(page of rows, total matching, rows per status, left-out pages per reason).

    The last two cover the whole knowledge base, not the filter or the page."""
    base = [WebsitePage.corpus_id == corpus_id]
    if source_id is not None:
        base.append(WebsitePage.source_id == source_id)
    filters = list(base)
    if status == "waiting":  # the dashboard's "Waiting" chip: found or queued, not read yet
        filters.append(WebsitePage.status.in_(("discovered", "queued")))
    elif status:
        filters.append(WebsitePage.status == status)
    if q:
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        filters.append(or_(WebsitePage.url.ilike(like), WebsitePage.title.ilike(like)))
    total = (
        await session.execute(select(func.count()).select_from(WebsitePage).where(*filters))
    ).scalar() or 0
    rows = (
        (
            await session.execute(
                select(WebsitePage)
                .where(*filters)
                .order_by(WebsitePage.rank_score.desc(), WebsitePage.url)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    counted = await session.execute(
        select(WebsitePage.status, func.count()).where(*base).group_by(WebsitePage.status)
    )
    counts = {str(s): int(n) for s, n in counted.all()}
    left_out = await session.execute(
        select(WebsitePage.skip_reason, func.count())
        .where(*base, WebsitePage.status.in_(("skipped", "excluded")))
        .group_by(WebsitePage.skip_reason)
    )
    reasons = {str(r or "other"): int(n) for r, n in left_out.all()}
    return list(rows), int(total), counts, reasons


async def set_page_excluded(
    session: "AsyncSession",
    corpus: Corpus,
    page: WebsitePage,
    excluded: bool,
    *,
    vector_store: Any,
    settings: Settings,
    request: "Request | None",
) -> bool:
    """`PATCH .../pages/{id}`. Excluding archives the page now; including queues it. Commits.
    Returns whether a read was queued."""
    if not excluded:
        await _guard_page_read(session, corpus, page, settings)
    if excluded:
        if page.document_id is not None and page.status == "learned":
            await archive.archive_pages(session, vector_store, corpus, [page])
        page.excluded_by_owner = True
        page.status, page.skip_reason = "excluded", "owner"
        action = "website.page_excluded"
    else:
        page.excluded_by_owner = False
        page.status, page.skip_reason = "queued", None
        action = "website.page_included"
    await record_audit(
        session,
        request,
        action,
        tenant_id=corpus.tenant_id,
        target_type="website_page",
        target_id=str(page.id),
        details={"corpus_id": str(corpus.id), "page_id": str(page.id), "url": page.url},
    )
    await session.commit()
    if excluded:
        return False
    return dispatch_urls(page.source_id, [page.url])


async def _guard_page_read(
    session: "AsyncSession", corpus: Corpus, page: WebsitePage, settings: Settings
) -> None:
    """A page read fetches the owner's site, so it needs learning switched on, a site that is
    not paused, and at least a minute since the page was last read."""
    refuse_disabled(await policy_of(session, corpus, settings))
    source = await session.get(WebsiteSource, page.source_id)
    if source is not None and source.status == "paused":
        raise APIError(
            409,
            "Learning from this website is paused.",
            "website_source_paused",
            reason=source.pause_reason,
        )
    now = _now()
    if page.last_checked_at is not None and now - page.last_checked_at < PAGE_COOLDOWN:
        wait = max(1, math.ceil((page.last_checked_at + PAGE_COOLDOWN - now).total_seconds()))
        raise APIError(
            429,
            f"You can read this page again in {wait} seconds.",
            "website_reread_too_soon",
            headers={"Retry-After": str(wait)},
            retry_after=wait,
        )


async def reread_page(
    session: "AsyncSession",
    corpus: Corpus,
    page: WebsitePage,
    *,
    settings: Settings,
    request: "Request | None",
) -> bool:
    await _guard_page_read(session, corpus, page, settings)
    await record_audit(
        session,
        request,
        "website.reread_requested",
        tenant_id=corpus.tenant_id,
        target_type="website_page",
        target_id=str(page.id),
        details={"corpus_id": str(corpus.id), "scope": "page", "page_id": str(page.id)},
    )
    await session.commit()
    return dispatch_urls(page.source_id, [page.url])
