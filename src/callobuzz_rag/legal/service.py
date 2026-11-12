"""Legal pages: drafts, preview, publish, and the acceptances recorded at sign-up.

A draft is the one row per slug with `version IS NULL`; publishing copies it into a new frozen row
(`version = n + 1`) whose placeholders are filled and whose HTML is rendered once, so an
acceptance always points at the exact text the person saw. Nothing here commits: the caller does.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.company import STATES
from callobuzz_rag.db.models.legal import LegalAcceptance, LegalDocument
from callobuzz_rag.legal.render import fill, to_html, used_placeholders
from callobuzz_rag.legal.store import DEFAULT_TITLES, SLUGS, Slug, default_template

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.accounts.settings_store import EngineSettings

REVIEW_NOTE: Final = (
    "This is a starting template. Have your lawyer review it before you publish it."
)
MARKDOWN_MAX: Final = 100_000


def not_found() -> APIError:
    return APIError(404, "That legal page does not exist.", "legal_page_not_found")


def check_slug(slug: str) -> Slug:
    if slug not in SLUGS:
        raise not_found()
    return slug


def format_date(day: date) -> str:
    """`8 October 2026`: a page for readers in India, so day first and the month spelled out."""
    return f"{day.day} {day.strftime('%B %Y')}"


def placeholder_values(settings: "EngineSettings", *, effective: date) -> dict[str, str]:
    """What each `{{placeholder}}` becomes, from branding, the company details and the legal
    settings. A value that is not set is missing (absent from the dict)."""
    branding = settings.branding
    legal = settings.legal
    company = settings.company  # the deployer's own business (`core/company.py`)
    values: dict[str, str | None] = {
        "product_name": branding.product_name,
        "support_email": branding.support_email or company.email,
        "grievance_name": legal.grievance_name,
        "grievance_email": legal.grievance_email,
        "grievance_address": legal.grievance_address,
        "jurisdiction_city": legal.jurisdiction_city,
        "website": legal.website,
        "effective_date": format_date(effective),
    }
    address = ", ".join(part for part in (company.address_line1, company.address_line2) if part)
    state_name = STATES.get(company.state_code or "")
    state = " ".join(part for part in (state_name, company.pincode) if part)
    values.update(
        {
            "legal_name": company.legal_name,
            "trade_name": company.trade_name or company.legal_name,
            "address": address,
            "city": company.city,
            "state": state,
            "email": company.email,
            "phone": company.phone,
        }
    )
    return {name: value for name, value in values.items() if value}


def missing_for(markdown: str, values: dict[str, str]) -> list[str]:
    return sorted(name for name in used_placeholders(markdown) if name not in values)


# ------------------------------------------------------------------ reads


@dataclass(slots=True)
class PageState:
    """Everything the admin screens show about one slug."""

    slug: Slug
    draft: LegalDocument | None
    versions: list[LegalDocument] = field(default_factory=list)  # newest first

    @property
    def latest(self) -> LegalDocument | None:
        return self.versions[0] if self.versions else None


async def _rows(session: "AsyncSession", slug: Slug | None = None) -> list[LegalDocument]:
    stmt = select(LegalDocument)
    if slug is not None:
        stmt = stmt.where(LegalDocument.slug == slug)
    return list((await session.execute(stmt)).scalars().all())


def _state(slug: Slug, rows: list[LegalDocument]) -> PageState:
    mine = [r for r in rows if r.slug == slug]
    draft = next((r for r in mine if r.version is None), None)
    versions = sorted((r for r in mine if r.version is not None), key=lambda r: -(r.version or 0))
    return PageState(slug=slug, draft=draft, versions=versions)


async def page_state(session: "AsyncSession", slug: Slug) -> PageState:
    return _state(slug, await _rows(session, slug))


async def all_states(session: "AsyncSession") -> list[PageState]:
    rows = await _rows(session)
    return [_state(slug, rows) for slug in SLUGS]


def company_changed(state: PageState, settings: "EngineSettings") -> bool:
    """The newest published text no longer matches what today's settings would fill in."""
    latest = state.latest
    if latest is None or latest.published_at is None or latest.body_filled is None:
        return False
    values = placeholder_values(settings, effective=latest.published_at.astimezone(UTC).date())
    return fill(latest.body_md, values) != latest.body_filled


# ------------------------------------------------------------------ drafts


async def _locked_draft(session: "AsyncSession", slug: Slug) -> LegalDocument | None:
    return (
        await session.execute(
            select(LegalDocument)
            .where(LegalDocument.slug == slug, LegalDocument.version.is_(None))
            .with_for_update()
        )
    ).scalar_one_or_none()


def _conflict() -> APIError:
    return APIError(
        409,
        "This page was changed by someone else. Reload it and apply your edit again.",
        "legal_draft_conflict",
    )


async def save_draft(
    session: "AsyncSession", slug: Slug, *, title: str, markdown: str, base_revision: int
) -> LegalDocument:
    """Create the draft (`base_revision` 0) or update it (`base_revision` = its revision)."""
    draft = await _locked_draft(session, slug)
    now = datetime.now(UTC)
    if draft is None:
        if base_revision != 0:
            raise _conflict()
        draft = LegalDocument(
            slug=slug, version=None, title=title, body_md=markdown, revision=1, created_at=now
        )
        draft.updated_at = now
        session.add(draft)
        try:
            await session.flush()
        except IntegrityError as exc:  # another request created the draft first
            raise _conflict() from exc
        return draft
    if draft.revision != base_revision:
        raise _conflict()
    draft.title = title
    draft.body_md = markdown
    draft.revision = draft.revision + 1
    draft.updated_at = now
    await session.flush()
    return draft


def preview(markdown: str, settings: "EngineSettings") -> tuple[str, list[str]]:
    """The page as it would read today: HTML, and the placeholders with no value (shown as
    `[[name]]`)."""
    values = placeholder_values(settings, effective=datetime.now(UTC).date())
    return to_html(fill(markdown, values)), missing_for(markdown, values)


async def publish(
    session: "AsyncSession",
    slug: Slug,
    *,
    settings: "EngineSettings",
    change_note: str | None,
    confirm_reviewed: bool,
    published_by: str,
) -> LegalDocument:
    """Freeze the draft (or the shipped template when there is none) as the next version.

    Raises:
        APIError 422 `legal_review_required`: the owner did not confirm a review.
        APIError 422 `legal_placeholders_missing` (extra `missing`): a used placeholder has no
            value yet.
    """
    if not confirm_reviewed:
        raise APIError(
            422,
            "Confirm that you have had this page reviewed before you publish it.",
            "legal_review_required",
        )
    draft = await _locked_draft(session, slug)
    now = datetime.now(UTC)
    title = draft.title if draft is not None else DEFAULT_TITLES[slug]
    body_md = draft.body_md if draft is not None else default_template(slug)
    revision = draft.revision if draft is not None else 1
    values = placeholder_values(settings, effective=now.date())
    missing = missing_for(body_md, values)
    if missing:
        raise APIError(
            422,
            "Fill in these details first: " + ", ".join(missing) + ".",
            "legal_placeholders_missing",
            missing=missing,
        )
    if draft is None:  # the shipped template becomes the draft, so it can be edited from here
        draft = LegalDocument(
            slug=slug, version=None, title=title, body_md=body_md, revision=revision, created_at=now
        )
        draft.updated_at = now
        session.add(draft)
        try:
            await session.flush()
        except IntegrityError as exc:  # two first publishes raced: the other one's draft won
            raise _conflict() from exc
    rows = await _rows(session, slug)
    version = max((r.version or 0 for r in rows), default=0) + 1
    filled = fill(body_md, values)
    page = LegalDocument(
        slug=slug,
        version=version,
        title=title,
        body_md=body_md,
        body_filled=filled,
        body_html=to_html(filled),
        revision=revision,
        change_note=(change_note or "").strip() or None,
        created_at=now,
        published_at=now,
        published_by=published_by[:320],
    )
    page.updated_at = now
    session.add(page)
    try:
        await session.flush()
    except IntegrityError as exc:  # two publishes raced past the draft lock
        raise _conflict() from exc
    return page


# ------------------------------------------------------------------ acceptances


@dataclass(frozen=True, slots=True)
class Accepted:
    """The versions a person agreed to at sign-up, as sent in `accept_legal`."""

    terms: int | None = None
    privacy: int | None = None

    def as_payload(self) -> dict[str, int]:
        return {k: v for k, v in (("terms", self.terms), ("privacy", self.privacy)) if v}


async def required_versions(session: "AsyncSession") -> dict[str, int]:
    """The versions sign-up must be accepted at: Terms and Privacy, only when both are
    published. Empty means sign-up asks for nothing (the default)."""
    from callobuzz_rag.legal.store import REQUIRED_AT_SIGNUP, published_all

    pages = {p.slug: p for p in await published_all(session)}
    if not all(slug in pages for slug in REQUIRED_AT_SIGNUP):
        return {}
    return {slug: pages[slug].version for slug in REQUIRED_AT_SIGNUP}


def check_acceptance(required: dict[str, int], sent: dict[str, int] | None) -> dict[str, int]:
    """The accepted versions to store, or the coded refusal.

    Raises:
        APIError 422 `legal_acceptance_required`: a required page was not accepted.
        APIError 409 `legal_version_changed`: an older version than the one now published.
    """
    if not required:
        return {}
    sent = sent or {}
    for slug in required:
        if not sent.get(slug):
            raise APIError(
                422,
                "Please accept the Terms and the Privacy notice to sign up.",
                "legal_acceptance_required",
                required=required,
            )
    for slug, version in required.items():
        if sent[slug] != version:
            raise APIError(
                409,
                "The Terms or the Privacy notice changed while you were reading. "
                "Please read the new version and accept it.",
                "legal_version_changed",
                required=required,
            )
    return {slug: sent[slug] for slug in required}


async def record_acceptances(
    session: "AsyncSession",
    accepted: dict[str, Any],
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
) -> int:
    """One `legal_acceptances` row per accepted page, pointing at the exact published row.
    Returns the number written (a version that no longer resolves is skipped, never guessed)."""
    written = 0
    for slug, version in accepted.items():
        if slug not in SLUGS or not isinstance(version, int):
            continue
        document = (
            await session.execute(
                select(LegalDocument).where(
                    LegalDocument.slug == slug, LegalDocument.version == version
                )
            )
        ).scalar_one_or_none()
        if document is None:
            continue
        session.add(
            LegalAcceptance(
                user_id=user_id,
                email=email,
                tenant_id=tenant_id,
                document_id=document.id,
                slug=slug,
                version=version,
                via="signup",
                accepted_at=datetime.now(UTC),
            )
        )
        written += 1
    return written
