"""The super admin's billing pages: plan pricing and customers' billing accounts (BIL2).

`/api/v1/admin/billing/plans/{plan_id}/pricing` (+ `/history`): how customers on a plan pay,
versioned. `/api/v1/admin/billing/accounts*`: one account per workspace, created on first open,
with the customer's own terms. Admin only; every route answers 404 `billing_disabled` while
`RAG_BILLING_ENABLED=false`. The rules live in `billing/`.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import plans
from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.accounts.settings_store import read_engine_settings_row
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing import (
    BillingAccountListResponse,
    BillingAccountOut,
    BillingAccountRow,
    PendingChangeOut,  # TV3 notice
    PlanPricingHistoryResponse,
    PlanPricingOut,
    PlanPricingRequest,
    PlanRef,
    PricingPreviewOut,  # TV3 notice
    UpdateBillingAccountRequest,
)
from callobuzz_rag.billing import accounts, enforcement, notice, pricing  # BIL4 state, TV3
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.billing.pricing import PaymentType, PricingFields, TenantNotFoundError
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/billing",
    tags=["billing"],
    dependencies=[Depends(require_admin), Depends(require_billing_enabled)],
)


def _pricing_out(plan: Plan, row: PlanPricing | None, *, default_currency: str) -> PlanPricingOut:
    if row is None:
        fields = PricingFields(currency=default_currency)  # type: ignore[arg-type]
        return PlanPricingOut(**fields.model_dump(), plan_id=plan.id, locked=plan.is_internal)
    return PlanPricingOut(
        **pricing.fields_from_row(row).model_dump(),
        plan_id=plan.id,
        version=row.version,
        is_current=row.is_current,
        locked=plan.is_internal,
        created_at=row.created_at,
        created_by=row.created_by,
        effective_at=row.effective_at,  # TV3 notice
    )


async def _default_currency(session: AsyncSession) -> str:
    return (await read_engine_settings_row(session))[0].billing.default_currency


@router.get("/plans/{plan_id}/pricing", response_model=PlanPricingOut)
async def get_plan_pricing(
    plan_id: UUID, session: AsyncSession = Depends(get_session)
) -> PlanPricingOut:
    """The pricing in force for a plan. A plan without pricing returns the defaults with
    `version: null`: nothing is charged and nobody is paused."""
    plan = await plans.get_plan(session, plan_id)
    row = await pricing.current_pricing(session, plan.id)
    out = _pricing_out(plan, row, default_currency=await _default_currency(session))
    # TV3 notice
    coming = notice.pending_scheduled(await notice.plan_versions(session, plan.id))
    if coming:
        out.scheduled = _pricing_out(plan, coming[-1], default_currency=coming[-1].currency)
    # /TV3 notice
    return out


# TV3 notice
async def _notice_settings(session: AsyncSession) -> tuple[int, str]:
    """(price_change_notice_days, product name) from the engine settings."""
    engine = (await read_engine_settings_row(session))[0]
    return engine.trust.price_change_notice_days, engine.branding.product_name


async def _announce(
    request: Request,
    session: AsyncSession,
    plan: Plan,
    old: PlanPricing | None,
    new: PlanPricing,
    kind: str,
) -> None:
    """After the commit: tell the businesses on the plan. Never fails the saved change. A plan's
    first pricing (`old` None) replaces the free defaults and is told just the same."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        _, product = await _notice_settings(session)
        settings = getattr(request.app.state, "settings", None)
        base = settings.dashboard_url if settings is not None else "http://localhost:8090"
        await notice.announce_change(
            session,
            plan=plan,
            old=old,
            new=new,
            kind=kind,
            enqueue=enqueue_email,
            product_name=product,
            dashboard_url=base,
        )
    except Exception:
        logger.warning("Price change notices were not sent", exc_info=True)


# /TV3 notice


@router.put("/plans/{plan_id}/pricing", response_model=PlanPricingOut)
async def set_plan_pricing(
    plan_id: UUID,
    body: PlanPricingRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PlanPricingOut:
    """Replace a plan's pricing with a new version. Every field is sent (an omitted one takes
    its default). The response lists what changed from the previous version. The Internal plan
    is never billed (409 `pricing_locked`).

    A change that makes any business pay more is announced first: send `apply: "scheduled"`
    and, optionally, `effective_at` no sooner than the notice period (422
    `price_change_notice_too_short`); `apply: "now"` for such a change is 422
    `price_change_not_lower`. Each business pays the new price from its next renewal after
    that date. A plan nobody is on, and a change that costs nobody more, may start at once."""
    plan = await plans.get_plan(session, plan_id)
    fields = PricingFields.model_validate(body.model_dump(exclude={"apply", "effective_at"}))
    notice_days, _ = await _notice_settings(session)  # TV3 notice
    saved = await notice.save_with_notice(
        session,
        plan,
        fields,
        created_by=actor_from_request(request)[2],
        apply=body.apply,
        effective_at=body.effective_at,
        notice_days=notice_days,
        now=datetime.now(UTC),
    )
    row, changed = saved.row, saved.changed
    if saved.scheduled:  # TV3 notice
        await record_audit(
            session,
            request,
            "billing.price_change_scheduled",
            target_type="plan",
            target_id=str(plan.id),
            details={
                "plan_id": str(plan.id),
                "version": row.version,
                "effective_at": row.effective_at.isoformat() if row.effective_at else None,
                "impact": saved.impact,
                "workspaces": saved.workspaces,
            },
        )
    else:
        await record_audit(
            session,
            request,
            "billing.pricing_updated",
            target_type="plan",
            target_id=str(plan.id),
            details={"plan_id": str(plan.id), "version": row.version, "changed": changed},
        )
    for gone in saved.replaced:  # TV3 notice
        await record_audit(
            session,
            request,
            "billing.price_change_cancelled",
            target_type="plan",
            target_id=str(plan.id),
            details={"plan_id": str(plan.id), "version": gone.version},
        )
    await session.commit()
    # TV3 notice: after the commit the businesses are told (best effort)
    for gone in saved.replaced:
        await _announce(request, session, plan, saved.previous, gone, "price_change_cancelled")
    if saved.scheduled:
        await _announce(request, session, plan, saved.previous, row, "price_change")
    elif saved.impact == "lower" and saved.workspaces:
        await _announce(request, session, plan, saved.previous, row, "plan_lower")
    # /TV3 notice
    shown = saved.previous if saved.scheduled and saved.previous is not None else row
    out = _pricing_out(plan, shown, default_currency=fields.currency)
    out.changed = changed
    out.impact = saved.impact  # TV3 notice
    out.workspaces = saved.workspaces  # TV3 notice
    if saved.scheduled:  # TV3 notice
        out.scheduled = _pricing_out(plan, row, default_currency=fields.currency)
    return out


# TV3 notice
@router.post("/plans/{plan_id}/pricing/preview", response_model=PricingPreviewOut)
async def preview_plan_pricing(
    plan_id: UUID,
    body: PlanPricingRequest,
    session: AsyncSession = Depends(get_session),
) -> PricingPreviewOut:
    """What saving this pricing would mean: whether any business would pay more (`impact`),
    which fields change, how many businesses are on the plan and the earliest day a higher price
    may start. Writes nothing."""
    plan = await plans.get_plan(session, plan_id)
    fields = PricingFields.model_validate(body.model_dump(exclude={"apply", "effective_at"}))
    notice_days, _ = await _notice_settings(session)
    preview = await notice.preview_change(
        session, plan, fields, notice_days=notice_days, now=datetime.now(UTC)
    )
    return PricingPreviewOut(
        impact=preview.impact,
        changed=preview.changed,
        workspaces=preview.workspaces,
        earliest_effective_at=preview.earliest_effective_at,
    )


@router.delete("/plans/{plan_id}/pricing/scheduled", status_code=204)
async def cancel_scheduled_pricing(
    plan_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Cancel the price change scheduled for a plan. The businesses are told it will not
    happen. 404 `scheduled_pricing_not_found` when nothing is scheduled."""
    plan = await plans.get_plan(session, plan_id)
    current = await pricing.current_pricing(session, plan.id)
    gone = await notice.cancel_scheduled(session, plan)
    for row in gone:
        await record_audit(
            session,
            request,
            "billing.price_change_cancelled",
            target_type="plan",
            target_id=str(plan.id),
            details={"plan_id": str(plan.id), "version": row.version},
        )
    await session.commit()
    for row in gone:
        await _announce(request, session, plan, current, row, "price_change_cancelled")
    return Response(status_code=204)


# /TV3 notice


@router.get("/plans/{plan_id}/pricing/history", response_model=PlanPricingHistoryResponse)
async def get_plan_pricing_history(
    plan_id: UUID, session: AsyncSession = Depends(get_session)
) -> PlanPricingHistoryResponse:
    """Every version of a plan's pricing, newest first. Read-only."""
    plan = await plans.get_plan(session, plan_id)
    rows = await pricing.pricing_history(session, plan.id)
    return PlanPricingHistoryResponse(
        items=[_pricing_out(plan, r, default_currency=r.currency) for r in rows]
    )


# ---------------------------------------------------------------- customers' accounts


def _tenant_not_found() -> APIError:
    return APIError(404, "That workspace does not exist.", "tenant_not_found")


def account_out(
    ctx: accounts.BillingContext, pending: PendingChangeOut | None = None
) -> BillingAccountOut:
    acc, eff = ctx.account, ctx.effective
    return BillingAccountOut(
        tenant_id=ctx.tenant.id,
        tenant_name=ctx.tenant.name,
        plan=PlanRef(id=ctx.plan.id, name=ctx.plan.name),
        currency=eff.currency,
        exempt=eff.exempt,
        pricing_version=ctx.pricing.version if ctx.pricing is not None else None,
        effective=eff.fields,
        overrides=pricing.overrides_from_stored(acc.overrides).set_fields(),
        overridden=sorted(eff.overridden),
        limit_overrides=dict(acc.limit_overrides or {}),
        pause_override=acc.pause_override,
        billing_day=acc.billing_day,
        period_anchor=acc.period_anchor,
        next_charge_on=acc.next_charge_on,
        trial_ends_at=acc.trial_ends_at,
        balance_minor=acc.balance_minor,
        ledger_seq=acc.ledger_seq,
        currency_locked=acc.ledger_seq > 0,
        notes=acc.notes,
        created_at=acc.created_at,
        updated_at=acc.updated_at,
        # BIL4 state
        status=enforcement.stored_status(eff, acc),
        pause_reason=enforcement.stored_reason(eff, acc),
        alert_level=acc.alert_level,
        pending_change=pending,  # TV3 notice
        # /BIL4 state
    )


@router.get("/accounts", response_model=BillingAccountListResponse)
async def list_billing_accounts(
    q: str | None = Query(None, max_length=200, description="Part of the customer's name"),
    payment_type: PaymentType | None = Query(None, description="prepaid or postpaid (effective)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> BillingAccountListResponse:
    """Every customer's billing at a glance, by name. The COB system workspace is never
    listed; the operator's own workspace is, marked `exempt`. A customer without an account yet
    shows the plan's terms (`has_account: false`)."""
    rows, total = await accounts.list_rows(
        session, q=q, payment_type=payment_type, offset=offset, limit=limit
    )
    return BillingAccountListResponse(items=[BillingAccountRow(**row) for row in rows], total=total)


@router.get("/accounts/{tenant_id}", response_model=BillingAccountOut)
async def get_billing_account(
    tenant_id: UUID, session: AsyncSession = Depends(get_session)
) -> BillingAccountOut:
    """One customer's billing account with their effective terms and what they override. The
    account is created on first open."""
    try:
        ctx = await accounts.load_context(session, tenant_id)
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    await session.commit()
    return account_out(ctx, await _pending_view(session, ctx))  # TV3 notice


@router.patch("/accounts/{tenant_id}", response_model=BillingAccountOut)
async def update_billing_account(
    tenant_id: UUID,
    body: UpdateBillingAccountRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> BillingAccountOut:
    """Change a customer's terms. `overrides` sets individual pricing fields (`null` removes
    an override so the plan's value applies again); `limit_overrides` replaces their limits.
    The currency is locked once the customer has a transaction (409 `currency_locked`). The
    operator's own workspace accepts only `notes` (409 `billing_exempt`)."""
    try:
        await accounts.account_for_update(session, tenant_id)
        ctx = await accounts.load_context(session, tenant_id)
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    changes = {
        name: getattr(body, name)
        for name in body.model_fields_set
        if name not in ("business_agreed", "reason")  # TV3 notice: not account fields
    }
    # TV3 notice: a raise waits for its notice period, unless the business asked for it
    held = None
    if changes.get("overrides") is not None:
        notice_days, product = await _notice_settings(session)
        held = await notice.hold_overrides(
            session,
            ctx,
            changes["overrides"],
            agreed=body.business_agreed,
            reason=body.reason,
            notice_days=notice_days,
            now=datetime.now(UTC),
            request=request,
        )
        if held is not None:
            changes = {k: v for k, v in changes.items() if k != "overrides"}
        else:  # applied at once: an earlier held raise of the same fields must not undo it
            await notice.drop_stale_overrides(session, ctx, changes["overrides"], request)
    # /TV3 notice
    changed = await accounts.update_account(session, ctx, changes, request=request)
    if changed:
        ctx = await accounts.load_context(session, tenant_id)  # the terms after the change
    await session.commit()
    if held is not None:  # TV3 notice: after the commit the owners are told (best effort)
        await _tell_owners(request, session, ctx, held, product)
    if changed:  # BIL4 billing: the override or new terms may pause or resume the workspace
        await _recheck(request, ctx.account.id)
    return account_out(ctx, await _pending_view(session, ctx))


# TV3 notice
async def _pending_view(
    session: AsyncSession, ctx: accounts.BillingContext
) -> PendingChangeOut | None:
    """The workspace's pending change with what it will change, for the admin's card."""
    acc = ctx.account
    effect = await notice.pending_effect(session, ctx)
    if effect is None or acc.pending_change_at is None:
        return None
    new, target = effect
    raw = dict(acc.pending_change or {})
    return PendingChangeOut(
        effective_at=acc.pending_change_at,
        plan=PlanRef(id=target.id, name=target.name) if target is not None else None,
        overrides=raw.get("overrides"),
        changes=notice.describe_changes(ctx.effective.fields, new),
    )


async def _tell_owners(
    request: Request,
    session: AsyncSession,
    ctx: accounts.BillingContext,
    held: notice.HeldChange,
    product: str,
) -> None:
    """After the commit: e-mail the owners about the pending change. Never fails the change."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        settings = getattr(request.app.state, "settings", None)
        base = settings.dashboard_url if settings is not None else "http://localhost:8090"
        await notice.notify_pending(
            session,
            ctx,
            held,
            enqueue=enqueue_email,
            product_name=product,
            dashboard_url=base,
        )
    except Exception:
        logger.warning("Pending change notice was not sent", exc_info=True)


@router.delete("/accounts/{tenant_id}/pending-change", status_code=204)
async def cancel_pending_change(
    tenant_id: UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Drop the change waiting for a workspace's next renewal. 404 `pending_change_not_found`
    when there is none."""
    try:
        await accounts.account_for_update(session, tenant_id)
        ctx = await accounts.load_context(session, tenant_id)
    except TenantNotFoundError as exc:
        raise _tenant_not_found() from exc
    await notice.cancel_pending(session, ctx, request)
    await session.commit()
    return Response(status_code=204)


# /TV3 notice


# BIL4 billing
async def _recheck(request: Request, account_id: UUID) -> None:
    """After the commit: pause and resume transitions, audited as the admin who changed the
    terms. Never fails the committed change."""
    from callobuzz_rag.billing import hooks

    try:
        await hooks.after_balance_change(
            getattr(request.app.state, "session_factory", None),
            [account_id],
            actor=actor_from_request(request),
        )
    except Exception:
        import logging

        logging.getLogger(__name__).warning("Balance hook failed after a PATCH", exc_info=True)


# /BIL4 billing
