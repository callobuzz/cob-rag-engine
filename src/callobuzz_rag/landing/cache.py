"""The published site, held per process (LP1).

A visitor request must not cost a database round trip per page. The cache re-checks the one
`landing_sites` row at most every `ttl` seconds, and loads a version's content only when the
published version number changed, so a publish shows on every worker within seconds. The
publishing process calls `invalidate()` and sees it at once.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from sqlalchemy import select

from callobuzz_rag.db.models.landing import LandingSite, LandingVersion
from callobuzz_rag.landing.schema import LandingContent
from callobuzz_rag.landing.store import SITE_ID, asset_ids_in
from callobuzz_rag.landing.theme import ThemeTokens

TTL_S: Final = 10.0


@dataclass(frozen=True)
class PublishedSite:
    """What the public pages render from: a frozen version plus the live site settings."""

    version: int
    content: LandingContent
    theme: ThemeTokens
    theme_css: str
    content_hash: str
    published_at: datetime
    address: str | None  # the website's own address setting (not the effective one)
    ask_widget_key_id: UUID | None
    asset_ids: frozenset[str] = frozenset()  # uploads this version uses (the media route's list)


@dataclass(frozen=True)
class _SiteRow:
    enabled: bool
    published_version: int | None
    address: str | None
    ask_widget_key_id: UUID | None


class PublishedCache:
    def __init__(self, ttl: float = TTL_S) -> None:
        self._ttl = ttl
        self._checked_at: float | None = None
        self._row: _SiteRow | None = None
        self._versions: dict[int, PublishedSite] = {}
        self._lock: asyncio.Lock | None = None

    def invalidate(self) -> None:
        self._checked_at = None
        self._versions.clear()

    async def get(self, session_factory: Callable[[], Any], now: float) -> PublishedSite | None:
        """The live site, or None while it is off (or nothing is published)."""
        if self._checked_at is None or now - self._checked_at >= self._ttl:
            await self._refresh(session_factory, now)
        row = self._row
        if row is None or not row.enabled or row.published_version is None:
            return None
        cached = self._versions.get(row.published_version)
        if cached is None:
            return None
        return PublishedSite(
            version=cached.version,
            content=cached.content,
            theme=cached.theme,
            theme_css=cached.theme_css,
            content_hash=cached.content_hash,
            published_at=cached.published_at,
            address=row.address,
            ask_widget_key_id=row.ask_widget_key_id,
            asset_ids=cached.asset_ids,
        )

    async def _refresh(self, session_factory: Callable[[], Any], now: float) -> None:
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._checked_at is not None and now - self._checked_at < self._ttl:
                return  # another request refreshed while this one waited
            async with session_factory() as session:
                site = (
                    await session.execute(select(LandingSite).where(LandingSite.id == SITE_ID))
                ).scalar_one_or_none()
                if site is None:
                    self._row = None
                else:
                    self._row = _SiteRow(
                        enabled=bool(site.enabled),
                        published_version=site.published_version,
                        address=site.address,
                        ask_widget_key_id=site.ask_widget_key_id,
                    )
                    number = site.published_version
                    if site.enabled and number is not None and number not in self._versions:
                        version = (
                            await session.execute(
                                select(LandingVersion).where(LandingVersion.version == number)
                            )
                        ).scalar_one_or_none()
                        if version is not None:
                            self._versions = {number: _frozen(version)}
            self._checked_at = now


def _frozen(version: LandingVersion) -> PublishedSite:
    return PublishedSite(
        version=version.version,
        content=LandingContent.model_validate(version.content),
        theme=ThemeTokens.from_json(version.theme),
        theme_css=version.theme_css,
        content_hash=version.content_hash,
        published_at=version.published_at,
        address=None,
        ask_widget_key_id=None,
        asset_ids=frozenset(asset_ids_in(version.content)),
    )


PUBLISHED = PublishedCache()


def invalidate_all() -> None:
    """After a publish or a settings change in this process: forget the site and what the pages
    read around it, so the change shows at once here (other workers follow within seconds)."""
    from callobuzz_rag.landing.environment import ENVIRONMENT

    PUBLISHED.invalidate()
    ENVIRONMENT.invalidate()


async def landing_is_on(session_factory: Callable[[], Any]) -> bool:
    """Whether the public website is live right now. Never raises: unreadable means off."""
    import logging
    import time

    try:
        return await PUBLISHED.get(session_factory, time.monotonic()) is not None
    except Exception:
        logging.getLogger(__name__).warning("landing state unreadable", exc_info=True)
        return False
