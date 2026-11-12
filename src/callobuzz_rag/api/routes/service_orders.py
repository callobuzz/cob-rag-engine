"""Service orders (P5): what a business bought as a service, tracked from request to done.

`GET /api/v1/billing/service-orders` is the workspace's own list (any member may read it: a status
is not money; a knowledge-base key is refused, the admin principal has no workspace).
`/api/v1/admin/billing/service-orders*` is the operator's list and the one route that moves an
order along (status, a note the owner reads, an internal note the owner never sees). Every route
answers 404 `billing_disabled` while billing is off. The rules live in `billing/service_orders.py`.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_shop import (
    OrderStatus,
    ServiceOrderAdmin,
    ServiceOrderAdminList,
    ServiceOrderList,
    ServiceOrderView,
    UpdateServiceOrderRequest,
)
from callobuzz_rag.billing import service_orders
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.db.models.service_order import ServiceOrder
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/billing", tags=["billing"], dependencies=[Depends(require_billing_enabled)]
)
admin_router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["billing"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)


def workspace_of(request: Request) -> UUID:
    """The workspace of any workspace principal (owner or member); keys of one knowledge base and
    the admin principal are refused."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Billing belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the admin billing pages.", "no_workspace"
        )
    return scope.tenant_id


def view(row: ServiceOrder) -> ServiceOrderView:
    return ServiceOrderView(
        id=row.id,
        name=row.name,
        status=row.status,
        status_label=service_orders.STATUS_LABELS[row.status],
        owner_note=row.owner_note,
        public_note=row.public_note,
        includes_support_months=row.includes_support_months,
        support_until=row.support_until,
        support_request_id=row.support_request_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def admin_view(row: ServiceOrder, tenant_name: str | None) -> ServiceOrderAdmin:
    return ServiceOrderAdmin(
        **view(row).model_dump(),
        tenant_id=row.tenant_id,
        tenant_name=tenant_name,
        grant_id=row.grant_id,
        internal_note=row.internal_note,
    )


@router.get("/service-orders", response_model=ServiceOrderList)
async def my_service_orders(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ServiceOrderList:
    """This workspace's service orders, newest first, with the provider's notes for the owner.
    Members may read them."""
    rows, _ = await service_orders.list_orders(session, tenant_id=workspace_of(request), limit=100)
    return ServiceOrderList(items=[view(r) for r in rows])


async def _names(session: AsyncSession, rows: list[ServiceOrder]) -> dict[UUID, str]:
    ids = {r.tenant_id for r in rows}
    if not ids:
        return {}
    found = await session.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(ids)))
    return {tid: name for tid, name in found.all()}


@admin_router.get("/service-orders", response_model=ServiceOrderAdminList)
async def list_service_orders(
    status: OrderStatus | None = Query(None),
    tenant_id: UUID | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> ServiceOrderAdminList:
    """Every workspace's service orders, newest first."""
    rows, total = await service_orders.list_orders(
        session, tenant_id=tenant_id, status=status, offset=offset, limit=limit
    )
    names = await _names(session, rows)
    return ServiceOrderAdminList(
        items=[admin_view(r, names.get(r.tenant_id)) for r in rows], total=total
    )


@admin_router.patch("/service-orders/{order_id}", response_model=ServiceOrderAdmin)
async def update_service_order(
    order_id: UUID,
    body: UpdateServiceOrderRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ServiceOrderAdmin:
    """Move an order along and leave notes. A status change posts a message on the order's support
    request and e-mails the workspace's owners; `public_note` is what they read, `internal_note`
    never leaves this screen. Cancelling is a status only: refund unused balance separately."""
    order = await service_orders.get_order(session, order_id)
    changed_from = await service_orders.update_order(
        session,
        order,
        status=body.status,
        public_note=body.public_note,
        internal_note=body.internal_note,
        fields_set=frozenset(body.model_fields_set),
        request=request,
    )
    tenant = await session.get(Tenant, order.tenant_id)
    out = admin_view(order, tenant.name if tenant is not None else None)
    await session.commit()
    if changed_from is not None and tenant is not None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        await service_orders.notify_owners(
            session, order, tenant, enqueue=enqueue_email, dashboard_url=_dashboard_url(request)
        )
    return out


def _dashboard_url(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    return str(settings.dashboard_url) if settings is not None else "http://localhost:8090"


async def announce_new_orders(request: Request, session: AsyncSession) -> None:
    """After a purchase's commit: e-mail the provider's inbox about the new order, once. Never
    raises (the order is already committed and in the Support inbox)."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        engine = (await read_engine_settings_row(session))[0]
        settings = getattr(request.app.state, "settings", None)
        await service_orders.notify_provider_of_new_orders(
            request.app.state.session_factory,
            enqueue=enqueue_email,
            support_email=str(engine.branding.support_email or ""),
            superadmin_email=getattr(settings, "superadmin_email", None),
            dashboard_url=_dashboard_url(request),
        )
    except Exception:
        logger.warning("New service order was not announced", exc_info=True)
