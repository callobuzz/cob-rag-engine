"""Support requests, the business's side (TV3): `GET /api/v1/support/info`, a list of this
workspace's requests, open one, read the thread, add to it and close it.

Owners and members of a workspace may all write to the provider (it is not an owner-only
action); a knowledge-base key and the operator have no workspace here. The provider's side is in
`support_admin.py`. Texts are stored as plain text and escaped wherever they are shown.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import EngineSettings, read_engine_settings_row
from callobuzz_rag.api.deps import get_session, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.support import (
    SupportInfoOut,
    SupportListOut,
    SupportMessageCreate,
    SupportMessageOut,
    SupportRequestCreate,
    SupportRequestDetail,
    SupportRequestOut,
    SupportStatus,
)
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.support import notify, service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/support", tags=["support"])


def _workspace(request: Request) -> uuid.UUID:
    """The workspace of a sign-in or a `tk_` key; a knowledge-base key and the operator have
    none."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403,
            "Support requests belong to a workspace, not to one knowledge base.",
            "scope_forbidden",
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the support inbox instead.", "no_workspace"
        )
    return scope.tenant_id


def request_out(row: SupportRequest) -> SupportRequestOut:
    return SupportRequestOut(
        id=row.id,
        number=row.number,
        subject=row.subject,
        status=row.status,
        page=row.page,
        created_by_email=row.created_by_email,
        created_at=row.created_at,
        last_message_at=row.last_message_at,
        first_response_at=row.first_response_at,
        closed_at=row.closed_at,
    )


def message_out(message: SupportMessage, *, viewer_is_business: bool = True) -> SupportMessageOut:
    return SupportMessageOut(
        id=message.id,
        author_kind=message.author_kind,
        author=message.author_email if message.author_kind == "business" else None,
        body=message.body,
        created_at=message.created_at,
    )


async def detail_out(session: AsyncSession, row: SupportRequest) -> SupportRequestDetail:
    messages = await service.messages_of(session, row.id)
    return SupportRequestDetail(
        **request_out(row).model_dump(), messages=[message_out(m) for m in messages]
    )


def _author(request: Request) -> tuple[str, uuid.UUID | None]:
    kind, user_id, label = actor_from_request(request)
    return label, user_id if kind == "user" else None


def _dashboard_url(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    return str(settings.dashboard_url) if settings is not None else "http://localhost:8090"


def _superadmin(request: Request) -> str | None:
    settings = getattr(request.app.state, "settings", None)
    return getattr(settings, "superadmin_email", None)


async def _engine(session: AsyncSession) -> EngineSettings:
    return (await read_engine_settings_row(session))[0]


async def _audit_status(
    session: AsyncSession, request: Request, row: SupportRequest, tenant: Tenant, status: str
) -> None:
    await record_audit(
        session,
        request,
        "support.request_status_changed",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="support_request",
        target_id=str(row.id),
        details={"request_id": str(row.id), "number": row.number, "status": status},
    )


@router.get("/info", response_model=SupportInfoOut)
async def support_info(
    request: Request, session: AsyncSession = Depends(get_session)
) -> SupportInfoOut:
    """Whether the provider takes support requests, the time they promise to reply in, and how
    many of this workspace's requests are open."""
    tenant_id = _workspace(request)
    engine = await _engine(session)
    return SupportInfoOut(
        enabled=engine.trust.support_enabled,
        provider_name=engine.branding.product_name,
        sla_hours=engine.trust.support_sla_hours,
        open_count=await service.open_count(session, tenant_id),
    )


@router.get("/requests", response_model=SupportListOut)
async def list_support_requests(
    request: Request,
    status: SupportStatus | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> SupportListOut:
    """This workspace's support requests, the latest activity first."""
    tenant_id = _workspace(request)
    rows, total = await service.list_requests(
        session, tenant_id=tenant_id, status=status, offset=offset, limit=limit
    )
    return SupportListOut(items=[request_out(r) for r in rows], total=total)


@router.post("/requests", response_model=SupportRequestOut, status_code=201)
async def create_support_request(
    body: SupportRequestCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SupportRequestOut:
    """Write to the provider. The page you were on and the last error codes the dashboard
    saw travel with it, with your plan and workspace status. At most ten new requests a day
    (429 `support_rate_limited`); 409 `support_disabled` when the provider turned it off."""
    tenant_id = _workspace(request)
    engine = await _engine(session)
    if not engine.trust.support_enabled:
        raise service.disabled()
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise service.not_found()
    email, user_id = _author(request)
    row, first = await service.create_request(
        session,
        tenant,
        subject=body.subject,
        message=body.message,
        page=body.page,
        client_errors=list(body.client_errors),
        author_email=email,
        author_user_id=user_id,
    )
    await record_audit(
        session,
        request,
        "support.request_created",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="support_request",
        target_id=str(row.id),
        details={"request_id": str(row.id), "number": row.number},
    )
    out = request_out(row)
    await session.commit()
    await _tell_provider(request, session, row, first, tenant, engine, first_message=True)
    return out


async def _tell_provider(
    request: Request,
    session: AsyncSession,
    row: SupportRequest,
    message: SupportMessage,
    tenant: Tenant,
    engine: EngineSettings,
    *,
    first_message: bool,
) -> None:
    """After the commit: mail the provider's inbox. Never fails the request."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        await notify.notify_business_message(
            session,
            row,
            message,
            tenant,
            enqueue=enqueue_email,
            support_email=str(engine.branding.support_email or ""),
            superadmin_email=_superadmin(request),
            dashboard_url=_dashboard_url(request),
            first=first_message,
        )
    except Exception:
        logger.warning("Support mail was not queued", exc_info=True)


@router.get("/requests/{request_id}", response_model=SupportRequestDetail)
async def get_support_request(
    request_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> SupportRequestDetail:
    """One request with its thread. Another workspace's request is a 404."""
    tenant_id = _workspace(request)
    row = await service.get_request(session, request_id, tenant_id=tenant_id)
    return await detail_out(session, row)


@router.post("/requests/{request_id}/messages", response_model=SupportMessageOut, status_code=201)
async def add_support_message(
    request_id: uuid.UUID,
    body: SupportMessageCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SupportMessageOut:
    """Add to a request. A closed request is opened again; the provider is told. At most twenty
    messages an hour from one workspace (429 `support_rate_limited`)."""
    tenant_id = _workspace(request)
    engine = await _engine(session)
    if not engine.trust.support_enabled:
        raise service.disabled()
    row = await service.get_request(session, request_id, tenant_id=tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise service.not_found()
    email, _ = _author(request)
    message, changed = await service.add_business_message(
        session, row, body=body.body, author_email=email
    )
    if changed is not None:
        await _audit_status(session, request, row, tenant, "open")
    out = message_out(message)
    await session.commit()
    await _tell_provider(request, session, row, message, tenant, engine, first_message=False)
    return out


@router.post("/requests/{request_id}/close", response_model=SupportRequestOut)
async def close_support_request(
    request_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> SupportRequestOut:
    """Close a request that is done. Adding a message opens it again."""
    tenant_id = _workspace(request)
    row = await service.get_request(session, request_id, tenant_id=tenant_id)
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise service.not_found()
    changed = await service.set_status(session, row, "closed")
    if changed is not None:
        await _audit_status(session, request, row, tenant, "closed")
    out = request_out(row)
    await session.commit()
    return out


__all__: list[Any] = ["message_out", "request_out", "router"]
