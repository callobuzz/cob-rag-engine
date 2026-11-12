"""Reads of the legal pages: the shipped templates and the published versions."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import load_only

from callobuzz_rag.db.models.legal import LegalDocument
from callobuzz_rag.legal.slugs import REQUIRED_AT_SIGNUP, SLUGS, Slug

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "DEFAULT_TITLES",
    "REQUIRED_AT_SIGNUP",
    "SLUGS",
    "PublishedPage",
    "Slug",
    "default_template",
    "published",
    "published_all",
]

DEFAULT_TITLES: Final[dict[Slug, str]] = {
    "terms": "Terms of Service",
    "privacy": "Privacy Notice",
    "refunds": "Refund and Cancellation Policy",
    "delivery": "Shipping and Delivery Policy",
    "acceptable-use": "Acceptable Use Policy",
    "dpa": "Data Processing Addendum",
    "sub-processors": "Sub-processors",
    "grievance": "Grievance Officer",
}


@cache
def default_template(slug: Slug) -> str:
    """The Markdown template shipped with the engine for `slug` (placeholders unfilled)."""
    if slug not in SLUGS:
        raise KeyError(slug)
    path = resources.files("callobuzz_rag.legal").joinpath("templates", f"{slug}.md")
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# ------------------------------------------------------------------ published versions


@dataclass(frozen=True, slots=True)
class PublishedPage:
    id: uuid.UUID
    slug: Slug
    title: str
    version: int
    published_at: datetime
    html: str  # rendered and frozen at publish (placeholders filled)
    markdown: str  # the published text, placeholders filled


def _page(row: LegalDocument) -> PublishedPage:
    assert row.version is not None and row.published_at is not None
    return PublishedPage(
        id=row.id,
        slug=cast("Slug", row.slug),
        title=row.title,
        version=row.version,
        published_at=row.published_at,
        html=row.body_html or "",
        markdown=row.body_filled or "",
    )


async def published(session: "AsyncSession", slug: Slug) -> PublishedPage | None:
    """The newest published version of `slug`, or None."""
    row = (
        await session.execute(
            select(LegalDocument)
            .where(LegalDocument.slug == slug, LegalDocument.version.is_not(None))
            .order_by(LegalDocument.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return _page(row) if row is not None else None


async def published_all(session: "AsyncSession") -> list[PublishedPage]:
    """The newest published version of each slug, in the order of `SLUGS`, in one query.

    Listing only: the pages come back WITHOUT their bodies (`html` and `markdown` are empty), so
    the cost does not grow with every publish; read a page with `published` to get its text.
    """
    rows = (
        (
            await session.execute(
                select(LegalDocument)
                .options(
                    load_only(
                        LegalDocument.id,
                        LegalDocument.slug,
                        LegalDocument.title,
                        LegalDocument.version,
                        LegalDocument.published_at,
                    )
                )
                .where(LegalDocument.version.is_not(None))
                .ext(distinct_on(LegalDocument.slug))
                .order_by(LegalDocument.slug, LegalDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )
    newest: dict[str, LegalDocument] = {}
    for row in rows:  # the query already returns one per slug; this keeps the newest regardless
        if row.slug not in newest or (newest[row.slug].version or 0) < (row.version or 0):
            newest[row.slug] = row
    return [_page(newest[slug]) for slug in SLUGS if slug in newest]
