"""Service orders (P5): a business bought a service add-on (website design, installation help,
priority support) and the provider does the work.

A `service` grant opens exactly one order in the same transaction (any payment kind, any path:
the admin applying it, the owner's balance, an online payment). The order opens a support request
as well ("Service order: <name>"), so it is also in the Support inbox; every status change posts a
message on that request and e-mails the owners. `public_note` is what the owner reads;
`internal_note` never leaves the operator's screens. Cancelling is a status, never a refund: the
operator refunds unused balance or adjusts it, so money only moves by explicit entries.

Statuses: `requested` -> `in_progress` -> `done`; `requested` and `in_progress` -> `cancelled`;
`done` and `cancelled` are final. Nothing here commits; the callers do.
"""

import calendar
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.add_on import AddOnGrant
from callobuzz_rag.db.models.service_order import ORDER_STATUSES, ServiceOrder
from callobuzz_rag.db.models.support import SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.support import service as support_service

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)

STATUS_LABELS: Final = {
    "requested": "Requested",
    "in_progress": "In progress",
    "done": "Done",
    "cancelled": "Cancelled",
}
TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "requested": frozenset({"in_progress", "cancelled"}),
    "in_progress": frozenset({"done", "cancelled"}),
    "done": frozenset(),
    "cancelled": frozenset(),
}
NOTE_MAX: Final = 1000
EXPIRES_S: Final = 7 * 24 * 3600
Enqueue = Callable[..., None]

assert set(STATUS_LABELS) == set(ORDER_STATUSES)


def not_found() -> APIError:
    return APIError(404, "That service order does not exist.", "service_order_not_found")


def add_months(day: date, months: int) -> date:
    """`day` plus calendar months, the day clamped to the shorter month (31 Jan + 1 -> 28 Feb)."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def support_until(done_on: datetime, months: int | None) -> date | None:
    return add_months(done_on.astimezone(UTC).date(), months) if months else None


# ------------------------------------------------------------------------------ opening


async def open_for_grant(
    session: AsyncSession,
    tenant: Tenant,
    grant: AddOnGrant,
    *,
    owner_note: str | None,
    now: datetime | None = None,
) -> ServiceOrder:
    """The order of a service grant, with its support request. Idempotent: the grant's one order
    is returned when it already exists. The caller commits."""
    moment = now or datetime.now(UTC)
    existing = (
        await session.execute(select(ServiceOrder).where(ServiceOrder.grant_id == grant.id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    order = ServiceOrder(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        grant_id=grant.id,
        name=grant.name,
        status="requested",
        owner_note=(owner_note or "")[:NOTE_MAX] or None,
        includes_support_months=grant.includes_support_months,
        created_at=moment,
        updated_at=moment,
    )
    request = SupportRequest(
        tenant_id=tenant.id,
        number=await support_service.next_number(session),
        subject=f"Service order: {grant.name}"[:150],
        status="open",
        context={"service_order": {"order_id": str(order.id), "service": grant.name}},
        created_by_email=str(grant.granted_by)[:320],
        last_message_at=moment,
        created_at=moment,
    )
    session.add(request)
    await session.flush()
    order.support_request_id = request.id
    session.add(order)
    await session.flush()
    first = f"New order: {grant.name}."
    if order.owner_note:
        first += f"\n\nNote from the business: {order.owner_note}"
    session.add(
        SupportMessage(
            request_id=request.id,
            author_kind="business",
            author_email=str(grant.granted_by)[:320],
            body=first,
            created_at=moment,
        )
    )
    await session.flush()
    return order


# ------------------------------------------------------------------------------ reading


async def get_order(
    session: AsyncSession, order_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None
) -> ServiceOrder:
    """One order; with `tenant_id`, only that workspace's (another's is the same 404).

    Raises:
        APIError 404 `service_order_not_found`.
    """
    row = (
        await session.execute(select(ServiceOrder).where(ServiceOrder.id == order_id))
    ).scalar_one_or_none()
    if row is None or (tenant_id is not None and row.tenant_id != tenant_id):
        raise not_found()
    return row


async def list_orders(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    status: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[ServiceOrder], int]:
    """Orders newest first, optionally of one workspace and one status."""
    conditions: list[Any] = []
    if tenant_id is not None:
        conditions.append(ServiceOrder.tenant_id == tenant_id)
    if status is not None:
        conditions.append(ServiceOrder.status == status)
    total = int(
        (
            await session.execute(select(func.count()).select_from(ServiceOrder).where(*conditions))
        ).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                select(ServiceOrder)
                .where(*conditions)
                .order_by(ServiceOrder.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows, total


# ------------------------------------------------------------------------------ changing


def _invalid_transition(old: str, new: str) -> APIError:
    return APIError(
        409,
        f"An order that is {STATUS_LABELS[old].lower()} cannot become {STATUS_LABELS[new].lower()}.",
        "invalid_service_order_transition",
    )


async def update_order(
    session: AsyncSession,
    order: ServiceOrder,
    *,
    status: str | None,
    public_note: str | None,
    internal_note: str | None,
    fields_set: frozenset[str],
    request: "Request | None",
    now: datetime | None = None,
) -> str | None:
    """Apply what the operator sent (the caller commits). Returns the old status when it changed.

    A status change is audited, posts a message on the order's support request (closing it when
    the order is done or cancelled) and, for `done`, sets `support_until`. A note-only change
    writes nothing else.

    Raises:
        APIError 409 `invalid_service_order_transition`.
    """
    moment = now or datetime.now(UTC)
    if "public_note" in fields_set:
        order.public_note = (public_note or "").strip()[:NOTE_MAX] or None
    if "internal_note" in fields_set:
        order.internal_note = (internal_note or "").strip() or None
    changed_from: str | None = None
    if status is not None and status != order.status:
        if status not in TRANSITIONS[order.status]:
            raise _invalid_transition(order.status, status)
        changed_from = order.status
        order.status = status
        if status == "done":
            order.completed_at = moment
            order.support_until = support_until(moment, order.includes_support_months)
    order.updated_at = moment
    await session.flush()
    if changed_from is not None:
        await _post_status_message(session, order, request, moment)
        tenant = await session.get(Tenant, order.tenant_id)
        await record_audit(
            session,
            request,
            "billing.service_order_updated",
            tenant_id=order.tenant_id,
            tenant_label=tenant.name if tenant is not None else None,
            target_type="service_order",
            target_id=str(order.id),
            details={"order_id": str(order.id), "from": changed_from, "to": order.status},
        )
    return changed_from


async def _post_status_message(
    session: AsyncSession, order: ServiceOrder, request: "Request | None", moment: datetime
) -> None:
    if order.support_request_id is None:
        return
    row = await session.get(SupportRequest, order.support_request_id)
    if row is None:
        return
    body = f"Status: {STATUS_LABELS[order.status]}"
    if order.public_note:
        body += f" — {order.public_note}"
    if order.status == "done" and order.support_until is not None:
        body += f"\nIncluded support until {order.support_until:%d %b %Y}."
    final = order.status in ("done", "cancelled")
    await support_service.add_deployer_message(
        session,
        row,
        body=body,
        author_email=str(actor_from_request(request)[2])[:320],
        status="closed" if final else "answered",
        now=moment,
    )


# --------------------------------------------------------------------------- the e-mails


def _orders_url(dashboard_url: str) -> str:
    from callobuzz_rag.accounts.email.render import dashboard_link

    return dashboard_link(dashboard_url, "/plan")


async def notify_owners(
    session: AsyncSession,
    order: ServiceOrder,
    tenant: Tenant,
    *,
    enqueue: Enqueue,
    dashboard_url: str,
) -> int:
    """Tell the workspace's owners about a status change. Never raises; returns how many."""
    from callobuzz_rag.accounts.lifecycle import owner_emails

    sent = 0
    try:
        context = {
            "tenant_name": tenant.name,
            "service_name": order.name,
            "status_label": STATUS_LABELS[order.status],
            "public_note": order.public_note or "",
            "orders_url": _orders_url(dashboard_url),
        }
        for to in await owner_emails(session, tenant.id):
            enqueue("service_order_update", to, context, expires_s=EXPIRES_S)
            sent += 1
    except Exception:
        logger.warning("Service order mail was not queued", exc_info=True)
    return sent


async def notify_provider_of_new_orders(
    session_factory: Any,
    *,
    enqueue: Enqueue,
    support_email: str | None,
    superadmin_email: str | None,
    dashboard_url: str,
    limit: int = 50,
) -> int:
    """E-mail the provider's inbox about orders not yet announced; each is claimed once (an
    UPDATE on `provider_notified_at IS NULL`), so the request path and the hourly schedule never
    send the same order twice. Never raises; returns how many were sent."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.support.notify import deployer_inbox

    to = deployer_inbox(support_email, superadmin_email)
    if not to:
        return 0
    sent = 0
    try:
        async with session_factory() as session:
            rows = list(
                (
                    await session.execute(
                        select(ServiceOrder, Tenant.name)
                        .join(Tenant, Tenant.id == ServiceOrder.tenant_id)
                        .where(ServiceOrder.provider_notified_at.is_(None))
                        .order_by(ServiceOrder.created_at)
                        .limit(limit)
                    )
                ).all()
            )
            pending = [
                (order.id, order.name, order.owner_note or "", tenant_name)
                for order, tenant_name in rows
            ]
            for order_id, service_name, owner_note, tenant_name in pending:
                claimed = (
                    await session.execute(
                        update(ServiceOrder)
                        .where(
                            ServiceOrder.id == order_id,
                            ServiceOrder.provider_notified_at.is_(None),
                        )
                        .values(provider_notified_at=datetime.now(UTC))
                    )
                ).rowcount
                if not claimed:
                    continue
                # the claim is held (uncommitted) while the mail is queued: if queueing fails the
                # stamp is rolled back and the hourly run tries again
                try:
                    enqueue(
                        "service_order_update",
                        to,
                        {
                            "tenant_name": tenant_name,
                            "service_name": service_name,
                            "status_label": "New order",
                            "public_note": owner_note,
                            "orders_url": dashboard_link(
                                dashboard_url, "/admin/billing?tab=service-orders"
                            ),
                        },
                        expires_s=EXPIRES_S,
                    )
                except Exception:
                    await session.rollback()
                    logger.warning("New service order mail was not queued", exc_info=True)
                    continue
                await session.commit()
                sent += 1
    except Exception:
        logger.warning("Service order announcements failed", exc_info=True)
    return sent
