"""The super admin's add-ons (BIL5): the catalogue, and applying or revoking one for a customer.

`/api/v1/admin/billing/add-ons*` is the catalogue; `/api/v1/admin/billing/accounts/{tenant_id}/
grants*` applies an add-on to a workspace (the money goes through the ledger, the limit through a
grant) and revokes a grant. Admin only; every route answers 404 `billing_disabled` while
`RAG_BILLING_ENABLED=false`. Every change is audited in the same transaction. The rules live in
`billing/addons.py` and `billing/limits.py`.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing_addons import (
    AddOnListResponse,
    AddOnOut,
    ApplyAddOnRequest,
    CreateAddOnRequest,
    GrantListResponse,
    GrantOut,
    RevokeGrantRequest,
    UpdateAddOnRequest,
)
from callobuzz_rag.billing import accounts, addons, hooks
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.pricing import TenantNotFoundError
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["billing"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)


def add_on_out(row: AddOn) -> AddOnOut:
    return AddOnOut.model_validate(row, from_attributes=True)


def grant_out(row: AddOnGrant, now: datetime | None = None) -> GrantOut:
    moment = now or datetime.now(UTC)
    return GrantOut(
        **{
            f: getattr(row, f)
            for f in (
                "id",
                "tenant_id",
                "add_on_id",
                "kind",
                "name",
                "currency",
                "price_minor",
                "credit_minor",
                "bonus_pct",
                "limit_field",
                "limit_delta",
                "questions",
                # P5 plans
                "feature",
                "renews",
                "includes_support_months",
                "sac_code",
                "cancelled_at",
                "renewed_through",
                # /P5 plans
                "payment",
                "entry_id",
                "starts_at",
                "ends_at",
                "revoked_at",
                "revoked_by",
                "revoke_memo",
                "granted_by",
                "memo",
                "created_at",
            )
        },
        active=addons.grant_is_active(row, moment),
        summary=addons.grant_summary(row),
    )


def _tenant_not_found() -> APIError:
    return APIError(404, "That workspace does not exist.", "tenant_not_found")


# ---------------------------------------------------------------------------- catalogue


@router.get("/add-ons", response_model=AddOnListResponse)
async def list_add_ons(
    include_inactive: bool = Query(False, description="Also the deactivated ones"),
    session: AsyncSession = Depends(get_session),
) -> AddOnListResponse:
    """The add-on catalogue: credit packs, extra limits and question packs."""
    rows = await addons.list_add_ons(session, include_inactive=include_inactive)
    return AddOnListResponse(items=[add_on_out(r) for r in rows])


@router.post("/add-ons", response_model=AddOnOut, status_code=201)
async def create_add_on(
    body: CreateAddOnRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AddOnOut:
    """Add one to the catalogue. A credit pack needs `credit_minor` (and may carry a bonus and an
    expiry); an extra limit a `limit_field`, `limit_delta` and `duration`; a question pack
    `questions`. Names are unique (409 `add_on_name_taken`)."""
    row = await addons.create_add_on(session, body, request=request)
    out = add_on_out(row)
    await session.commit()
    return out


@router.patch("/add-ons/{add_on_id}", response_model=AddOnOut)
async def update_add_on(
    add_on_id: UUID,
    body: UpdateAddOnRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AddOnOut:
    """Change an add-on (price, fields, `is_active`). Customers who already have it keep what
    they were given."""
    row = await addons.get_add_on(session, add_on_id)
    await addons.update_add_on(
        session, row, body.model_dump(include=body.model_fields_set), request=request
    )
    out = add_on_out(row)
    await session.commit()
    return out


@router.delete("/add-ons/{add_on_id}", status_code=204)
async def delete_add_on(
    add_on_id: UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete an add-on that was never given to anyone; otherwise 409 `add_on_in_use` (deactivate
    it instead)."""
    row = await addons.get_add_on(session, add_on_id)
    await addons.delete_add_on(session, row, request=request)
    await session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------------------- grants


@router.get("/accounts/{tenant_id}/grants", response_model=GrantListResponse)
async def list_grants(
    tenant_id: UUID,
    active_only: bool = Query(False, description="Only the add-ons raising a limit now"),
    session: AsyncSession = Depends(get_session),
) -> GrantListResponse:
    """The add-ons given to a customer, newest first (history included)."""
    if await session.get(Tenant, tenant_id) is None:
        raise _tenant_not_found()
    now = datetime.now(UTC)
    rows = await addons.list_grants(session, tenant_id, active_only=active_only, now=now)
    return GrantListResponse(items=[grant_out(r, now) for r in rows])


@router.post("/accounts/{tenant_id}/grants", response_model=GrantOut, status_code=201)
async def apply_add_on(
    tenant_id: UUID,
    body: ApplyAddOnRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> GrantOut:
    """Apply an add-on to a customer. A credit pack adds its credit (plus bonus) to the balance;
    an extra limit or question pack raises the customer's limit until the end of the month or
    until revoked. `payment`: `external` (paid outside, the default), `free`, or `wallet` (the price
    leaves the balance; extra limits and question packs only). Sending the same `idempotency_key`
    again returns the same grant (200)."""
    try:
        grant, created = await addons.apply_add_on(session, tenant_id, body, request=request)
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    out = grant_out(grant)
    account_id = (await accounts.ensure_account(session, tenant_id)).id
    await session.commit()
    if created and grant.entry_id is not None:
        factory = getattr(request.app.state, "session_factory", None)
        try:
            await hooks.after_balance_change(factory, [account_id])
        except Exception:  # never fail a committed grant because of a notification
            logger.warning("Balance hook failed after an add-on grant", exc_info=True)
    if not created:
        response.status_code = 200  # a replay of the same request
    return out


@router.post("/accounts/{tenant_id}/grants/{grant_id}/revoke", response_model=GrantOut)
async def revoke_grant(
    tenant_id: UUID,
    grant_id: UUID,
    request: Request,
    body: RevokeGrantRequest | None = None,
    session: AsyncSession = Depends(get_session),
) -> GrantOut:
    """End an extra limit or question pack now. Money is never returned automatically: add a
    refund or an adjustment if some is due. A credit pack cannot be revoked (409
    `grant_not_revocable`)."""
    try:
        grant = await addons.revoke_grant(
            session, tenant_id, grant_id, body.memo if body else None, request=request
        )
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    out = grant_out(grant)
    await session.commit()
    return out
