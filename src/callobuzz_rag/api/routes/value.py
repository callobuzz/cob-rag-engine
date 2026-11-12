"""The value report (TV4): the owner's value settings, a month's numbers with the formula, the
printable page, and the deployer's view of one business.

A report is an estimate from the owner's own settings, always shown with its formula; closed
months are frozen by the beat job on the 1st and never change afterwards.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin, require_owner, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.value import (
    ValueReportMonth,
    ValueReportOut,
    ValueReportsOut,
    ValueSettingsOut,
    ValueSettingsRequest,
)
from callobuzz_rag.business import value_report as service
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.value import ValueReport

router = APIRouter(prefix="/api/v1/value", tags=["value"])
admin_router = APIRouter(
    prefix="/api/v1/admin/tenants", tags=["value"], dependencies=[Depends(require_admin)]
)

HTML_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"
MAX_FROZEN = 24
MONTH_PATTERN = r"^\d{4}-\d{2}$"

ReportFormat = Literal["json", "html"]
_FORMATS: dict[int | str, dict[str, Any]] = {
    200: {
        "description": "The report as JSON or a printable HTML page.",
        "content": {"application/json": {}, "text/html": {}},
    }
}


def _workspace(request: Request) -> uuid.UUID:
    """The workspace of a sign-in or a `tk_` key; a knowledge-base key and the operator have
    none."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403,
            "The value report belongs to a workspace, not to one knowledge base.",
            "scope_forbidden",
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the business's page instead.", "no_workspace"
        )
    return scope.tenant_id


async def _respond(
    session: AsyncSession, tenant_id: uuid.UUID, month: str | None, fmt: ReportFormat
) -> Any:
    report = await service.report_for(session, tenant_id, month, now=datetime.now(UTC))
    if fmt == "html":
        tenant = await session.get(Tenant, tenant_id)
        branding = (await read_engine_settings_row(session))[0].branding
        page = service.render_html(
            report, tenant.name if tenant is not None else "Workspace", branding
        )
        return Response(
            page,
            media_type="text/html; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": HTML_CSP,
                "X-Content-Type-Options": "nosniff",
            },
        )
    return report


# ------------------------------------------------------------------ settings


@router.get("/settings", response_model=ValueSettingsOut)
async def get_value_settings(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ValueSettingsOut:
    """The values the estimate uses, or suggestions for this kind of business until the owner
    saves their own."""
    tenant_id = _workspace(request)
    ctx = await service.workspace_context(session, tenant_id)
    return service.settings_out(await service.settings_state(session, tenant_id, ctx))


@router.put("/settings", response_model=ValueSettingsOut)
async def put_value_settings(
    body: ValueSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ValueSettingsOut:
    """Save the owner's values. Owners only."""
    tenant_id = _workspace(request)
    require_owner(request)
    ctx = await service.workspace_context(session, tenant_id)
    _, _, actor = actor_from_request(request)
    changed = await service.save_settings(
        session, tenant_id, ctx, body.model_dump(), actor_label=actor
    )
    if changed:
        await record_audit(
            session,
            request,
            "value.settings_updated",
            tenant_id=tenant_id,
            target_type="value_settings",
            target_id=str(tenant_id),
            details={
                "changed": changed,
                **({"industry_defaults": body.industry_defaults} if body.industry_defaults else {}),
            },
        )
        await session.commit()
    return service.settings_out(await service.settings_state(session, tenant_id, ctx))


# ------------------------------------------------------------------ reports

MonthQuery = Annotated[
    str | None,
    Query(pattern=MONTH_PATTERN, description="`YYYY-MM`; omitted: this month so far"),
]


@router.get("/report", response_model=ValueReportOut, responses=_FORMATS)
async def get_value_report(
    request: Request,
    month: MonthQuery = None,
    format: ReportFormat = Query("json", description="json, or html for a printable page"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """What the assistant did in a month and what that is worth by the owner's own values,
    with the formula. 422 `value_month_invalid` for a future month or one before the workspace
    existed."""
    return await _respond(session, _workspace(request), month, format)


@router.get("/reports", response_model=ValueReportsOut)
async def list_value_reports(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ValueReportsOut:
    """The frozen months, newest first (at most 24)."""
    rows = (
        (
            await session.execute(
                select(ValueReport)
                .where(ValueReport.tenant_id == _workspace(request))
                .order_by(ValueReport.month.desc())
                .limit(MAX_FROZEN)
            )
        )
        .scalars()
        .all()
    )
    return ValueReportsOut(
        items=[
            ValueReportMonth(
                month=f"{r.month:%Y-%m}",
                total_minor=int(r.data["breakdown"]["total_minor"]),
                currency=r.data["currency"],
            )
            for r in rows
        ]
    )


@admin_router.get("/{tenant_id}/value-report", response_model=ValueReportOut, responses=_FORMATS)
async def get_tenant_value_report(
    tenant_id: uuid.UUID,
    month: MonthQuery = None,
    format: ReportFormat = Query("json", description="json, or html for a printable page"),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """One business's value report, as its owner sees it, for the deployer."""
    if await session.get(Tenant, tenant_id) is None:
        raise APIError(404, "That workspace does not exist.", "tenant_not_found")
    return await _respond(session, tenant_id, month, format)
