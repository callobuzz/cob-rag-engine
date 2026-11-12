"""Self-service plan choice and change (P6): a business picks or moves its own plan.

Only plans the deployer marked `self_service` can be chosen, and only by an owner. The rules:

- **Up** (the new plan's fee for the period is higher): it applies at once. The difference for the
  rest of the current period is charged from the balance as ONE ledger entry
  (`plan-change:<tenant>:<plan>:<period start>`), together with the matching share of the larger
  included credit (`plan-change-credit:...`), both prorated by whole days with integer floor
  division (`prorate`), so a retry never charges twice and nothing is ever a float. A workspace
  with no billing schedule yet pays the new plan's first period, under the very keys the hourly
  schedule would use (`sub:<day>`, `inc:<day>`), so the two can never both charge it. When the
  balance cannot cover it, nothing is charged: the change waits for the payment
  (`pending_change` with `awaiting_payment`, no date) and `settle_awaiting`, run from the balance
  hook after every committed money change, applies it once the money is in. A wait ends after
  `AWAITING_DAYS` or at the period end, whichever comes first.
- **Down** (lower fee): scheduled for the end of the current period through TV3's pending change
  (`pending_change_at` = the renewal day), applied by `notice.apply_due_account_changes`. Never
  mid-period, never a refund.
- **Same fee**: it applies at once, nothing is charged.

An owner's move is the business asking for it, so TV3's notice wait never applies; the audit row
says so (`reason`). A change the deployer scheduled (an override raise or a plan move) is the
deployer's word: while one waits, the owner cannot change plans (409
`plan_change_pending_from_provider`). With billing off only plans
with no fee can be chosen, at once; a plan with a fee answers 409 `plan_change_needs_billing`.
Lower plan limits block new additions only (the existing rule). Nothing here commits.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

from sqlalchemy import select

from callobuzz_rag.accounts.audit import Actor, actor_from_request, record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.pricing import PricingFields, combine, fields_from_row
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.billing.accounts import BillingContext

AWAITING_DAYS: Final = 7
OWNER_REASON: Final = "The business chose this plan itself from its dashboard."
Direction = Literal["current", "upgrade", "downgrade", "switch"]
Status = Literal["applied", "awaiting_payment", "scheduled", "current"]


# ---------------------------------------------------------------- pure rules


def plan_slug(name: str) -> str:
    """`Pro` -> `pro`, `Growth Plus` -> `growth-plus`: the sign-up link's readable plan name."""
    return re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:64]


def prorate(difference_minor: int, total_days: int, remaining_days: int) -> int:
    """The share of `difference_minor` for `remaining_days` of a `total_days` period: integer,
    floored (the business never pays a part of a paisa), never negative, never more than the
    difference. A period of no days (bad data) charges nothing."""
    if difference_minor <= 0 or total_days <= 0 or remaining_days <= 0:
        return 0
    remaining = min(remaining_days, total_days)
    return difference_minor * remaining // total_days


def split_difference(
    difference_minor: int, total_days: int, remaining_days: int
) -> tuple[int, int]:
    """`(now, rest)`: what is charged for the rest of the period and the part already used up,
    which together are exactly the difference (zero and zero when it is not positive)."""
    now = prorate(difference_minor, total_days, remaining_days)
    return now, max(difference_minor, 0) - now


def per_period(fee_minor: int, plan_period: str, account_period: str) -> int:
    """A plan's fee for one of the workspace's own periods: a yearly fee is a twelfth of it for a
    monthly workspace, a monthly fee twelve times for a yearly one. Integer."""
    if plan_period == account_period:
        return fee_minor
    if plan_period == "yearly":
        return fee_minor // 12
    return fee_minor * 12


def classify(old_fee_minor: int, new_fee_minor: int) -> Direction:
    if new_fee_minor > old_fee_minor:
        return "upgrade"
    if new_fee_minor < old_fee_minor:
        return "downgrade"
    return "switch"


@dataclass(frozen=True)
class Window:
    """The workspace's current billing period in UTC dates: `[start, end)`, `end` the renewal."""

    start: date
    end: date
    today: date

    @property
    def total_days(self) -> int:
        return (self.end - self.start).days

    @property
    def remaining_days(self) -> int:
        return max(0, min((self.end - self.today).days, self.total_days))


def window_of(account: BillingAccount, period: str, today: date) -> Window | None:
    """The current period of a workspace on a billing schedule; None without one.

    Raises:
        APIError 409 `plan_change_renewal_due`: the renewal is due and the hourly schedule has
            not charged it yet (a move now could skip it); it clears within the hour.
    """
    from callobuzz_rag.billing.notice import current_period_start

    if account.next_charge_on is None:
        return None
    if account.next_charge_on <= today:
        raise APIError(
            409,
            "Your plan is renewing right now. Please try again in an hour.",
            "plan_change_renewal_due",
        )
    start = current_period_start(account.next_charge_on, today, period, account.billing_day or 1)
    return Window(start, account.next_charge_on, today)


def shortfall(fields: PricingFields, available_minor: int, charge_minor: int) -> int:
    """The least the balance must receive so `charge_minor` fits under the pause rule
    (`covers`): a prepaid balance must stay above zero and the grace line, a postpaid one above
    its credit limit."""
    if charge_minor <= 0:
        return 0
    if fields.payment_type == "prepaid":
        floor = max(0, 1 - fields.grace_minor)
    else:
        floor = 1 - fields.credit_limit_minor
    return max(0, charge_minor + floor - available_minor)


def pay_amount(
    shortfall_minor: int, *, minimum_minor: int, registered: bool, prices_include_gst: bool
) -> int:
    """What to type on the pay page so the balance receives at least `shortfall_minor`: the
    amount itself, or, when the deployer's prices include GST, the smallest total whose taxable
    part covers it; never below the smallest top-up."""
    from callobuzz_rag.billing.invoicing import gst
    from callobuzz_rag.billing.invoicing.display import inclusive_total

    amount = max(shortfall_minor, 0)
    if registered and prices_include_gst and amount > 0:
        total = inclusive_total(amount, registered=True, prices_include_gst=False)
        while (
            min(gst.split_inclusive(total, kind, "27").taxable_minor for kind in ("intra", "inter"))
            < amount
        ):
            total += 1
        amount = total
    amount = -(-amount // 100) * 100  # whole rupees (or dollars) on the pay page
    return max(amount, minimum_minor)


# ---------------------------------------------------------------- reading plans


@dataclass(frozen=True)
class Target:
    """A plan a workspace could move to, with what it would pay on it."""

    plan: Plan
    pricing: PlanPricing | None  # the version in force for the period the move lands in
    fields: PricingFields  # that version with the workspace's own terms on top
    placeholder: bool


async def self_service_plans(session: "AsyncSession") -> list[Plan]:
    rows = (
        await session.execute(
            select(Plan)
            .where(
                Plan.self_service.is_(True),
                Plan.is_active.is_(True),
                Plan.is_internal.is_(False),
            )
            .order_by(Plan.name)
        )
    ).scalars()
    return list(rows.all())


async def resolve_ref(session: "AsyncSession", ref: str | None) -> Plan | None:
    """The self-service plan a sign-up link names (`?plan=<id or slug>`); None for anything else
    (unknown, inactive, not self-service, ambiguous), so the caller falls back to the default."""
    text = (ref or "").strip()
    if not text:
        return None
    found = await self_service_plans(session)
    try:
        wanted = uuid.UUID(text)
    except ValueError:
        matches = [p for p in found if plan_slug(p.name) == plan_slug(text)]
        return matches[0] if len(matches) == 1 else None
    return next((p for p in found if p.id == wanted), None)


async def target_for(
    session: "AsyncSession", ctx: "BillingContext", plan: Plan, *, at: datetime
) -> Target:
    """What `ctx`'s workspace would pay on `plan` for the period starting at `at`."""
    from callobuzz_rag.billing.notice import plan_versions, version_in_force
    from callobuzz_rag.billing.pricing import overrides_from_stored
    from callobuzz_rag.billing.seed_saas import PLACEHOLDER_BY

    row = version_in_force(await plan_versions(session, plan.id), at)
    base = (
        fields_from_row(row) if row is not None else PricingFields(currency=ctx.effective.currency)
    )
    fields = combine(
        base,
        overrides_from_stored(ctx.account.overrides),
        currency=ctx.effective.currency,
        exempt=False,
    ).fields
    return Target(plan, row, fields, row is not None and row.created_by == PLACEHOLDER_BY)


async def plain_fee(session: "AsyncSession", plan: Plan) -> tuple[int, PlanPricing | None]:
    """The plan's current list fee (billing off, sign-up): no workspace terms on top."""
    from callobuzz_rag.billing.pricing import current_pricing

    row = await current_pricing(session, plan.id)
    return (row.subscription_fee_minor if row is not None else 0), row


# ---------------------------------------------------------------- the quote


@dataclass(frozen=True)
class ChangeQuote:
    target: Target
    direction: Direction
    charge_minor: int  # taken from the balance now (an upgrade)
    credit_minor: int  # included credit granted with it
    first_period: bool  # no schedule yet: the new plan's first full period
    period_start: date
    period_end: date  # the renewal: a downgrade's day
    effective_on: date

    @property
    def key(self) -> str:
        return f"{self.target.plan.id}:{self.period_start.isoformat()}"


def _ist_midnight(day: date) -> datetime:
    from callobuzz_rag.billing.notice import ist_midnight

    return ist_midnight(day)


async def quote(
    session: "AsyncSession", ctx: "BillingContext", plan: Plan, *, now: datetime
) -> ChangeQuote:
    """What moving `ctx`'s workspace to `plan` means now. Writes nothing."""
    from callobuzz_rag.billing.subscriptions import add_period

    today = now.astimezone(UTC).date()
    current = ctx.effective.fields
    period = current.billing_period
    window = window_of(ctx.account, period, today)
    start = window.start if window is not None else today
    target = await target_for(session, ctx, plan, at=_ist_midnight(start))
    if plan.id == ctx.plan.id:
        end = window.end if window is not None else today
        return ChangeQuote(target, "current", 0, 0, False, start, end, today)
    new = target.fields
    new_fee = per_period(new.subscription_fee_minor, new.billing_period, period)
    new_credit = per_period(new.included_credit_minor, new.billing_period, period)
    direction = classify(current.subscription_fee_minor, new_fee)
    if window is None:
        end = add_period(today, new.billing_period, ctx.account.billing_day or 1)
        if direction == "downgrade":
            from callobuzz_rag.billing.notice import applies_from

            day = applies_from(ctx.account, now, period)
            return ChangeQuote(target, direction, 0, 0, False, today, day, day)
        bearing = new.subscription_fee_minor > 0 or new.included_credit_minor > 0
        if direction == "upgrade" and bearing:
            return ChangeQuote(
                target,
                direction,
                new.subscription_fee_minor,
                new.included_credit_minor,
                True,
                today,
                end,
                today,
            )
        return ChangeQuote(target, direction, 0, 0, False, today, end, today)
    if direction == "downgrade":
        return ChangeQuote(target, direction, 0, 0, False, window.start, window.end, window.end)
    charge = prorate(
        new_fee - current.subscription_fee_minor, window.total_days, window.remaining_days
    )
    credit = prorate(
        new_credit - current.included_credit_minor, window.total_days, window.remaining_days
    )
    return ChangeQuote(target, direction, charge, credit, False, window.start, window.end, today)


def covers(fields: PricingFields, available_minor: int, charge_minor: int) -> bool:
    """Whether the balance can take `charge_minor` without reaching the pause line (the shop's
    rule). Nothing to charge always fits."""
    from callobuzz_rag.billing.addons import past_the_pause_line

    return charge_minor <= 0 or not past_the_pause_line(fields, available_minor, charge_minor)


# ---------------------------------------------------------------- pending changes


def owner_pending(account: BillingAccount) -> bool:
    return bool(account.pending_change and account.pending_change.get("by") == "owner")


def awaiting(account: BillingAccount) -> bool:
    return bool(account.pending_change and account.pending_change.get("awaiting_payment"))


def provider_pending(account: BillingAccount) -> bool:
    """A change the deployer scheduled is waiting (an override raise or a plan move)."""
    return bool(account.pending_change) and not owner_pending(account)


def awaiting_expired(account: BillingAccount, now: datetime) -> bool:
    raw = (account.pending_change or {}).get("expires_at")
    if not raw:
        return True
    try:
        return datetime.fromisoformat(str(raw)) <= now
    except ValueError:
        return True


def _refuse_provider_pending() -> APIError:
    return APIError(
        409,
        "A change your provider set up is waiting to start. Please contact them to change "
        "your plan.",
        "plan_change_pending_from_provider",
    )


def _needs_billing() -> APIError:
    return APIError(
        409,
        "This plan has a fee, and payments are not set up here. Please contact us to move to it.",
        "plan_change_needs_billing",
    )


def _not_choosable() -> APIError:
    return APIError(404, "That plan cannot be chosen here.", "plan_not_found")


def _exempt() -> APIError:
    return APIError(
        409, "This workspace is never billed, so its plan is set by the operator.", "billing_exempt"
    )


# ---------------------------------------------------------------- applying


@dataclass(frozen=True)
class ChangeOutcome:
    status: Status
    plan: Plan
    charged_minor: int = 0
    credit_minor: int = 0
    effective_on: date | None = None
    amount_due_minor: int = 0  # awaiting payment: what the balance must take
    shortfall_minor: int = 0  # awaiting payment: what is missing
    expires_at: datetime | None = None


def _memo(plan: Plan, q: ChangeQuote) -> str:
    if q.first_period:
        return f"{plan.name} plan, first period"[:200]
    return f"Move to {plan.name}: rest of this period"[:200]


async def _apply_upgrade(
    session: "AsyncSession",
    ctx: "BillingContext",
    q: ChangeQuote,
    *,
    request: "Request | None",
    actor: Actor | None,
    via: str,
    now: datetime,
) -> ChangeOutcome:
    """Charge the quote (if anything) and move the plan, in the caller's transaction."""
    from callobuzz_rag.accounts import plans
    from callobuzz_rag.billing import ledger
    from callobuzz_rag.billing.subscriptions import add_period

    account = ctx.account
    plan = q.target.plan
    fields = q.target.fields
    tenant_key = f"{ctx.tenant.id}:{q.key}"
    entries: list[ledger.NewEntry] = []
    if q.first_period:
        day = q.period_start.isoformat()
        if q.charge_minor > 0:
            entries.append(
                ledger.NewEntry(
                    ledger.EntryType.SUBSCRIPTION_CHARGE,
                    -q.charge_minor,
                    f"sub:{day}",
                    reference_type="subscription_period",
                    reference_id=day,
                    memo_public=_memo(plan, q),
                )
            )
        if q.credit_minor > 0:
            entries.append(
                ledger.NewEntry(
                    ledger.EntryType.INCLUDED_CREDIT,
                    q.credit_minor,
                    f"inc:{day}",
                    reference_type="subscription_period",
                    reference_id=day,
                    memo_public="Included credit",
                    expires_at=_utc_midnight(q.period_end)
                    if fields.included_credit_expires
                    else None,
                )
            )
        account.next_charge_on = add_period(
            q.period_start, fields.billing_period, account.billing_day or 1
        )
        if account.period_anchor is None:
            account.period_anchor = q.period_start
    else:
        if q.charge_minor > 0:
            entries.append(
                ledger.NewEntry(
                    ledger.EntryType.SUBSCRIPTION_CHARGE,
                    -q.charge_minor,
                    f"plan-change:{tenant_key}",
                    reference_type="plan_change",
                    reference_id=str(plan.id),
                    memo=f"Prorated difference to {plan.name} for {q.period_start.isoformat()}",
                    memo_public=_memo(plan, q),
                )
            )
        if q.credit_minor > 0:
            entries.append(
                ledger.NewEntry(
                    ledger.EntryType.INCLUDED_CREDIT,
                    q.credit_minor,
                    f"plan-change-credit:{tenant_key}",
                    reference_type="plan_change",
                    reference_id=str(plan.id),
                    memo_public=f"Extra included credit ({plan.name})"[:200],
                    expires_at=_utc_midnight(q.period_end)
                    if fields.included_credit_expires
                    else None,
                )
            )
    previous = ctx.plan
    if entries:
        who = actor if actor is not None else actor_from_request(request)
        await ledger.append_entries(session, account.id, entries, actor=who, now=now)
    await plans.set_tenant_plan(session, ctx.tenant, plan.id, request=request)
    account.pending_change = None
    account.pending_change_at = None
    account.updated_at = now
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.plan_change_applied",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "from": {"id": str(previous.id), "name": previous.name},
            "to": {"id": str(plan.id), "name": plan.name},
            "charged_minor": q.charge_minor,
            "credit_minor": q.credit_minor,
            "period_start": q.period_start.isoformat(),
            "via": via,
            "reason": OWNER_REASON,
        },
        actor=actor,
    )
    return ChangeOutcome(
        "applied", plan, q.charge_minor, q.credit_minor, effective_on=now.astimezone(UTC).date()
    )


def _utc_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


async def _hold_for_payment(
    session: "AsyncSession",
    ctx: "BillingContext",
    q: ChangeQuote,
    *,
    available_minor: int,
    request: "Request | None",
    actor: Actor | None,
    now: datetime,
) -> ChangeOutcome:
    account = ctx.account
    plan = q.target.plan
    expires = now + timedelta(days=AWAITING_DAYS)
    if not q.first_period:
        expires = min(expires, _ist_midnight(q.period_end))
    missing = shortfall(ctx.effective.fields, available_minor, q.charge_minor)
    account.pending_change = {
        "plan_id": str(plan.id),
        "by": "owner",
        "awaiting_payment": True,
        "quoted_minor": q.charge_minor,
        "expires_at": expires.isoformat(),
    }
    account.pending_change_at = None
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.plan_change_awaiting_payment",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "to": {"id": str(plan.id), "name": plan.name},
            "amount_minor": q.charge_minor,
            "expires_at": expires.isoformat(),
            "reason": OWNER_REASON,
        },
        actor=actor,
    )
    return ChangeOutcome(
        "awaiting_payment",
        plan,
        amount_due_minor=q.charge_minor,
        shortfall_minor=missing,
        expires_at=expires,
    )


async def _schedule_downgrade(
    session: "AsyncSession",
    ctx: "BillingContext",
    q: ChangeQuote,
    *,
    request: "Request | None",
) -> ChangeOutcome:
    """TV3's pending change, dated the renewal day (IST midnight, so it is applied before the
    renewal is charged at the new plan's price)."""
    account = ctx.account
    plan = q.target.plan
    at = _ist_midnight(q.effective_on)
    account.pending_change = {"plan_id": str(plan.id), "by": "owner"}
    account.pending_change_at = at
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.plan_change_scheduled",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "from": {"id": str(ctx.plan.id), "name": ctx.plan.name},
            "to": {"id": str(plan.id), "name": plan.name},
            "effective_at": at.isoformat(),
            "reason": OWNER_REASON,
        },
    )
    return ChangeOutcome("scheduled", plan, effective_on=q.effective_on)


async def _locked_context(session: "AsyncSession", tenant_id: uuid.UUID) -> "BillingContext":
    """The billing context with the account row locked and the tenant row re-read, so two
    requests for one workspace serialise and the second sees the first's plan."""
    from callobuzz_rag.billing import accounts

    await accounts.account_for_update(session, tenant_id)
    await session.execute(
        select(Tenant).where(Tenant.id == tenant_id).execution_options(populate_existing=True)
    )
    return await accounts.load_context(session, tenant_id)


async def change_plan(
    session: "AsyncSession",
    tenant_id: uuid.UUID,
    plan_id: uuid.UUID,
    *,
    request: "Request | None",
    now: datetime | None = None,
) -> ChangeOutcome:
    """An owner's request to move to `plan_id` (see the module docstring). Does not commit.

    Raises:
        APIError 404 `plan_not_found` (not a plan the business may choose), 409
        `billing_exempt`, `plan_change_pending_from_provider`, `plan_change_needs_billing`,
        `plan_not_ready` (its price is a placeholder), `plan_change_renewal_due`,
        `currency_mismatch`.
    """
    from callobuzz_rag.accounts import plans
    from callobuzz_rag.billing import ledger

    moment = now or datetime.now(UTC)
    plan = await session.get(Plan, plan_id)
    if plan is None or not plan.is_active or plan.is_internal:
        raise _not_choosable()
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise _not_choosable()
    if tenant.is_operator or tenant.is_system:
        raise _exempt()
    if not billing_enabled():
        if tenant.plan_id == plan.id:
            return ChangeOutcome("current", plan)
        if not plan.self_service:
            raise _not_choosable()
        if (await plain_fee(session, plan))[0] > 0:
            raise _needs_billing()
        await plans.set_tenant_plan(session, tenant, plan.id, request=request)
        return ChangeOutcome("applied", plan, effective_on=moment.astimezone(UTC).date())
    ctx = await _locked_context(session, tenant_id)
    if ctx.effective.exempt:
        raise _exempt()
    if ctx.plan.id == plan.id:
        return ChangeOutcome("current", plan)
    if not plan.self_service:
        raise _not_choosable()
    if provider_pending(ctx.account):
        raise _refuse_provider_pending()
    q = await quote(session, ctx, plan, now=moment)
    if q.target.placeholder:
        raise APIError(
            409, "This plan's price is not set yet. Please contact us.", "plan_not_ready"
        )
    from callobuzz_rag.billing.pricing import check_plan_currency

    await check_plan_currency(session, tenant_id, plan.id)
    if q.direction == "downgrade":
        return await _schedule_downgrade(session, ctx, q, request=request)
    available = await ledger.available_minor(session, ctx.account)
    if not covers(ctx.effective.fields, available, q.charge_minor):
        return await _hold_for_payment(
            session, ctx, q, available_minor=available, request=request, actor=None, now=moment
        )
    return await _apply_upgrade(
        session, ctx, q, request=request, actor=None, via="balance", now=moment
    )


async def cancel_change(
    session: "AsyncSession", tenant_id: uuid.UUID, *, request: "Request | None"
) -> dict[str, Any]:
    """Drop the owner's own scheduled or waiting change (audited by TV3's `cancel_pending`).

    Raises:
        APIError 404 `pending_change_not_found`, 409 `plan_change_pending_from_provider`.
    """
    from callobuzz_rag.billing import notice

    ctx = await _locked_context(session, tenant_id)
    if not ctx.account.pending_change:
        raise APIError(404, "No plan change is waiting.", "pending_change_not_found")
    if not owner_pending(ctx.account):
        raise _refuse_provider_pending()
    return await notice.cancel_pending(session, ctx, request)


# ---------------------------------------------------------------- after a payment


async def settle_awaiting(
    session_factory: Any, account_ids: Any, *, now: datetime | None = None
) -> list[uuid.UUID]:
    """Apply each waiting upgrade the balance now covers (or drop one whose time is up), in its
    own transaction under the account lock. Called by the balance hook before its checks, so a
    top-up that pays for a plan moves it at once. Returns the accounts whose plan moved."""
    import logging

    from callobuzz_rag.billing import ledger, notice

    log = logging.getLogger(__name__)
    moment = now or datetime.now(UTC)
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        return []
    async with session_factory() as session:
        waiting = list(
            (
                await session.execute(
                    select(BillingAccount.id, BillingAccount.tenant_id).where(
                        BillingAccount.id.in_(ids),
                        BillingAccount.pending_change.has_key("awaiting_payment"),
                    )
                )
            ).all()
        )
    moved: list[uuid.UUID] = []
    for account_id, tenant_id in waiting:
        try:
            async with session_factory() as session:
                ctx = await _locked_context(session, tenant_id)
                account = ctx.account
                if not awaiting(account):
                    continue
                raw = dict(account.pending_change or {})
                plan = await session.get(Plan, uuid.UUID(str(raw.get("plan_id"))))
                reason: str | None = None
                if awaiting_expired(account, moment):
                    reason = "payment_not_received"
                elif plan is None or not plan.is_active or not plan.self_service:
                    reason = "plan_unavailable"
                if reason is None and plan is not None:
                    q = await quote(session, ctx, plan, now=moment)
                    if q.direction != "upgrade" or q.charge_minor > int(
                        raw.get("quoted_minor") or 0
                    ):
                        reason = "price_changed"
                    else:
                        available = await ledger.available_minor(session, account)
                        if not covers(ctx.effective.fields, available, q.charge_minor):
                            continue  # still waiting for the money
                        await _apply_upgrade(
                            session,
                            ctx,
                            q,
                            request=None,
                            actor=ledger.SYSTEM_ACTOR,
                            via="payment",
                            now=moment,
                        )
                        await session.commit()
                        moved.append(account_id)
                        continue
                await notice.cancel_pending(session, ctx, None)
                await record_audit(
                    session,
                    None,
                    "billing.plan_change_awaiting_payment",
                    tenant_id=ctx.tenant.id,
                    tenant_label=ctx.tenant.name,
                    target_type="billing_account",
                    target_id=str(account.id),
                    details={"tenant_id": str(ctx.tenant.id), "ended": reason},
                    actor=ledger.SYSTEM_ACTOR,
                )
                await session.commit()
        except Exception:
            log.warning("Settling the waiting plan change of %s failed", account_id, exc_info=True)
    return moved


# ---------------------------------------------------------------- sign-up


@dataclass(frozen=True)
class SignupChoice:
    status: Literal["active", "awaiting_payment", "on_approval"]
    plan: Plan
    amount_due_minor: int = 0


async def apply_signup_choice(
    session: "AsyncSession",
    tenant: Tenant,
    plan_id: uuid.UUID | None,
    *,
    request: "Request | None",
    now: datetime | None = None,
) -> SignupChoice | None:
    """Put a new (or just approved) workspace on the plan its owner chose at sign-up. A plan
    with no fee applies at once; a plan with a fee waits for the owner's payment (nothing is
    charged until they pay). None when the choice no longer stands (unknown, not self-service,
    a fee with billing off): the workspace keeps the default plan. Does not commit."""
    from callobuzz_rag.accounts import plans

    moment = now or datetime.now(UTC)
    if plan_id is None:
        return None
    plan = await session.get(Plan, plan_id)
    if plan is None or not plan.is_active or plan.is_internal or not plan.self_service:
        return None
    if tenant.plan_id == plan.id:
        return SignupChoice("active", plan)
    fee, row = await plain_fee(session, plan)
    if fee <= 0:
        await plans.set_tenant_plan(session, tenant, plan.id, request=request)
        return SignupChoice("active", plan)
    if not billing_enabled() or tenant.is_operator or tenant.is_system:
        return None
    from callobuzz_rag.billing.seed_saas import PLACEHOLDER_BY

    if row is not None and row.created_by == PLACEHOLDER_BY:
        return None
    ctx = await _locked_context(session, tenant.id)
    if ctx.effective.exempt:
        return None
    q = await quote(session, ctx, plan, now=moment)
    out = await _hold_for_payment(
        session,
        ctx,
        q,
        available_minor=0,
        request=request,
        actor=None,
        now=moment,
    )
    return SignupChoice("awaiting_payment", plan, out.amount_due_minor)
