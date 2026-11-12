"""The deployer's legal pages: edit a draft, preview it, publish a version (P3).

All operator-only. Public pages are served by `legal.py` and `legal_public.py`.
"""

import csv
import io
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    LegalSettings,
    invalidate_engine_settings_cache,
    read_engine_settings_row,
    update_engine_settings,
)
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.schemas.legal import (
    LegalAcceptanceOut,
    LegalAcceptancesOut,
    LegalDraftRequest,
    LegalPageAdmin,
    LegalPageDetail,
    LegalPageList,
    LegalPreviewOut,
    LegalPreviewRequest,
    LegalPublishRequest,
    LegalVersionOut,
)
from callobuzz_rag.db.models.legal import LegalAcceptance, LegalDocument
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.legal import service
from callobuzz_rag.legal.store import DEFAULT_TITLES, default_template

router = APIRouter(
    prefix="/api/v1/admin/legal", tags=["legal"], dependencies=[Depends(require_admin)]
)


def _version(row: LegalDocument) -> LegalVersionOut:
    assert row.version is not None and row.published_at is not None
    return LegalVersionOut(
        version=row.version,
        published_at=row.published_at,
        published_by=row.published_by,
        change_note=row.change_note,
    )


def _detail(state: service.PageState, settings: EngineSettings) -> LegalPageDetail:
    draft = state.draft
    markdown = draft.body_md if draft is not None else default_template(state.slug)
    values = service.placeholder_values(settings, effective=datetime.now(UTC).date())
    return LegalPageDetail(
        slug=state.slug,
        title=draft.title if draft is not None else DEFAULT_TITLES[state.slug],
        markdown=markdown,
        revision=draft.revision if draft is not None else 0,
        is_default_template=draft is None,
        published=_version(state.latest) if state.latest is not None else None,
        history=[_version(r) for r in state.versions],
        missing=service.missing_for(markdown, values),
        company_changed=service.company_changed(state, settings),
        review_note=service.REVIEW_NOTE,
    )


async def _settings(session: AsyncSession) -> EngineSettings:
    return (await read_engine_settings_row(session))[0]


@router.get("", response_model=LegalPageList)
async def list_pages(session: AsyncSession = Depends(get_session)) -> LegalPageList:
    """The eight pages, with what is published and whether a draft exists."""
    settings = await _settings(session)
    items = []
    for state in await service.all_states(session):
        latest = state.latest
        shown = latest or state.draft
        items.append(
            LegalPageAdmin(
                slug=state.slug,
                title=shown.title if shown is not None else DEFAULT_TITLES[state.slug],
                published_version=latest.version if latest is not None else None,
                published_at=latest.published_at if latest is not None else None,
                has_draft=state.draft is not None,
                draft_changed=state.draft is not None
                and (latest is None or state.draft.revision != latest.revision),
                draft_revision=state.draft.revision if state.draft is not None else 0,
                default_available=True,
                company_changed=service.company_changed(state, settings),
                review_note=service.REVIEW_NOTE,
            )
        )
    return LegalPageList(items=items)


# P3 legal: declared before `/{slug}` so "settings" and "acceptances" are never read as a slug
@router.get("/settings", response_model=LegalSettings)
async def get_legal_settings(session: AsyncSession = Depends(get_session)) -> LegalSettings:
    """The grievance officer, the city for the courts and the footer switch."""
    return (await _settings(session)).legal


@router.put("/settings", response_model=LegalSettings)
async def put_legal_settings(
    body: LegalSettings, request: Request, session: AsyncSession = Depends(get_session)
) -> LegalSettings:
    """Replace the legal settings (a field sent as null is cleared)."""
    _, _, label = actor_from_request(request)
    settings, changed = await update_engine_settings(
        session, {"legal": body.model_dump()}, actor_label=label
    )
    if changed:
        await record_audit(
            session,
            request,
            "legal.settings_updated",
            target_type="settings",
            target_id="legal",
            details={"changed": sorted(k.removeprefix("legal.") for k in changed)},
        )
        await session.commit()
        invalidate_engine_settings_cache()
    return settings.legal


CSV_MAX = 50_000
_CSV_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value: object) -> str:
    """A spreadsheet reads a leading `=`, `+`, `-` or `@` as a formula: neutralise it."""
    text = str(value)
    return "'" + text if text.startswith(_CSV_FORMULA) else text


@router.get(
    "/acceptances",
    response_model=LegalAcceptancesOut,
    responses={200: {"content": {"application/json": {}, "text/csv": {}}}},
)
async def list_acceptances(
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    format: Annotated[Literal["json", "csv"], Query()] = "json",
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Who accepted which Terms or Privacy version at sign-up, newest first. `format=csv`
    downloads every match (accepted_at, email, workspace, page, version)."""
    filters = []
    if tenant_id is not None:
        filters.append(LegalAcceptance.tenant_id == tenant_id)
    if user_id is not None:
        filters.append(LegalAcceptance.user_id == user_id)
    total = (
        await session.execute(select(func.count()).select_from(LegalAcceptance).where(*filters))
    ).scalar_one()
    stmt = select(LegalAcceptance).where(*filters).order_by(LegalAcceptance.accepted_at.desc())
    stmt = stmt.limit(CSV_MAX) if format == "csv" else stmt.offset(offset).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    ids = {r.tenant_id for r in rows if r.tenant_id is not None}
    names: dict[uuid.UUID, str] = {}
    if ids:
        tenants = (await session.execute(select(Tenant).where(Tenant.id.in_(ids)))).scalars().all()
        names = {t.id: t.name for t in tenants}
    if format == "csv":
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\n")
        writer.writerow(["accepted_at", "email", "workspace", "page", "version"])
        for r in rows:
            writer.writerow(
                [
                    r.accepted_at.isoformat(),
                    _csv_cell(r.email),
                    _csv_cell(names.get(r.tenant_id, "") if r.tenant_id else ""),
                    r.slug,
                    r.version,
                ]
            )
        return Response(
            out.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="legal-acceptances.csv"'},
        )
    return LegalAcceptancesOut(
        total=total,
        items=[
            LegalAcceptanceOut(
                id=r.id,
                accepted_at=r.accepted_at,
                email=r.email,
                tenant_id=r.tenant_id,
                workspace=names.get(r.tenant_id) if r.tenant_id else None,
                page=r.slug,
                version=r.version,
                via=r.via,
            )
            for r in rows
        ],
    )


# /P3 legal


@router.get("/{slug}", response_model=LegalPageDetail)
async def get_page(slug: str, session: AsyncSession = Depends(get_session)) -> LegalPageDetail:
    """One page: the draft (or the shipped template), what is published, and its history."""
    checked = service.check_slug(slug)
    return _detail(await service.page_state(session, checked), await _settings(session))


@router.put("/{slug}/draft", response_model=LegalPageDetail)
async def put_draft(
    slug: str,
    body: LegalDraftRequest,
    session: AsyncSession = Depends(get_session),
) -> LegalPageDetail:
    """Save the draft. `base_revision` is the revision you loaded (0 for a first save); a
    changed draft answers 409 `legal_draft_conflict`."""
    checked = service.check_slug(slug)
    await service.save_draft(
        session,
        checked,
        title=body.title,
        markdown=body.markdown,
        base_revision=body.base_revision,
    )
    await session.commit()
    return _detail(await service.page_state(session, checked), await _settings(session))


@router.post("/{slug}/preview", response_model=LegalPreviewOut)
async def preview_page(
    slug: str,
    body: LegalPreviewRequest,
    session: AsyncSession = Depends(get_session),
) -> LegalPreviewOut:
    """The text as visitors would read it today; missing values show as `[[name]]`."""
    service.check_slug(slug)
    html, missing = service.preview(body.markdown, await _settings(session))
    return LegalPreviewOut(html=html, missing=missing)


@router.post("/{slug}/publish", response_model=LegalPageDetail)
async def publish_page(
    slug: str,
    body: LegalPublishRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> LegalPageDetail:
    """Publish the draft (or the shipped template) as the next version. Needs
    `confirm_reviewed: true` and a value for every placeholder the page uses."""
    checked = service.check_slug(slug)
    settings = await _settings(session)
    _, _, label = actor_from_request(request)
    page = await service.publish(
        session,
        checked,
        settings=settings,
        change_note=body.change_note,
        confirm_reviewed=body.confirm_reviewed,
        published_by=label,
    )
    await record_audit(
        session,
        request,
        "legal.page_published",
        target_type="legal_page",
        target_id=checked,
        details={"slug": checked, "version": page.version, "change_note": page.change_note},
    )
    await session.commit()
    return _detail(await service.page_state(session, checked), settings)
