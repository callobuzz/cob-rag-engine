"""Reads and writes of the landing tables (LP1). Nothing here commits: the caller does.

The site is one row (`id = 1`) made on first use with the shipped default content, so changing
the default later never needs a data migration.
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from callobuzz_rag.db.models.landing import LandingAsset, LandingSite, LandingVersion
from callobuzz_rag.landing.schema import LandingContent, default_content
from callobuzz_rag.landing.theme import ThemeTokens, derive_theme

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SITE_ID: Final = 1
VERSIONS_SHOWN: Final = 50


def theme_of(content: LandingContent) -> ThemeTokens:
    return derive_theme(content.theme.brand_color, content.theme.accent_color)


async def ensure_site(session: "AsyncSession") -> LandingSite:
    """The one site row, created with the default content the first time it is needed."""
    site = await _select_site(session, lock=False)
    if site is not None:
        return site
    content = default_content()
    await session.execute(
        insert(LandingSite)
        .values(
            id=SITE_ID,
            enabled=False,
            draft=content.model_dump(mode="json"),
            draft_revision=1,
            draft_theme=theme_of(content).to_json(),
            published_version=None,
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )
    site = await _select_site(session, lock=False)
    assert site is not None
    return site


async def locked_site(session: "AsyncSession") -> LandingSite:
    """The site row, locked for the rest of the transaction (created first if missing)."""
    await ensure_site(session)
    site = await _select_site(session, lock=True)
    assert site is not None
    return site


async def _select_site(session: "AsyncSession", *, lock: bool) -> LandingSite | None:
    stmt = select(LandingSite).where(LandingSite.id == SITE_ID)
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def all_versions(session: "AsyncSession") -> list[LandingVersion]:
    """Every published version, newest first."""
    rows = list((await session.execute(select(LandingVersion))).scalars().all())
    return sorted(rows, key=lambda r: -r.version)


async def get_version(session: "AsyncSession", version: int) -> LandingVersion | None:
    return (
        await session.execute(select(LandingVersion).where(LandingVersion.version == version))
    ).scalar_one_or_none()


async def list_assets(session: "AsyncSession") -> list[LandingAsset]:
    """The uploaded logos, posters and videos (not the generated Open Graph images)."""
    rows = list((await session.execute(select(LandingAsset))).scalars().all())
    return sorted((r for r in rows if r.kind != "og"), key=lambda r: r.created_at)


def content_hash(content: dict[str, Any], theme: dict[str, Any]) -> str:
    """A stable fingerprint of what a visitor sees (the ETag and the version's identity)."""
    blob = json.dumps({"content": content, "theme": theme}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_EXTENSIONS: Final = {
    "image/png": "png",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
}


def media_name(asset: LandingAsset) -> str:
    """`<id>.<ext>`: the file name a published asset is served under."""
    return f"{asset.id}.{_EXTENSIONS[asset.content_type]}"


def asset_ids_in(content: Any) -> set[str]:
    """Every asset id a content document names (logo, video and poster)."""
    ids: set[str] = set()
    theme = content.get("theme", {}) if isinstance(content, dict) else {}
    if theme.get("logo_asset_id"):
        ids.add(str(theme["logo_asset_id"]))
    for section in content.get("home", []) if isinstance(content, dict) else []:
        if isinstance(section, dict) and section.get("type") == "video":
            ids |= {
                str(section[k]) for k in ("video_asset_id", "poster_asset_id") if section.get(k)
            }
    return ids
