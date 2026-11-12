"""The business owner's add-on shop (P5): what a workspace can buy on top of its plan.

`GET /api/v1/billing/add-ons` lists the add-ons the deployer put in the shop (`sold_to_owners`),
with the price, what it comes to with GST, what it gives, and whether the workspace already has
it. `POST .../{add_on_id}/buy` buys one from the balance (a credit pack is paid online only: it
cannot be paid from the balance it adds to). Paying online is the payment flow (`/pay`, purpose
`add_on`). `POST .../grants/{grant_id}/cancel` stops a monthly add-on renewing. Buying and
cancelling are owners' (money); anyone in the workspace may look. Every route answers 404
`billing_disabled` while billing is off. The rules live in `billing/addons.py`.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import get_engine_settings
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.billing_statements import owner_workspace
from callobuzz_rag.api.routes.service_orders import announce_new_orders, workspace_of
from callobuzz_rag.api.schemas.billing_addons import ApplyAddOnRequest
from callobuzz_rag.api.schemas.billing_shop import (
    BuyAddOnRequest,
    BuyWith,
    GrantView,
    ShopItem,
    ShopList,
)
from callobuzz_rag.billing import accounts, addons, hooks, ledger
from callobuzz_rag.billing.byok import byok_status
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.invoicing.display import inclusive_total
from callobuzz_rag.billing.payments import factory
from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/billing", tags=["billing"], dependencies=[Depends(require_billing_enabled)]
)

KIND_LABELS = {
    "feature_unlock": "Your own AI key",
    "service": "Services",
    "credit_pack": "Credit packs",
    "extra_limit": "Extras",
    "question_pack": "Extras",
}


def grant_view(grant: AddOnGrant, now: datetime | None = None) -> GrantView:
    moment = now or datetime.now(UTC)
    return GrantView(
        id=grant.id,
        add_on_id=grant.add_on_id,
        kind=grant.kind,
        name=grant.name,
        summary=addons.grant_summary(grant),
        renews=grant.renews,
        starts_at=grant.starts_at,
        ends_at=grant.ends_at,
        cancelled_at=grant.cancelled_at,
        active=addons.grant_is_active(grant, moment),
    )


async def _sellable(session: AsyncSession, add_on_id: UUID, currency: str) -> AddOn:
    """The add-on if it is in the shop for this workspace; else the same 404 as an unknown one."""
    row = await session.get(AddOn, add_on_id)
    if (
        row is None
        or not row.is_active
        or not row.sold_to_owners
        or row.placeholder  # a seeded price the deployer has not set yet is not for sale
        or row.currency != currency
    ):
        raise APIError(404, "That add-on does not exist.", "add_on_not_found")
    return row


@router.get("/add-ons", response_model=ShopList)
async def list_shop(request: Request, session: AsyncSession = Depends(get_session)) -> ShopList:
    """What this workspace can buy: services, the own-key add-on and credit packs, with GST."""
    tenant_id = workspace_of(request)
    ctx = await accounts.load_context(session, tenant_id)
    if ctx.effective.exempt:
        return ShopList(items=[])
    currency = ctx.effective.currency
    engine = await get_engine_settings(request.app.state.session_factory)
    registered = bool(engine.invoicing.gstin)
    inclusive = bool(engine.invoicing.prices_include_gst)
    online = await factory.provider_for(request.app, session) is not None and currency == "INR"
    now = datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(AddOn)
                .where(
                    AddOn.is_active.is_(True),
                    AddOn.sold_to_owners.is_(True),
                    AddOn.placeholder.is_(False),
                    AddOn.currency == currency,
                )
                .order_by(AddOn.kind, AddOn.price_minor, AddOn.name)
            )
        )
        .scalars()
        .all()
    )
    # what the workspace holds now: a monthly add-on (also a service that renews) or an unlock
    held = [
        g
        for g in await addons.list_grants(session, tenant_id, active_only=False, now=now)
        if g.revoked_at is None
        and (g.ends_at is None or g.ends_at > now)
        and (g.renews or g.kind == "feature_unlock")
    ]
    by_add_on = {g.add_on_id: g for g in held}
    byok_allowed, _source = await byok_status(session, tenant_id, now=now)
    available = await ledger.available_minor(session, ctx.account)
    items = []
    for row in rows:
        grant = by_add_on.get(row.id)
        included = row.kind == "feature_unlock" and row.feature == "byok" and byok_allowed
        buy_with: list[BuyWith] = []
        if online:
            buy_with.append("online")
        if row.kind != "credit_pack" and not included:
            buy_with.append("balance")
        items.append(
            ShopItem(
                id=row.id,
                kind=row.kind,
                kind_label=KIND_LABELS.get(row.kind, "Extras"),
                name=row.name,
                description=row.description,
                gives=addons.summary(
                    kind=row.kind,
                    credit_minor=row.credit_minor,
                    bonus_pct=row.bonus_pct,
                    currency=row.currency,
                    limit_field=row.limit_field,
                    limit_delta=row.limit_delta,
                    questions=row.questions,
                    ends_at=(
                        addons.period_end(now)
                        if row.kind == "feature_unlock" and row.duration == "period"
                        else None
                    ),
                    feature=row.feature,
                    renews=row.renews,
                    includes_support_months=row.includes_support_months,
                ),
                currency=row.currency,
                price_minor=row.price_minor,
                inclusive_total_minor=inclusive_total(
                    row.price_minor, registered=registered, prices_include_gst=inclusive
                ),
                renews=row.renews,
                includes_support_months=row.includes_support_months,
                owned=grant is not None or included,
                owned_until=grant.ends_at if grant is not None else None,
                grant_id=grant.id if grant is not None else None,
                renewal_cancelled=grant is not None and grant.cancelled_at is not None,
                buy_with=[] if included else buy_with,
                affordable=not addons.past_the_pause_line(
                    ctx.effective.fields, available, row.price_minor
                ),
            )
        )
    return ShopList(items=items)


@router.post("/add-ons/{add_on_id}/buy", response_model=GrantView, status_code=201)
async def buy_add_on(
    add_on_id: UUID,
    body: BuyAddOnRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> GrantView:
    """Buy an add-on from the balance. A service opens an order the provider works on; a monthly
    add-on renews each period until you cancel it. Sending the same `idempotency_key` again returns
    the same purchase (200). Owners only."""
    tenant_id = owner_workspace(request)
    ctx = await accounts.load_context(session, tenant_id)
    await _sellable(session, add_on_id, ctx.effective.currency)
    grant, created = await addons.apply_add_on(
        session,
        tenant_id,
        ApplyAddOnRequest(
            add_on_id=add_on_id,
            payment="wallet",
            memo=body.note,
            idempotency_key=body.idempotency_key,
        ),
        request=request,
    )
    out = grant_view(grant)
    account_id = (await accounts.ensure_account(session, tenant_id)).id
    await session.commit()
    if created:
        if grant.entry_id is not None:
            try:
                await hooks.after_balance_change(request.app.state.session_factory, [account_id])
            except Exception:  # never fail a committed purchase because of a notification
                logger.warning("Balance hook failed after a shop purchase", exc_info=True)
        if grant.kind == "service":
            await announce_new_orders(request, session)
    if not created:
        response.status_code = 200
    return out


@router.post("/add-ons/grants/{grant_id}/cancel", response_model=GrantView)
async def cancel_add_on(
    grant_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> GrantView:
    """Stop a monthly add-on from renewing. It stays in force until the end of the period you
    paid for; nothing is charged again and nothing is refunded. Owners only."""
    tenant_id = owner_workspace(request)
    grant = await addons.cancel_renewal(session, tenant_id, grant_id, request=request)
    out = grant_view(grant)
    await session.commit()
    return out
