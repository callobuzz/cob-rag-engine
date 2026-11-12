"""A workspace's own billing view (BIL2): `GET /api/v1/billing/account`.

For people and keys of one workspace: an owner session or a `tk_` key sees everything on the
plan; a member session sees the terms but no money unless the plan sets `members_see_money`.
A knowledge-base key (`ck_`) is refused (403 `scope_forbidden`); the admin uses the admin
routes (403 `no_workspace`). The markup, the operator's AI cost and the admin's notes are never
part of this view.
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_owner, resolve_scope
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.billing import (
    BillingAccountView,
    GstDisplay,  # P2 invoices
    PlanRef,
    SetSpendCapRequest,  # TV3 spend cap
    SpendCapOut,  # TV3 spend cap
    StatementRef,  # BIL6 summary
    UsagePeriodOut,  # BIL6 summary
)
from callobuzz_rag.api.schemas.billing_addons import ActiveAddOn  # BIL5 add-ons
from callobuzz_rag.api.schemas.billing_ledger import (  # BIL3 ledger
    LedgerEntryView,
    LedgerViewListResponse,
)
from callobuzz_rag.api.schemas.billing_shop import OwnKeyStatus  # P5 plans
from callobuzz_rag.billing import accounts, addons, alerts, enforcement, ledger  # BIL3 ledger
from callobuzz_rag.billing import summary as billing_summary  # BIL6 summary
from callobuzz_rag.billing.byok import own_key_status, plans_with_byok  # P5 plans
from callobuzz_rag.billing.config import require_billing_enabled
from callobuzz_rag.db.models.billing_account import BillingAccount  # BIL3 ledger
from callobuzz_rag.db.models.wallet_ledger import WalletLedger  # BIL3 ledger

router = APIRouter(
    prefix="/api/v1/billing", tags=["billing"], dependencies=[Depends(require_billing_enabled)]
)


def account_view(
    ctx: accounts.BillingContext,
    *,
    is_member: bool,
    available_minor: int = 0,
    add_ons: list[ActiveAddOn] | None = None,  # BIL5 add-ons
    state: dict[str, Any] | None = None,  # BIL4 state
    summary: dict[str, Any] | None = None,  # BIL6 summary
) -> BillingAccountView:
    """The owner's view of `ctx`; money fields are None for a member unless the plan allows."""
    f = ctx.effective.fields
    visible = not is_member or f.members_see_money
    state = state or {}  # BIL4 state

    def money(value: int) -> int | None:
        return value if visible else None

    return BillingAccountView(
        tenant_id=ctx.tenant.id,
        plan=PlanRef(id=ctx.plan.id, name=ctx.plan.name),
        currency=ctx.effective.currency,
        money_visible=visible,
        payment_type=f.payment_type,
        usage_mode=f.usage_mode,
        byok_mode=f.byok_mode,
        subscription_fee_minor=money(f.subscription_fee_minor),
        billing_period=f.billing_period,
        per_question_minor=money(f.per_question_minor),
        byok_per_question_minor=money(f.byok_per_question_minor),
        overage=f.overage,
        overage_per_question_minor=money(f.overage_per_question_minor),
        included_credit_minor=money(f.included_credit_minor),
        credit_limit_minor=money(f.credit_limit_minor),
        exempt=ctx.effective.exempt,
        # BIL3 ledger
        balance_minor=money(ctx.account.balance_minor),
        available_minor=money(available_minor),
        # /BIL3 ledger
        add_ons=add_ons or [],  # BIL5 add-ons
        **state,  # BIL4 state
        **(summary or {}),  # BIL6 summary
    )


@router.get("/account", response_model=BillingAccountView)
async def get_billing_account(
    request: Request, session: AsyncSession = Depends(get_session)
) -> BillingAccountView:
    """How this workspace pays: its plan's pricing with the customer's own terms on top. The
    account is created on first read. Members see no money unless the plan allows it."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Billing belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the admin billing pages.", "no_workspace"
        )
    ctx = await accounts.load_context(session, scope.tenant_id)
    available = await ledger.available_minor(session, ctx.account)  # BIL3 ledger
    # BIL5 add-ons
    in_force = [
        ActiveAddOn(name=g.name, kind=g.kind, summary=addons.grant_summary(g), ends_at=g.ends_at)
        for g in await addons.active_grants(session, scope.tenant_id)
    ]
    # /BIL5 add-ons
    is_member = getattr(request.state, "role", None) == "member"
    extra = await owner_summary(session, ctx, is_member=is_member)  # BIL6 summary
    # P5 plans: the own-key rule in plain facts (the dashboard locks or opens the key form)
    own_key = await own_key_status(session, scope.tenant_id)
    extra["byok"] = OwnKeyStatus(
        allowed=own_key[0],
        key_saved=own_key[1],
        source=own_key[2],
        plans_with_byok=[] if own_key[0] else await plans_with_byok(session),
    )
    # /P5 plans
    # P2 invoices
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row

    seller = (await read_engine_settings_row(session))[0].invoicing
    gst = GstDisplay(registered=bool(seller.gstin), prices_include_gst=seller.prices_include_gst)
    # /P2 invoices
    await session.commit()
    view = account_view(
        ctx,
        is_member=is_member,
        available_minor=available,  # BIL3 ledger
        add_ons=in_force,  # BIL5 add-ons
        state=live_state(ctx, available),  # BIL4 state
        summary=extra,  # BIL6 summary
    )
    view.gst = gst  # P2 invoices
    return view


# BIL6 summary
async def owner_summary(
    session: AsyncSession, ctx: accounts.BillingContext, *, is_member: bool
) -> dict[str, Any]:
    """This month's usage, the next charge and the statements list. A member sees how many
    questions were asked, never an amount or a statement unless the plan lets members see money
    (and statements stay owner-only either way)."""
    visible = not is_member or ctx.effective.fields.members_see_money
    usage = await billing_summary.usage_this_period(session, ctx.account)
    refs = [] if is_member else await billing_summary.recent_statements(session, ctx.account)
    # TV3 summary
    trust = await billing_summary.trust_fields(session, ctx, usage, visible=visible)
    # /TV3 summary
    return {
        **trust,  # TV3 summary
        "billed": enforcement.stored_status(ctx.effective, ctx.account) != "not_billed",
        "usage_this_period": UsagePeriodOut(
            questions=usage.questions,
            charged_minor=usage.charged_minor if visible else None,
            unpriced=usage.unpriced if visible else None,
        ),
        "next_charge_on": ctx.account.next_charge_on if visible else None,
        "trial_ends_at": ctx.account.trial_ends_at if visible else None,
        "statements": [StatementRef(**r) for r in refs],
    }


# /BIL6 summary


# TV3 spend cap
@router.put("/spend-cap", response_model=SpendCapOut)
async def set_spend_cap(
    body: SetSpendCapRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SpendCapOut:
    """Set (or remove, with `cap_minor: null`) this workspace's monthly spending cap on usage.
    At the cap the assistant switches to the provider's fallback model, or pauses and shows
    your contact options, until the month ends; you are told at 80 % and at the cap. Owners
    only. 409 `spend_cap_not_billed`, 422 `spend_cap_too_high` (extra `max_minor`)."""
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.billing import spend_cap

    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Billing belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the admin billing pages.", "no_workspace"
        )
    require_owner(request)
    ctx = await accounts.load_context(session, scope.tenant_id)
    engine = (await read_engine_settings_row(session))[0]
    max_minor = engine.trust.spend_cap_max_minor.get(ctx.effective.currency)
    await spend_cap.set_cap(session, ctx, body.cap_minor, max_minor=max_minor, request=request)
    usage = await billing_summary.usage_this_period(session, ctx.account)
    await session.commit()
    return SpendCapOut(
        **spend_cap.cap_view(ctx.account.spend_cap_minor, usage.charged_minor, max_minor)
    )


# /TV3 spend cap


# BIL4 state
def live_state(ctx: accounts.BillingContext, available: int) -> dict[str, Any]:
    """Paused or low, from the live balance (usage not yet posted counts): the banner's input."""
    snap = alerts.build_snapshot(ctx.account, ctx.tenant, ctx.effective, available)
    if snap is None or not (snap.state.billed or snap.state.paused):
        return {}
    reason = snap.state.reason if snap.state.paused else None
    return {
        "paused": snap.state.paused,
        "pause_reason": reason if reason in ("prepaid_empty", "credit_limit", "forced") else None,
        "alert_level": snap.level if snap.level else None,
    }


# /BIL4 state


# BIL3 ledger
@router.get("/ledger", response_model=LedgerViewListResponse)
async def list_my_transactions(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> LedgerViewListResponse:
    """This workspace's transactions, newest first (top-ups, usage, subscription, credit).
    Owners only. Each line carries the description written for the customer; the super admin's
    notes and who made an entry are never part of it."""
    scope = resolve_scope(request)
    if scope.corpus_id is not None:
        raise APIError(
            403, "Billing belongs to a workspace, not to one knowledge base.", "scope_forbidden"
        )
    if scope.is_admin or scope.tenant_id is None:
        raise APIError(
            403, "There is no workspace here: use the admin billing pages.", "no_workspace"
        )
    require_owner(request)
    account = (
        await session.execute(
            select(BillingAccount).where(BillingAccount.tenant_id == scope.tenant_id)
        )
    ).scalar_one_or_none()
    if account is None:
        return LedgerViewListResponse(items=[], total=0)
    total = (
        await session.execute(
            select(func.count())
            .select_from(WalletLedger)
            .where(WalletLedger.account_id == account.id)
        )
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(WalletLedger)
                .where(WalletLedger.account_id == account.id)
                .order_by(WalletLedger.seq.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return LedgerViewListResponse(
        items=[
            LedgerEntryView(
                id=r.id,
                entry_type=r.entry_type,
                amount_minor=r.amount_minor,
                currency=r.currency,
                balance_after_minor=r.balance_after_minor,
                description=r.memo_public or "",
                created_at=r.created_at,
            )
            for r in rows
        ],
        total=int(total),
    )


# /BIL3 ledger
