"""The website's draft, publish, restore and settings rules (LP1). Nothing here commits.

One draft with a revision counter (a stale save is a 409), frozen published versions, and the
switch that puts the site online. Audit rows are written by the routes, which hold the request.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Literal
from urllib.parse import urlsplit

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.landing import LandingAsset, LandingSite, LandingVersion
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.landing import store
from callobuzz_rag.landing.schema import (
    LandingContent,
    VideoSection,
    translation_gaps,
)
from callobuzz_rag.landing.theme import theme_css

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

LANGUAGE_NAMES: Final = {"en": "English", "hi": "Hindi", "hi-Latn": "Hinglish"}
AskStatus = Literal["ready", "not_connected", "key_inactive", "origin_missing", "kb_missing"]

_HOST = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_PROD_ADDRESS = re.compile(rf"^https://{_HOST}(?:\.{_HOST})+(?::\d{{1,5}})?$")
_DEV_ADDRESS = re.compile(r"^http://(?:127\.0\.0\.1|localhost)(?::\d{1,5})?$")


def conflict() -> APIError:
    return APIError(
        409,
        "Someone saved this website in another tab. Reload to see their changes.",
        "landing_revision_conflict",
    )


# ------------------------------------------------------------------ the address


def normalise_address(value: str, *, prod: bool) -> str:
    """`https://host[:port]` with no path. Plain http only for this machine, and not in prod."""
    clean = value.strip().rstrip("/").lower()
    ok = bool(_PROD_ADDRESS.match(clean)) or (not prod and bool(_DEV_ADDRESS.match(clean)))
    if not ok:
        raise APIError(
            422,
            "Use your website's address like https://www.example.com, with no page after it.",
            "landing_address_invalid",
        )
    return clean


def effective_address(site_address: str | None, public_url: str) -> str | None:
    """The address canonical links, the sitemap and share images are built from: the one set for
    the website, else `RAG_PUBLIC_URL`. Never the address a request arrived on."""
    if site_address:
        return site_address
    parts = urlsplit(public_url.strip())
    if parts.scheme in ("http", "https") and parts.netloc:
        return f"{parts.scheme}://{parts.netloc}".lower()
    return None


def origin_of(address: str | None) -> str | None:
    if not address:
        return None
    parts = urlsplit(address)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.netloc else None


# ------------------------------------------------------------------ draft


async def save_draft(
    session: "AsyncSession",
    *,
    base_revision: int,
    content: LandingContent,
    updated_by: str,
) -> LandingSite:
    site = await store.locked_site(session)
    if site.draft_revision != base_revision:
        raise conflict()
    site.draft = content.model_dump(mode="json")
    site.draft_theme = store.theme_of(content).to_json()
    site.draft_revision = site.draft_revision + 1
    site.updated_at = datetime.now(UTC)
    site.updated_by = updated_by[:320]
    await session.flush()
    return site


def _asset_ids(content: LandingContent) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    if content.theme.logo_asset_id is not None:
        ids.add(content.theme.logo_asset_id)
    for section in content.home:
        if isinstance(section, VideoSection) and section.enabled:
            ids |= {section.video_asset_id, section.poster_asset_id}
    return ids


async def missing_assets(session: "AsyncSession", content: LandingContent) -> list[uuid.UUID]:
    wanted = _asset_ids(content)
    if not wanted:
        return []
    found = {
        a.id
        for a in (await session.execute(select(LandingAsset).where(LandingAsset.id.in_(wanted))))
        .scalars()
        .all()
    }
    return sorted(wanted - found, key=str)


async def publish(
    session: "AsyncSession",
    *,
    base_revision: int,
    change_note: str | None,
    published_by: str,
) -> LandingVersion:
    """Freeze the draft as the next version. The site is switched on separately."""
    site = await store.locked_site(session)
    if site.draft_revision != base_revision:
        raise conflict()
    content = LandingContent.model_validate(site.draft)
    for lang in content.languages:
        gaps = translation_gaps(content, lang)
        if gaps:
            raise APIError(
                409,
                f"Some texts are missing in {LANGUAGE_NAMES[lang]}. "
                "Fill them in or switch that language off.",
                "landing_translation_missing",
                paths=gaps[:50],
                language=lang,
            )
    if await missing_assets(session, content):
        raise APIError(
            409,
            "A picture or video this page uses was deleted. Choose another one.",
            "landing_asset_missing",
        )
    tokens = store.theme_of(content)
    versions = await store.all_versions(session)
    number = max((v.version for v in versions), default=0) + 1
    theme_json = tokens.to_json()
    content_json = content.model_dump(mode="json")
    now = datetime.now(UTC)
    row = LandingVersion(
        version=number,
        content=content_json,
        theme=theme_json,
        theme_css=theme_css(tokens, content.theme.base),
        content_hash=store.content_hash(content_json, theme_json),
        change_note=(change_note or "").strip()[:300] or None,
        published_at=now,
        published_by=published_by[:320],
    )
    session.add(row)
    site.published_version = number
    site.updated_at = now
    site.updated_by = published_by[:320]
    await session.flush()
    return row


async def restore(
    session: "AsyncSession", *, version: int, base_revision: int, updated_by: str
) -> LandingSite:
    """Copy a published version into the draft (publishing it again is a separate step)."""
    site = await store.locked_site(session)
    if site.draft_revision != base_revision:
        raise conflict()
    row = await store.get_version(session, version)
    if row is None:
        raise APIError(404, "That version does not exist.", "landing_version_not_found")
    content = LandingContent.model_validate(row.content)
    site.draft = content.model_dump(mode="json")
    site.draft_theme = store.theme_of(content).to_json()
    site.draft_revision = site.draft_revision + 1
    site.updated_at = datetime.now(UTC)
    site.updated_by = updated_by[:320]
    await session.flush()
    return site


# ------------------------------------------------------------------ settings


@dataclass(frozen=True)
class SettingsChange:
    site: LandingSite
    changed: list[str]


async def update_settings(
    session: "AsyncSession",
    *,
    enabled: bool | None,
    address: str | None,
    address_given: bool,
    public_url: str,
    prod: bool,
    updated_by: str,
) -> SettingsChange:
    site = await store.locked_site(session)
    changed: list[str] = []
    new_address = site.address
    if address_given:
        new_address = normalise_address(address, prod=prod) if address else None
        if new_address != site.address:
            changed.append("address")
    will_be_enabled = site.enabled if enabled is None else enabled
    if will_be_enabled:
        if site.published_version is None:
            raise APIError(
                409,
                "Publish your website once before switching it on.",
                "landing_nothing_published",
            )
        if effective_address(new_address, public_url) is None:
            raise APIError(
                409,
                "Set your website's address (or RAG_PUBLIC_URL) before switching the site on.",
                "landing_needs_address",
            )
    if enabled is not None and enabled != site.enabled:
        changed.append("enabled")
    if changed:
        site.address = new_address
        site.enabled = will_be_enabled
        site.updated_at = datetime.now(UTC)
        site.updated_by = updated_by[:320]
        await session.flush()
    return SettingsChange(site=site, changed=changed)


# ------------------------------------------------------------------ "Ask it yourself"


async def ask_key(
    session: "AsyncSession", widget_key_id: uuid.UUID | None, origin: str | None
) -> tuple[AskStatus, WidgetKey | None]:
    """The status of the "Ask it yourself" key and, when it is ready, the key."""
    if widget_key_id is None:
        return "not_connected", None
    key = await session.get(WidgetKey, widget_key_id)
    if key is None:
        return "not_connected", None
    from callobuzz_rag.accounts.operator import get_operator_tenant

    corpus = await session.get(Corpus, key.corpus_id)
    operator = await get_operator_tenant(session)
    # The key must still belong to an operator knowledge base: a knowledge base moved into a
    # customer's workspace must never answer on the operator's public website.
    if corpus is None or operator is None or corpus.tenant_id != operator.id:
        return "kb_missing", None
    if not key.is_active:
        return "key_inactive", None
    allowed = {str(o).rstrip("/").lower() for o in (key.allowed_origins or [])}
    if origin is None or origin not in allowed:
        return "origin_missing", None
    return "ready", key


async def ask_status(session: "AsyncSession", site: LandingSite, origin: str | None) -> AskStatus:
    """Whether the real widget can load on this website: a key of an operator knowledge base
    that is active and allows this website's address."""
    return (await ask_key(session, site.ask_widget_key_id, origin))[0]


@dataclass(frozen=True)
class SiteWarning:
    code: str
    message: str


async def warnings(
    session: "AsyncSession",
    site: LandingSite,
    content: LandingContent,
    *,
    address: str | None,
    ask: AskStatus,
) -> list[SiteWarning]:
    """Things the operator should fix, in plain words. Never blocks a save."""
    from callobuzz_rag.landing import pricing

    out: list[SiteWarning] = []
    if address is None:
        out.append(
            SiteWarning(
                "address_missing",
                "Set your website's address. Without it the site cannot be switched on.",
            )
        )
    if site.published_version is None:
        out.append(SiteWarning("nothing_published", "Nothing is published yet."))
    if content.ask.enabled and ask != "ready":
        out.append(
            SiteWarning(
                "ask_not_connected",
                '"Ask it yourself" is switched on but not connected, so the button is hidden.',
            )
        )
    names = await pricing.placeholder_priced(session, pricing.listed_plan_ids(content))
    if names:
        out.append(
            SiteWarning(
                "placeholder_price",
                'These plans still have a placeholder price and will say "Price on request": '
                + ", ".join(names)
                + ".",
            )
        )
    return out
