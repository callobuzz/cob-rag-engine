"""The provider's support inbox (TV3): every business's requests, the thread with the workspace
information the business sent along, replies, the status, and first-response numbers.

Admin only. A reply is mailed to the request's author and the workspace's owners after the
commit; the first reply fixes the request's first response time.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.routes.support import request_out
from callobuzz_rag.api.schemas.support import (
    AdminReplyRequest,
    AdminStatusRequest,
    AdminSupportDetail,
    AdminSupportListOut,
    AdminSupportMessageOut,
    AdminSupportRequestOut,
    SupportStatsOut,
    SupportStatus,
)
from callobuzz_rag.db.models.support import SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.landing import contact
from callobuzz_rag.support import notify, service, stats

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/support", tags=["support"], dependencies=[Depends(require_admin)]
)


def _admin_out(row: SupportRequest, names: dict[uuid.UUID, str]) -> AdminSupportRequestOut:
    return AdminSupportRequestOut(
        **request_out(row).model_dump(),
        tenant_id=row.tenant_id,
        workspace_name=names.get(row.tenant_id, ""),
        source="website" if contact.is_enquiry(row.context) else "workspace",
    )


async def _detail(session: AsyncSession, row: SupportRequest) -> AdminSupportDetail:
    names = await service.workspace_names(session, {row.tenant_id})
    messages = await service.messages_of(session, row.id)
    return AdminSupportDetail(
        **_admin_out(row, names).model_dump(),
        context=dict(row.context or {}),
        messages=[
            AdminSupportMessageOut(
                id=m.id,
                author_kind=m.author_kind,
                author_email=m.author_email,
                body=m.body,
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@router.get("/requests", response_model=AdminSupportListOut)
async def list_all_support_requests(
    status: SupportStatus | None = Query(None),
    tenant_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=100, description="A number such as #1042, or words"),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> AdminSupportListOut:
    """Every workspace's requests, the latest activity first."""
    rows, total = await service.list_requests(
        session, tenant_id=tenant_id, status=status, q=q, offset=offset, limit=limit
    )
    names = await service.workspace_names(session, {r.tenant_id for r in rows})
    return AdminSupportListOut(items=[_admin_out(r, names) for r in rows], total=total)


@router.get("/stats", response_model=SupportStatsOut)
async def support_stats(
    days: int = Query(30, ge=1, le=365), session: AsyncSession = Depends(get_session)
) -> SupportStatsOut:
    """How many requests wait for you, the median minutes to a first reply, and the share
    answered within your promised time (`trust.support_sla_hours`), over the last `days`."""
    now = datetime.now(UTC)
    engine = (await read_engine_settings_row(session))[0]
    since = now - timedelta(days=days)
    open_now = int(
        (
            await session.execute(
                select(func.count())
                .select_from(SupportRequest)
                .where(SupportRequest.status == "open")
            )
        ).scalar_one()
    )
    in_window = (
        await session.execute(
            select(SupportRequest.created_at, SupportRequest.first_response_at).where(
                SupportRequest.created_at >= since
            )
        )
    ).all()
    window = [stats.Row(created, first) for created, first in in_window]
    sla = engine.trust.support_sla_hours
    result = stats.compute(window, sla_hours=sla, now=now)
    return SupportStatsOut(
        open=open_now,
        answered=result.answered,
        median_first_response_minutes=result.median_first_response_minutes,
        within_sla_pct=result.within_sla_pct,
        sla_hours=sla,
        days=days,
    )


@router.get("/requests/{request_id}", response_model=AdminSupportDetail)
async def get_any_support_request(
    request_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AdminSupportDetail:
    """One request with its thread and the workspace information that came with it."""
    return await _detail(session, await service.get_request(session, request_id))


async def _tenant_of(session: AsyncSession, row: SupportRequest) -> Tenant:
    tenant = await session.get(Tenant, row.tenant_id)
    if tenant is None:
        raise service.not_found()
    return tenant


async def _audit_status(
    session: AsyncSession, request: Request, row: SupportRequest, tenant: Tenant
) -> None:
    await record_audit(
        session,
        request,
        "support.request_status_changed",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="support_request",
        target_id=str(row.id),
        details={"request_id": str(row.id), "number": row.number, "status": row.status},
    )


@router.post("/requests/{request_id}/messages", response_model=AdminSupportDetail, status_code=201)
async def reply_to_support_request(
    request_id: uuid.UUID,
    body: AdminReplyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AdminSupportDetail:
    """Answer a business. The request becomes `answered` (or `waiting` when you asked
    something, or `closed`); its author and the workspace's owners are mailed."""
    row = await service.get_request(session, request_id)
    tenant = await _tenant_of(session, row)
    _, _, label = actor_from_request(request)
    message, changed = await service.add_deployer_message(
        session, row, body=body.body, author_email=label, status=body.status
    )
    if changed is not None:
        await _audit_status(session, request, row, tenant)
    out = await _detail(session, row)
    engine = (await read_engine_settings_row(session))[0]
    await session.commit()
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        settings = getattr(request.app.state, "settings", None)
        await notify.notify_reply(
            session,
            row,
            message,
            tenant,
            enqueue=enqueue_email,
            product_name=engine.branding.product_name,
            dashboard_url=str(settings.dashboard_url) if settings is not None else "",
        )
    except Exception:
        logger.warning("Support reply mail was not queued", exc_info=True)
    return out


@router.patch("/requests/{request_id}", response_model=AdminSupportDetail)
async def set_support_status(
    request_id: uuid.UUID,
    body: AdminStatusRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AdminSupportDetail:
    """Set a request's status (open, answered, waiting, closed)."""
    row = await service.get_request(session, request_id)
    tenant = await _tenant_of(session, row)
    changed = await service.set_status(session, row, body.status)
    if changed is not None:
        await _audit_status(session, request, row, tenant)
    out = await _detail(session, row)
    await session.commit()
    return out
