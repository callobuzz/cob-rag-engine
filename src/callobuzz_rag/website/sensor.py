"""The widget's freshness sensor, server side (BIZ2).

The widget on a visitor's page reports `{page_url, fingerprint}`: the page's address and a short
code of its visible main text (never the text). The browser's text and the server's text differ
(scripts, hidden parts, whitespace), so a widget code is only ever compared with the previous
**widget** code of the same page (D9). A change does not mean anything is learned from the
browser: it only asks the server to read that URL again, and the server's own read decides.

Everything is bounded, because the widget key is public and `Origin` can be forged by anything
that is not a browser: the report is ignored unless it is for a page of the request's own origin
that is a page the knowledge base's learned sites already know (never a new address), not left
out and without a query string;
one server read per page per `RAG_WEBSITE_SENSOR_MIN_INTERVAL_SECONDS`; at most 60 per site per
hour; and the caller is told nothing (always 204).
"""

import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from sqlalchemy import select

from callobuzz_rag.api.schemas.widget import WidgetSensorConfig
from callobuzz_rag.db.models.website import WebsitePage, WebsiteSettings, WebsiteSource
from callobuzz_rag.website import push
from callobuzz_rag.website.policy import website_policy
from callobuzz_rag.website.urls import DEFAULT_EXCLUDES, canonical, is_excluded, url_hash

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

PER_SOURCE_PER_HOUR: Final = 60
_PARKED: Final = ("paused", "blocked")
_NOT_READ: Final = ("skipped", "excluded")


async def _live_sources(session: "AsyncSession", corpus_id: Any) -> list[WebsiteSource]:
    rows = (
        (await session.execute(select(WebsiteSource).where(WebsiteSource.corpus_id == corpus_id)))
        .scalars()
        .all()
    )
    return [s for s in rows if s.status not in _PARKED]


def _origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


async def config_for(
    session: "AsyncSession", corpus: "Corpus", origin: str | None, settings: "Settings"
) -> WidgetSensorConfig | None:
    """The `sensor` block of `GET /widget/config`: only when learning is on for this knowledge
    base and the page asking is on one of its learned sites; otherwise the widget sends nothing."""
    if not origin or settings.website_sensor_sample <= 0:
        return None
    policy = await website_policy(session, corpus, settings)
    if not policy.enabled:
        return None
    sources = await _live_sources(session, corpus.id)
    if push._owner_of(origin.rstrip("/") + "/", sources) is None:
        return None
    row = (
        await session.execute(select(WebsiteSettings).where(WebsiteSettings.corpus_id == corpus.id))
    ).scalar_one_or_none()
    owner = list(row.exclude_patterns or []) if row is not None else []
    return WidgetSensorConfig(
        sample=settings.website_sensor_sample, skip=[*DEFAULT_EXCLUDES, *owner]
    )


async def _allow(redis: Any, source: WebsiteSource, url: str, settings: "Settings") -> bool:
    """One server read per page per interval, and at most 60 per site per hour."""
    fresh = await redis.set(
        f"sensor:p:{source.id}:{url_hash(url)}",
        "1",
        ex=max(1, settings.website_sensor_min_interval_seconds),
        nx=True,
    )
    if not fresh:
        return False
    slot = int(time.time() // 3600)
    name = f"sensor:h:{source.id}:{slot}"
    count = int(await redis.incr(name))
    await redis.expire(name, 7200, nx=True)
    return count <= PER_SOURCE_PER_HOUR


async def report(
    session: "AsyncSession",
    redis: Any,
    settings: "Settings",
    corpus: "Corpus",
    *,
    origin: str | None,
    page_url: str,
    fingerprint: str,
    now: datetime,
) -> str:
    """Handle one report. Returns what happened (`off`, `ignored`, `stored`, `unchanged`,
    `queued`, `hinted`, `over_cap`, `throttled`): for tests and logs, never for the caller."""
    policy = await website_policy(session, corpus, settings)
    if not policy.enabled:
        return "off"
    if not origin or "?" in page_url:
        return "ignored"
    url = canonical(page_url)
    if url is None or _origin_of(url) != origin.rstrip("/").lower():
        return "ignored"
    source = push._owner_of(url, await _live_sources(session, corpus.id))
    if source is None:
        return "ignored"
    row = (
        await session.execute(select(WebsiteSettings).where(WebsiteSettings.corpus_id == corpus.id))
    ).scalar_one_or_none()
    if is_excluded(url, list(row.exclude_patterns or []) if row is not None else []) is not None:
        return "ignored"
    page = (
        await session.execute(
            select(WebsitePage).where(
                WebsitePage.source_id == source.id, WebsitePage.url_hash == url_hash(url)
            )
        )
    ).scalar_one_or_none()
    if page is not None and (page.excluded_by_owner or page.status in _NOT_READ):
        return "ignored"
    if page is None:
        # Never a new address: a forged report (the widget key is public) could otherwise add
        # pages to the site one made-up path at a time. New pages come from the sitemap, the
        # links and the push link.
        return "ignored"
    if page.status == "learned":
        previous = page.sensor_fingerprint
        if previous is None:
            page.sensor_fingerprint, page.sensor_seen_at = fingerprint, now
            await session.commit()
            return "stored"
        if previous == fingerprint:
            page.sensor_seen_at = now
            await session.commit()
            return "unchanged"
        outcome = "queued"
    else:  # a page the site knows but nobody has learned (removed, failed): worth a look
        if policy.pages_cap <= 0:
            return "over_cap"
        outcome = "hinted"
    if not await _allow(redis, source, url, settings):
        # Throttled: the change signal is kept (the stored code stays the old one), so the next
        # report that is let through still differs and queues the read.
        page.sensor_seen_at = now
        await session.commit()
        return "throttled"
    if not push.dispatch_urls(source.id, [url], "sensor", "sensor"):
        page.sensor_seen_at = now
        await session.commit()
        return "throttled"
    page.sensor_fingerprint, page.sensor_seen_at = fingerprint, now
    await session.commit()
    return outcome
