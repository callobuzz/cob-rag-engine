"""Self-service plan choice (P6): the owner's plan catalogue and plan change, and the public list
of plans a visitor can start on.

- `GET /api/v1/billing/plans`: the plans this workspace may choose (the deployer's `self_service`
  plans, plus its own), each with its price and the GST-inclusive total, what it includes, and
  what moving now would mean (charged now for an upgrade, the start day of a downgrade). Owners.
- `POST /api/v1/billing/plan` `{plan_id}`: move. Up: at once, the difference for the rest of the
  period charged from the balance (or, when it cannot cover it, waiting for a payment). Down: at
  the next renewal. Owners.
- `DELETE /api/v1/billing/plan/pending`: call off the owner's own scheduled or waiting change.
- `GET /api/v1/public/plans`: no credential; the sign-up page's chosen plan.

These routes work with billing off too (only plans with no fee can then be chosen). The rules are
`billing/plan_change.py`; the operator's own move (`POST /admin/tenants/{id}/plan`) is unchanged.
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import EngineSettings, read_engine_settings_row
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.routes.billing_statements import owner_workspace
from callobuzz_rag.api.schemas.billing_plans import (
    ChangePlanRequest,
    ChangePlanResponse,
    PendingPlanChange,
    PlanChoice,
    PlanChoiceList,
    PlanIncludes,
    PlanPrice,
    PublicPlan,
    PublicPlanList,
)
from callobuzz_rag.billing import plan_change
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.invoicing.display import inclusive_total
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])
public_router = APIRouter(prefix="/api/v1/public", tags=["public"])


def _includes(plan: Plan) -> PlanIncludes:
    return PlanIncludes(
        max_knowledge_bases=plan.max_knowledge_bases,
        monthly_questions=plan.monthly_questions,
        max_documents=plan.max_documents,
        max_widget_keys=plan.max_widget_keys,
        own_ai_key=bool(plan.byok_allowed),
    )


def _price(fields: PricingFields, engine: EngineSettings) -> PlanPrice:
    return PlanPrice(
        currency=fields.currency,
        subscription_fee_minor=fields.subscription_fee_minor,
        inclusive_total_minor=inclusive_total(
            fields.subscription_fee_minor,
            registered=bool(engine.invoicing.gstin),
            prices_include_gst=engine.invoicing.prices_include_gst,
        ),
        billing_period=fields.billing_period,
        included_credit_minor=fields.included_credit_minor,
        usage_mode=fields.usage_mode,
        per_question_minor=fields.per_question_minor,
    )


def _pay_amount(missing: int, engine: EngineSettings) -> int:
    if missing <= 0:
        return 0
    return plan_change.pay_amount(
        missing,
        minimum_minor=engine.payments.min_top_up_minor,
        registered=bool(engine.invoicing.gstin),
        prices_include_gst=engine.invoicing.prices_include_gst,
    )


async def _pay_online(request: Request, session: AsyncSession, currency: str) -> bool:
    from callobuzz_rag.billing.payments import factory

    if currency != "INR":
        return False
    try:
        return await factory.provider_for(request.app, session) is not None
    except Exception:  # payments misconfigured: the top-up request still works
        logger.warning("Payment provider lookup failed", exc_info=True)
        return False


def _ordered(plans: list[Plan], current: Plan | None) -> list[Plan]:
    out = list(plans)
    if current is not None and all(p.id != current.id for p in out):
        out.append(current)
    return out


async def _catalogue_billing_off(
    session: AsyncSession, tenant: Tenant, engine: EngineSettings
) -> PlanChoiceList:
    current = await session.get(Plan, tenant.plan_id)
    items: list[PlanChoice] = []
    for plan in _ordered(await plan_change.self_service_plans(session), current):
        fee, _row = await plan_change.plain_fee(session, plan)
        is_current = plan.id == tenant.plan_id
        items.append(
            PlanChoice(
                id=plan.id,
                name=plan.name,
                slug=plan_change.plan_slug(plan.name),
                description=plan.description,
                current=is_current,
                direction="current" if is_current else "switch",
                includes=_includes(plan),
                available=is_current or fee <= 0,
                unavailable_reason=None if is_current or fee <= 0 else "needs_billing",
            )
        )
    return PlanChoiceList(
        billing_on=False,
        currency=engine.billing.default_currency,
        current_plan_id=tenant.plan_id,
        can_change=not (tenant.is_operator or tenant.is_system),
        pay_online=False,
        items=items,
    )


def _pending_view(
    account: object, engine: EngineSettings, plans: dict[UUID, Plan]
) -> PendingPlanChange | None:
    from callobuzz_rag.billing.notice import ist_date

    raw = dict(getattr(account, "pending_change", None) or {})
    plan_id = raw.get("plan_id")
    if not plan_id:
        return None
    plan = plans.get(UUID(str(plan_id)))
    name = plan.name if plan is not None else "another plan"
    if raw.get("awaiting_payment"):
        return PendingPlanChange(
            kind="awaiting_payment",
            plan_id=UUID(str(plan_id)),
            plan_name=name,
            amount_due_minor=int(raw.get("quoted_minor") or 0),
            expires_at=datetime.fromisoformat(str(raw["expires_at"]))
            if raw.get("expires_at")
            else None,
        )
    at = getattr(account, "pending_change_at", None)
    return PendingPlanChange(
        kind="scheduled",
        plan_id=UUID(str(plan_id)),
        plan_name=name,
        effective_on=ist_date(at) if at is not None else None,
        by_provider=raw.get("by") != "owner",
    )


@router.get("/plans", response_model=PlanChoiceList)
async def list_plan_choices(
    request: Request, session: AsyncSession = Depends(get_session)
) -> PlanChoiceList:
    """The plans you can move to, with prices (GST included beside them), what each includes and
    what moving now would cost or when it would start. Owners only."""
    from callobuzz_rag.billing import accounts, ledger

    tenant_id = owner_workspace(request)
    engine = (await read_engine_settings_row(session))[0]
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise APIError(404, "Workspace not found.", "tenant_not_found")
    if not billing_enabled():
        return await _catalogue_billing_off(session, tenant, engine)
    ctx = await accounts.load_context(session, tenant_id)
    currency = ctx.effective.currency
    now = datetime.now(UTC)
    choosable = await plan_change.self_service_plans(session)
    by_id = {p.id: p for p in choosable}
    by_id[ctx.plan.id] = ctx.plan
    pending_raw = dict(ctx.account.pending_change or {})
    if pending_raw.get("plan_id"):
        target = await session.get(Plan, UUID(str(pending_raw["plan_id"])))
        if target is not None:
            by_id[target.id] = target
    if ctx.effective.exempt:
        await session.commit()
        return PlanChoiceList(
            billing_on=True,
            currency=currency,
            current_plan_id=ctx.plan.id,
            can_change=False,
            pay_online=False,
            items=[],
        )
    available = await ledger.available_minor(session, ctx.account)
    items: list[PlanChoice] = []
    renewal_due = False
    for plan in _ordered(choosable, ctx.plan):
        try:
            q = await plan_change.quote(session, ctx, plan, now=now)
        except APIError as exc:
            if exc.code != "plan_change_renewal_due":
                raise
            renewal_due = True
            break
        pricing = q.target.pricing
        is_current = plan.id == ctx.plan.id
        if not is_current and pricing is not None and pricing.currency != currency:
            continue  # a plan in another currency cannot take this workspace's balance
        fields = ctx.effective.fields if is_current else q.target.fields
        reason = "price_not_set" if q.target.placeholder and not is_current else None
        items.append(
            PlanChoice(
                id=plan.id,
                name=plan.name,
                slug=plan_change.plan_slug(plan.name),
                description=plan.description,
                current=is_current,
                direction=q.direction,
                price=_price(fields, engine),
                includes=_includes(plan),
                due_now_minor=q.charge_minor,
                credit_now_minor=q.credit_minor,
                first_period=q.first_period,
                effective_on=q.effective_on if not is_current else None,
                affordable=plan_change.covers(ctx.effective.fields, available, q.charge_minor),
                available=reason is None,
                unavailable_reason=reason,
            )
        )
    items.sort(key=lambda i: (i.price.subscription_fee_minor if i.price else 0, i.name))
    pending = _pending_view(ctx.account, engine, by_id)
    if pending is not None and pending.kind == "awaiting_payment":
        missing = plan_change.shortfall(ctx.effective.fields, available, pending.amount_due_minor)
        pending.shortfall_minor = missing
        pending.pay_amount_minor = _pay_amount(missing, engine)
    await session.commit()  # the account may have been created by this read
    return PlanChoiceList(
        billing_on=True,
        currency=currency,
        current_plan_id=ctx.plan.id,
        can_change=not renewal_due,
        pay_online=await _pay_online(request, session, currency),
        renews_on=ctx.account.next_charge_on,
        pending=pending,
        items=[] if renewal_due else items,
    )


@router.post("/plan", response_model=ChangePlanResponse)
async def change_plan(
    body: ChangePlanRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> ChangePlanResponse:
    """Move this workspace to another plan you may choose. An upgrade applies now and the
    difference for the rest of this period is taken from the balance (when the balance cannot
    cover it, the move waits for your payment: `awaiting_payment` with the amount to pay). A
    downgrade starts at your next renewal; nothing is refunded. Choosing your current plan
    changes nothing (`current`). Owners only. 404 `plan_not_found`; 409 `billing_exempt`,
    `plan_change_pending_from_provider`, `plan_change_needs_billing`, `plan_not_ready`,
    `plan_change_renewal_due`, `currency_mismatch`."""
    from callobuzz_rag.billing import accounts, hooks, ledger

    tenant_id = owner_workspace(request)
    engine = (await read_engine_settings_row(session))[0]
    outcome = await plan_change.change_plan(session, tenant_id, body.plan_id, request=request)
    account_id = None
    missing = outcome.shortfall_minor
    currency = engine.billing.default_currency
    if billing_enabled():
        ctx = await accounts.load_context(session, tenant_id)
        account_id = ctx.account.id
        currency = ctx.effective.currency
        if outcome.status == "awaiting_payment":
            available = await ledger.available_minor(session, ctx.account)
            missing = plan_change.shortfall(
                ctx.effective.fields, available, outcome.amount_due_minor
            )
    await session.commit()
    if (
        account_id is not None
        and outcome.status == "applied"
        and (outcome.charged_minor or outcome.credit_minor)
    ):
        try:
            await hooks.after_balance_change(request.app.state.session_factory, [account_id])
        except Exception:  # never fail a committed change because of a notification
            logger.warning("Balance hook failed after a plan change", exc_info=True)
    awaiting = outcome.status == "awaiting_payment"
    return ChangePlanResponse(
        status=outcome.status,
        plan_id=outcome.plan.id,
        plan_name=outcome.plan.name,
        charged_minor=outcome.charged_minor,
        credit_minor=outcome.credit_minor,
        effective_on=outcome.effective_on,
        amount_due_minor=outcome.amount_due_minor,
        shortfall_minor=missing if awaiting else 0,
        pay_amount_minor=_pay_amount(missing, engine) if awaiting else 0,
        pay_online=await _pay_online(request, session, currency) if awaiting else False,
        expires_at=outcome.expires_at,
    )


@router.delete("/plan/pending", status_code=204)
async def cancel_plan_change(
    request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Call off your scheduled move (or one waiting for a payment); you stay on your plan.
    A change your provider scheduled can only be changed by them. Owners only. 404
    `pending_change_not_found`, 409 `plan_change_pending_from_provider`."""
    tenant_id = owner_workspace(request)
    if not billing_enabled():
        raise APIError(404, "No plan change is waiting.", "pending_change_not_found")
    await plan_change.cancel_change(session, tenant_id, request=request)
    await session.commit()
    return Response(status_code=204)


@public_router.get("/plans", response_model=PublicPlanList)
async def list_public_plans(session: AsyncSession = Depends(get_session)) -> PublicPlanList:
    """The plans a new business can start on (the deployer's self-service plans), for the
    sign-up page's `?plan=` link. Prices only while billing is on; a plan whose price is not
    set yet is left out."""
    from callobuzz_rag.billing.seed_saas import PLACEHOLDER_BY

    engine = (await read_engine_settings_row(session))[0]
    on = billing_enabled()
    items: list[PublicPlan] = []
    for plan in await plan_change.self_service_plans(session):
        fee, row = await plan_change.plain_fee(session, plan)
        if row is not None and row.created_by == PLACEHOLDER_BY:
            continue
        price = None
        if on and row is not None:
            from callobuzz_rag.billing.pricing import fields_from_row

            price = _price(fields_from_row(row), engine)
        if not on and fee > 0:
            continue  # a plan with a fee cannot be started while payments are off
        items.append(
            PublicPlan(
                id=plan.id,
                name=plan.name,
                slug=plan_change.plan_slug(plan.name),
                description=plan.description,
                free=fee <= 0,
                price=price,
            )
        )
    items.sort(
        key=lambda i: (0 if i.free else i.price.subscription_fee_minor if i.price else 0, i.name)
    )
    return PublicPlanList(items=items)
