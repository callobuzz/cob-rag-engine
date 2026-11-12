"""Price changes with notice (TV3): what a change means for a business, when it may start, and
which pricing version a workspace pays in its current period.

The rule the whole module serves: a business never pays a raised price inside the period in
which the raise was announced. A raise is stored as a *scheduled* pricing version
(`plan_pricing.effective_at`), and a workspace pays the newest version whose `effective_at` is not
after the start of its current billing period (`pricing_in_force`), so every workspace moves to the
new price at its own next renewal, never in the middle of a period.

Everything with a decision in it is pure and tested without a database; the functions that read
rows are thin. Money is integer minor units. Dates are Indian Standard Time at the edges (an
`effective_at` is an IST midnight) and UTC dates in the billing schedule, as everywhere in billing.
"""

import calendar
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Final, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.ledger import currency_frozen
from callobuzz_rag.billing.money import Money
from callobuzz_rag.billing.pricing import (
    PricingFields,
    changed_fields,
    fields_from_row,
    lock_accounts_for_currency,
    pricing_locked,
    save_pricing,
)
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

IST: Final = timezone(timedelta(hours=5, minutes=30), "IST")  # India has no daylight saving

Impact = Literal["lower", "same", "higher", "mixed"]
_IMPACT_RANK: Final[dict[str, int]] = {"lower": 0, "same": 1, "higher": 2, "mixed": 3}

# Plain words for a business owner, in the order a change is listed.
CHANGE_LABELS: Final[dict[str, str]] = {
    "subscription_fee_minor": "Plan fee",
    "per_question_minor": "Price per question",
    "byok_per_question_minor": "Price per question with your own key",
    "overage_per_question_minor": "Price per question over your limit",
    "included_credit_minor": "Credit included each period",
    "credit_limit_minor": "Credit limit",
    "grace_minor": "Grace amount",
}

_HIGHER_WHEN_UP: Final = (
    "subscription_fee_minor",
    "per_question_minor",
    "byok_per_question_minor",
    "overage_per_question_minor",
    "markup_bp",
)
_HIGHER_WHEN_DOWN: Final = ("included_credit_minor", "credit_limit_minor", "grace_minor")
_MIXED_WHEN_CHANGED: Final = (
    "usage_mode",
    "byok_mode",
    "billing_period",
    "payment_type",
    "currency",
    "overage_mode",
)
# overage moves that cost the business more (or limit it): block -> charge/allow follows the
# plan text; allow -> charge and allow -> block are never good news either.
_OVERAGE_WORSE: Final = {
    ("block", "charge"),
    ("block", "allow"),
    ("allow", "charge"),
    ("allow", "block"),
}
_OVERAGE_BETTER: Final = {("charge", "block"), ("charge", "allow")}


def impact(old: PricingFields, new: PricingFields) -> Impact:
    """What `new` means for a business that pays `old` today: `lower`, `same`, `higher`, or
    `mixed` (some fields better and some worse, or a change of kind that cannot be compared)."""
    before, after = old.model_dump(), new.model_dump()
    worse = better = mixed = False
    for name in _HIGHER_WHEN_UP:
        if after[name] > before[name]:
            worse = True
        elif after[name] < before[name]:
            better = True
    for name in _HIGHER_WHEN_DOWN:
        if after[name] < before[name]:
            worse = True
        elif after[name] > before[name]:
            better = True
    for name in _MIXED_WHEN_CHANGED:
        mixed = mixed or after[name] != before[name]
    move = (before["overage"], after["overage"])
    if move in _OVERAGE_WORSE:
        worse = True
    elif move in _OVERAGE_BETTER:
        better = True
    if before["included_credit_expires"] != after["included_credit_expires"]:
        if after["included_credit_expires"]:
            worse = True  # credit that now lapses
        else:
            better = True
    if mixed or (worse and better):
        return "mixed"
    if worse:
        return "higher"
    if better:
        return "lower"
    return "same"


def needs_notice(value: Impact) -> bool:
    """A change that may cost some business more must be announced ahead."""
    return value in ("higher", "mixed")


def changes_for_owner(old: PricingFields, new: PricingFields) -> list[dict[str, Any]]:
    """The money fields that differ, as `{label, old_minor, new_minor}` in plain words."""
    before, after = old.model_dump(), new.model_dump()
    return [
        {"label": label, "old_minor": before[name], "new_minor": after[name]}
        for name, label in CHANGE_LABELS.items()
        if before[name] != after[name]
    ]


# ---------------------------------------------------------------- dates


def ist_midnight(day: date) -> datetime:
    """00:00 Indian Standard Time on `day`, as an aware datetime."""
    return datetime(day.year, day.month, day.day, tzinfo=IST)


def ist_date(moment: datetime) -> date:
    return moment.astimezone(IST).date()


def earliest_effective(now: datetime, notice_days: int) -> datetime:
    """The first moment a price change announced at `now` may start: IST midnight of the day
    after `now + notice_days`, so the notice is never shorter than promised."""
    return ist_midnight(ist_date(now + timedelta(days=notice_days)) + timedelta(days=1))


def _previous_period(day: date, period: str, billing_day: int) -> date:
    anchor = min(max(billing_day, 1), 28)
    if period == "yearly":
        year = day.year - 1
        return date(year, day.month, min(anchor, calendar.monthrange(year, day.month)[1]))
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, anchor)


def _raw_period_start(
    next_charge_on: date | None, today: date, period: str, billing_day: int
) -> date:
    if next_charge_on is None:
        return today.replace(day=1)
    if next_charge_on <= today:
        return next_charge_on
    return _previous_period(next_charge_on, period, billing_day)


def current_period_start(
    next_charge_on: date | None, today: date, period: str, billing_day: int
) -> date:
    """The first day of the workspace's current billing period. A workspace with no subscription
    schedule is on the calendar month; one whose charge is due today starts its new period today.
    Never a day after `today`: a period length that does not fit the schedule (a yearly charge
    read as monthly) steps back until it does, in a bounded walk."""
    start = _raw_period_start(next_charge_on, today, period, billing_day)
    for _ in range(60):
        if start <= today:
            break
        start = _previous_period(start, period, billing_day)
    return start


def period_start(
    account: BillingAccount | None,
    now: datetime,
    *,
    billing_period: str = "monthly",
    alternatives: Sequence[str] = (),
) -> datetime:
    """Start of the workspace's current billing period, as an IST-midnight datetime comparable
    with `plan_pricing.effective_at`. No account or no subscription: the first of the month.

    `billing_period` is the period length the caller believes; when it does not fit the
    workspace's schedule (the next charge is further away than one such period, as after a
    scheduled change from yearly to monthly) the `alternatives` are tried in order, so the
    workspace is judged by the period it actually paid."""
    today = now.astimezone(IST).date()
    if account is None or account.next_charge_on is None:
        return ist_midnight(today.replace(day=1))
    billing_day = account.billing_day or 1
    for period in (billing_period, *alternatives):
        raw = _raw_period_start(account.next_charge_on, today, period, billing_day)
        if raw <= today:
            return ist_midnight(raw)
    return ist_midnight(
        current_period_start(account.next_charge_on, today, billing_period, billing_day)
    )


async def account_period_start(
    session: AsyncSession,
    plan_id: uuid.UUID,
    account: BillingAccount | None,
    now: datetime,
    *,
    billing_period: str = "monthly",
) -> datetime:
    """`period_start` that reads the plan's other versions' period lengths only when the
    believed one does not fit the schedule (the rare case after a change of period length)."""
    start = period_start(account, now, billing_period=billing_period)
    if account is None or account.next_charge_on is None:
        return start
    today = now.astimezone(IST).date()
    raw = _raw_period_start(account.next_charge_on, today, billing_period, account.billing_day or 1)
    if raw <= today:
        return start
    versions = await plan_versions(session, plan_id)
    others = [v.billing_period for v in sorted(versions, key=lambda v: -v.version)]
    return period_start(account, now, billing_period=billing_period, alternatives=others)


# ---------------------------------------------------------------- the version in force


def version_in_force(versions: list[PlanPricing], start: datetime) -> PlanPricing | None:
    """The newest of `versions` that is in force for a period starting at `start`: no
    `effective_at` (always) or one not after `start`. Pure twin of `pricing_in_force`."""
    ok = [v for v in versions if v.effective_at is None or v.effective_at <= start]
    return max(ok, key=lambda v: v.version) if ok else None


async def pricing_in_force(
    session: AsyncSession,
    plan_id: uuid.UUID,
    account: BillingAccount | None,
    now: datetime,
    *,
    at: datetime | None = None,
    billing_period: str = "monthly",
) -> PlanPricing | None:
    """The pricing version a workspace pays for the period starting at `at` (default: its current
    period). One query: the newest version not scheduled after that start."""
    start = at if at is not None else period_start(account, now, billing_period=billing_period)
    return (
        await session.execute(
            select(PlanPricing)
            .where(
                PlanPricing.plan_id == plan_id,
                or_(PlanPricing.effective_at.is_(None), PlanPricing.effective_at <= start),
            )
            .order_by(PlanPricing.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def applies_from(
    account: BillingAccount | None, not_before: datetime, billing_period: str = "monthly"
) -> date:
    """The first period start at or after `not_before`: the day a business first pays a price
    that took effect (or a pending change that was announced) at that moment. A workspace on a
    subscription renews on its own day; one without is on the calendar month."""
    if account is None or account.next_charge_on is None:
        first = ist_date(not_before).replace(day=1)
        if ist_midnight(first) >= not_before:
            return first
        return date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    from callobuzz_rag.billing.subscriptions import add_period

    cursor = account.next_charge_on
    billing_day = account.billing_day or 1
    for _ in range(60):  # a bounded walk: never an endless loop on bad data
        if ist_midnight(cursor) >= not_before:
            return cursor
        cursor = add_period(cursor, billing_period, billing_day)
    return cursor


async def plan_versions(session: AsyncSession, plan_id: uuid.UUID) -> list[PlanPricing]:
    """Every pricing version of a plan, oldest first."""
    rows = (
        (await session.execute(select(PlanPricing).where(PlanPricing.plan_id == plan_id)))
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda r: r.version)


# ---------------------------------------------------------------- deciding a pricing save


@dataclass(frozen=True)
class Decision:
    """How a saved pricing version starts: now, or scheduled for `effective_at`."""

    scheduled: bool
    effective_at: datetime | None = None


def ceil_ist_midnight(moment: datetime) -> datetime:
    """`moment` rounded up to an IST midnight (an exact midnight stays), the only kind of start
    date a price change has."""
    local = moment.astimezone(IST)
    day = local.date()
    if local != ist_midnight(day):
        day += timedelta(days=1)
    return ist_midnight(day)


def too_short(earliest: datetime) -> APIError:
    return APIError(
        422,
        "Businesses must be told before a higher price starts. The earliest start date is "
        f"{ist_date(earliest):%d %B %Y}.",
        "price_change_notice_too_short",
        earliest=earliest.isoformat(),
    )


def decide(
    *,
    result: Impact,
    workspaces: int,
    apply: str,
    effective_at: datetime | None,
    now: datetime,
    notice_days: int,
) -> Decision:
    """The start of a new pricing version. A plan nobody is on starts at once whatever the change
    (a first version of a plan with businesses on it is judged against the free defaults they
    pay today). Otherwise a change that is not higher for anyone may start at once or on a
    chosen day; a higher or mixed one is always scheduled, no sooner than the notice period
    (`apply="now"` is refused with `price_change_not_lower`)."""
    if workspaces == 0:
        return Decision(False)
    earliest = earliest_effective(now, notice_days)
    if needs_notice(result):
        if apply == "now":
            raise APIError(
                422,
                "This change makes some businesses pay more, so it cannot start today. "
                "Schedule it so they are told first.",
                "price_change_not_lower",
                earliest=earliest.isoformat(),
            )
        start = ceil_ist_midnight(effective_at) if effective_at is not None else earliest
        if start < earliest:
            raise too_short(earliest)
        return Decision(True, start)
    if apply == "now":
        return Decision(False)
    start = ceil_ist_midnight(effective_at) if effective_at is not None else earliest
    if start <= now:
        raise too_short(ceil_ist_midnight(now + timedelta(seconds=1)))
    return Decision(True, start)


def overall_impact(
    versions: list[PlanPricing], current: PlanPricing | None, new: PricingFields, now: datetime
) -> Impact:
    """What `new` means for the businesses on a plan: measured against the current version and,
    while the current one is still rolling out (it started within the last year, so some
    workspaces may still pay the one before), against that one too. The worst wins."""
    if current is None:  # a plan with no pricing is free: that is what its businesses pay today
        return impact(PricingFields(currency=new.currency), new)
    baselines = [current]
    if current.effective_at is not None and current.effective_at > now - timedelta(days=400):
        earlier = [v for v in versions if v.version < current.version]
        if earlier:
            baselines.append(max(earlier, key=lambda v: v.version))
    found = [impact(fields_from_row(b), new) for b in baselines]
    if "mixed" in found:
        return "mixed"
    if "higher" in found:
        return "higher"
    if "lower" in found:
        return "lower"
    return "same"


def pending_scheduled(versions: list[PlanPricing]) -> list[PlanPricing]:
    """Versions announced and not yet current: not current and newer than the current one."""
    current = next((v for v in versions if v.is_current), None)
    floor = current.version if current is not None else 0
    return [v for v in versions if not v.is_current and v.version > floor]


@dataclass
class PricingSave:
    row: PlanPricing
    previous: PlanPricing | None
    changed: list[str]
    impact: Impact
    scheduled: bool
    replaced: list[PlanPricing]
    workspaces: int


@dataclass(frozen=True)
class PricingPreview:
    impact: Impact
    changed: list[str]
    workspaces: int
    earliest_effective_at: datetime


async def workspaces_on_plan(session: AsyncSession, plan_id: uuid.UUID) -> list[Tenant]:
    """The workspaces a pricing change reaches: those on the plan and those with a held move
    into it (they will pay it once the notice period of the move has passed); not COB's, not the
    deployer's own."""
    rows = (await session.execute(select(Tenant).where(Tenant.plan_id == plan_id))).scalars().all()
    found: dict[uuid.UUID, Tenant] = {t.id: t for t in rows}
    moving = (
        (
            await session.execute(
                select(BillingAccount).where(BillingAccount.pending_change_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    for account in moving:
        if str((account.pending_change or {}).get("plan_id")) != str(plan_id):
            continue
        if account.tenant_id not in found:
            tenant = await session.get(Tenant, account.tenant_id)
            if tenant is not None:
                found[tenant.id] = tenant
    return [t for t in found.values() if not t.is_system and not t.is_operator]


async def preview_change(
    session: AsyncSession,
    plan: Plan,
    fields: PricingFields,
    *,
    notice_days: int,
    now: datetime,
) -> PricingPreview:
    """What saving `fields` would mean, writing nothing."""
    versions = await plan_versions(session, plan.id)
    current = next((v for v in versions if v.is_current), None)
    old = (
        fields_from_row(current) if current is not None else PricingFields(currency=fields.currency)
    )
    return PricingPreview(
        impact=overall_impact(versions, current, fields, now),
        changed=changed_fields(old, fields),
        workspaces=len(await workspaces_on_plan(session, plan.id)),
        earliest_effective_at=earliest_effective(now, notice_days),
    )


async def save_with_notice(
    session: AsyncSession,
    plan: Plan,
    fields: PricingFields,
    *,
    created_by: str,
    apply: str,
    effective_at: datetime | None,
    notice_days: int,
    now: datetime,
) -> PricingSave:
    """Save a pricing version the way its impact allows (see `decide`). A version already
    scheduled for the plan is replaced (one at a time); the replaced rows are returned so the
    businesses can be told it was cancelled. Does not commit.

    Raises:
        APIError 409 `pricing_locked` / `currency_mismatch` (as `save_pricing`), 422
        `price_change_not_lower`, `price_change_notice_too_short`.
    """
    if plan.is_internal:
        raise pricing_locked()
    await session.execute(select(Plan.id).where(Plan.id == plan.id).with_for_update())
    await lock_accounts_for_currency(
        session, plan, fields
    )  # a hard stop comes before any date rule
    versions = await plan_versions(session, plan.id)
    current = next((v for v in versions if v.is_current), None)
    workspaces = len(await workspaces_on_plan(session, plan.id))
    result = overall_impact(versions, current, fields, now)
    decision = decide(
        result=result,
        workspaces=workspaces,
        apply=apply,
        effective_at=effective_at,
        now=now,
        notice_days=notice_days,
    )
    replaced = pending_scheduled(versions)
    for row in replaced:
        await session.delete(row)
    if replaced:
        await session.flush()
    row, changed = await save_pricing(
        session,
        plan,
        fields,
        created_by=created_by,
        effective_at=decision.effective_at if decision.scheduled else None,
    )
    return PricingSave(row, current, changed, result, decision.scheduled, replaced, workspaces)


async def cancel_scheduled(session: AsyncSession, plan: Plan) -> list[PlanPricing]:
    """Delete the plan's scheduled version(s). Does not commit.

    Raises:
        APIError 404 `scheduled_pricing_not_found`.
    """
    await session.execute(select(Plan.id).where(Plan.id == plan.id).with_for_update())
    scheduled = pending_scheduled(await plan_versions(session, plan.id))
    if not scheduled:
        raise APIError(
            404, "No price change is scheduled for this plan.", "scheduled_pricing_not_found"
        )
    for row in scheduled:
        await session.delete(row)
    await session.flush()
    return scheduled


async def _activate_plan(session: AsyncSession, plan_id: uuid.UUID, now: datetime) -> bool:
    """Make the plan's due scheduled version current, under the plan row lock."""
    await session.execute(select(Plan.id).where(Plan.id == plan_id).with_for_update())
    versions = await plan_versions(session, plan_id)
    due = [
        v
        for v in pending_scheduled(versions)
        if v.effective_at is not None and v.effective_at <= now
    ]
    if not due:
        return False
    target = max(due, key=lambda v: v.version)
    current = next((v for v in versions if v.is_current), None)
    if current is not None:
        await session.execute(
            update(PlanPricing).where(PlanPricing.id == current.id).values(is_current=False)
        )
        await session.flush()
    await session.execute(
        update(PlanPricing).where(PlanPricing.id == target.id).values(is_current=True)
    )
    accounts = (
        (
            await session.execute(
                select(BillingAccount)
                .join(Tenant, Tenant.id == BillingAccount.tenant_id)
                .where(Tenant.plan_id == plan_id)
            )
        )
        .scalars()
        .all()
    )
    for account in accounts:  # until the first transaction the plan's currency rules
        if account.currency != target.currency and not await currency_frozen(session, account):
            account.currency = target.currency
    return True


async def activate_due(session_factory: Any, now: datetime) -> int:
    """The hourly pass: a scheduled version whose day has come becomes the plan's current one.
    Each plan is handled in its own transaction under its row lock and re-checked there, so two
    runs flip it once. Workspaces mid-period keep paying the version before it
    (`pricing_in_force`). Returns the number of plans switched."""
    async with session_factory() as session:
        current = {
            plan_id: version
            for plan_id, version in (
                await session.execute(
                    select(PlanPricing.plan_id, PlanPricing.version).where(
                        PlanPricing.is_current.is_(True)
                    )
                )
            ).all()
        }
        due = (
            await session.execute(
                select(PlanPricing.plan_id, PlanPricing.version).where(
                    PlanPricing.is_current.is_(False), PlanPricing.effective_at <= now
                )
            )
        ).all()
    # Only a version newer than the plan's current one can be due: older scheduled versions
    # that were activated and replaced match the query for ever, and would otherwise cost a
    # transaction and the plan row lock every hour.
    plan_ids = [plan_id for plan_id, version in due if version > current.get(plan_id, 0)]
    activated = 0
    for plan_id in dict.fromkeys(plan_ids):
        try:
            async with session_factory() as session:
                if await _activate_plan(session, plan_id, now):
                    await session.commit()
                    activated += 1
        except Exception:
            logger.warning(
                "Activating the scheduled price of plan %s failed", plan_id, exc_info=True
            )
    return activated


# ---------------------------------------------------------------- telling the businesses

_MODE_WORDS: Final[dict[str, dict[str, str]]] = {
    "usage_mode": {
        "included": "Included in the plan",
        "per_question": "A price per question",
        "cost_plus": "Charged by actual use",
    },
    "billing_period": {"monthly": "Every month", "yearly": "Every year"},
    "payment_type": {"prepaid": "Prepaid balance", "postpaid": "Billed after use"},
    "overage": {
        "block": "Stops at the limit",
        "charge": "Charged beyond the limit",
        "allow": "Allowed beyond the limit",
    },
}
_MODE_LABELS: Final[dict[str, str]] = {
    "usage_mode": "How usage is charged",
    "billing_period": "Billing period",
    "payment_type": "How you pay",
    "overage": "Beyond your limit",
}


def money_text(minor: int, currency: str) -> str:
    """`₹999`, `₹1,299`, `$15`, `₹5,150.83`: whole units when there are no paise."""
    shown = Money(minor, "USD" if currency == "USD" else "INR").format()
    return shown[:-3] if shown.endswith(".00") else shown


def long_date(day: date) -> str:
    """`12 October 2026`."""
    return f"{day.day} {day:%B %Y}"


def describe_changes(old: PricingFields, new: PricingFields) -> list[dict[str, str]]:
    """What changed in plain words with the amounts written out, for an e-mail:
    `{label, old, new}` per line."""
    before, after = old.model_dump(), new.model_dump()
    lines: list[dict[str, str]] = []
    for name, label in CHANGE_LABELS.items():
        if before[name] != after[name]:
            lines.append(
                {
                    "label": label,
                    "old": money_text(before[name], old.currency),
                    "new": money_text(after[name], new.currency),
                }
            )
    for name, label in _MODE_LABELS.items():
        if before[name] != after[name]:
            words = _MODE_WORDS[name]
            lines.append(
                {
                    "label": label,
                    "old": words.get(str(before[name]), str(before[name])),
                    "new": words.get(str(after[name]), str(after[name])),
                }
            )
    if before["currency"] != after["currency"]:
        lines.append({"label": "Currency", "old": before["currency"], "new": after["currency"]})
    return lines


async def announce_change(
    session: AsyncSession,
    *,
    plan: Plan,
    old: PlanPricing | None,
    new: PlanPricing,
    kind: str,
    enqueue: Callable[..., None],
    product_name: str,
    dashboard_url: str,
) -> int:
    """Tell the owners of every workspace on `plan` about a price change, once each
    (`billing_notices`: kind and `str(new.id)`). `kind` is `price_change` (a raise or any
    scheduled change), `plan_lower` (a drop that started at once) or `price_change_cancelled`.
    `old` is the version it replaces; None for a plan's first pricing, which replaces the free
    defaults. Best effort per workspace: a failed queue is logged and leaves no notice row, and
    `announce_scheduled_changes` (hourly) tells again. Returns the number of workspaces told.
    Commits the notice rows."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails

    new_fields = fields_from_row(new)
    old_fields = (
        fields_from_row(old)
        if old is not None
        else PricingFields.model_validate({"currency": new.currency})
    )
    lines = describe_changes(old_fields, new_fields)
    told = 0
    for tenant in await workspaces_on_plan(session, plan.id):
        key = str(new.id)
        try:
            exists = (
                await session.execute(
                    select(BillingNotice.id).where(
                        BillingNotice.tenant_id == tenant.id,
                        BillingNotice.kind == kind,
                        BillingNotice.key == key,
                    )
                )
            ).first()
            owners = await owner_emails(session, tenant.id)
            if exists is not None or not owners:
                continue
            context: dict[str, Any] = {
                "business_name": tenant.name,
                "product_name": product_name,
                "plan_name": plan.name,
                "billing_url": dashboard_link(dashboard_url, "/plan"),
            }
            if kind == "price_change_cancelled":
                template = "billing_price_change_cancelled"
            else:
                template = "billing_price_change"
                account = (
                    await session.execute(
                        select(BillingAccount).where(BillingAccount.tenant_id == tenant.id)
                    )
                ).scalar_one_or_none()
                starts = new.effective_at or datetime.now(UTC)
                # the workspace renews on its CURRENT schedule: a change of period length is
                # first paid at the renewal after the start date, counted in the old length
                period = ((account.overrides or {}) if account is not None else {}).get(
                    "billing_period"
                ) or old_fields.billing_period
                context.update(
                    {
                        "effective_on": long_date(applies_from(account, starts, str(period))),
                        "changes": lines,
                        "lower": kind == "plan_lower",
                        "support_url": dashboard_link(dashboard_url, "/support"),
                    }
                )
            for to in owners:
                enqueue(template, to, context, expires_s=7 * 24 * 3600)
            await session.execute(
                pg_insert(BillingNotice)
                .values(tenant_id=tenant.id, kind=kind, key=key)
                .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "key"])
            )
            told += 1
        except Exception:
            logger.warning("Price change notice for workspace %s failed", tenant.id, exc_info=True)
    await session.commit()
    return told


async def announce_scheduled_changes(
    session_factory: Any,
    *,
    enqueue: Callable[..., None],
    product_name: str,
    dashboard_url: str,
) -> int:
    """Tell every workspace about every scheduled price version it has not been told about
    (`billing_notices` makes each mail once). The save route announces right after its commit,
    best effort; this hourly pass is what makes the promise hold when that queueing failed, and
    it reaches the workspaces that joined the plan after the announcement. Returns the number
    of workspace notices sent."""
    async with session_factory() as session:
        plan_ids = list(
            (
                await session.execute(
                    select(PlanPricing.plan_id).where(
                        PlanPricing.is_current.is_(False), PlanPricing.effective_at.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
    told = 0
    for plan_id in dict.fromkeys(plan_ids):
        try:
            async with session_factory() as session:
                plan = await session.get(Plan, plan_id)
                if plan is None:
                    continue
                versions = await plan_versions(session, plan_id)
                current = next((v for v in versions if v.is_current), None)
                for coming in pending_scheduled(versions):
                    told += await announce_change(
                        session,
                        plan=plan,
                        old=current,
                        new=coming,
                        kind="price_change",
                        enqueue=enqueue,
                        product_name=product_name,
                        dashboard_url=dashboard_url,
                    )
        except Exception:
            logger.warning(
                "Announcing the scheduled price of plan %s failed", plan_id, exc_info=True
            )
    return told


async def hourly_pass(
    session_factory: Any,
    now: datetime,
    *,
    enqueue: Callable[..., None] | None = None,
    engine_settings: Any = None,
    dashboard_url: str | None = None,
) -> dict[str, int]:
    """The hourly beat of price changes: announce what has not been announced, then make due
    versions current and apply due per-workspace changes. Announcing comes first, so a notice
    that failed to queue is retried right up to the day the price starts."""
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    if engine_settings is None:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        engine_settings = await get_engine_settings(session_factory)
    if dashboard_url is None:
        from callobuzz_rag.core.config import get_settings

        dashboard_url = get_settings().dashboard_url
    product = engine_settings.branding.product_name
    announced = 0
    try:
        announced = await announce_scheduled_changes(
            session_factory, enqueue=enqueue, product_name=product, dashboard_url=dashboard_url
        )
    except Exception:
        logger.warning("Announcing scheduled prices failed", exc_info=True)
    return {
        "announced": announced,
        "activated": await activate_due(session_factory, now),
        "applied": await apply_due_account_changes(
            session_factory,
            now,
            enqueue=enqueue,
            product_name=product,
            dashboard_url=dashboard_url,
        ),
    }


# ---------------------------------------------------------------- per-workspace changes

MIN_REASON: Final = 10  # characters a deployer must write to apply a raise "the business agreed"


@dataclass(frozen=True)
class HeldChange:
    """A raise for one workspace that waits for its notice period."""

    pending: dict[str, Any]
    at: datetime
    impact: Impact
    old: PricingFields
    new: PricingFields


def pending_key(pending: dict[str, Any], at: datetime) -> str:
    """The `billing_notices` key of a pending change: its date and a hash of what it holds, so a
    replaced change is announced again but a re-run of the same one is not."""
    import hashlib
    import json

    digest = hashlib.sha256(json.dumps(pending, sort_keys=True, default=str).encode()).hexdigest()
    return f"{ist_date(at).isoformat()}-{digest[:16]}"


def needs_agreement_reason(agreed: bool, reason: str | None) -> str | None:
    """The reason the deployer wrote for applying a raise at once because the business asked for
    it, or None when `agreed` is false.

    Raises:
        APIError 422 `agreement_reason_required` (fewer than `MIN_REASON` characters).
    """
    if not agreed:
        return None
    text = (reason or "").strip()
    if len(text) < MIN_REASON:
        raise APIError(
            422,
            f"Write at least {MIN_REASON} characters about how the business asked for this "
            "change (who, when).",
            "agreement_reason_required",
        )
    return text


def _never_active(tenant: Tenant) -> bool:
    """A workspace still waiting for its first activation uses nothing, so it has no price it
    could be surprised by. A workspace that is live has, whether or not it was ever charged:
    one on a free or included plan has used the product for months and pays nothing today."""
    return tenant.status == "pending"


async def hold_overrides(
    session: AsyncSession,
    ctx: Any,
    changes: dict[str, Any],
    *,
    agreed: bool,
    reason: str | None,
    notice_days: int,
    now: datetime,
    request: Any,
) -> HeldChange | None:
    """Decide what an admin's override change means for this workspace. None: apply it now
    (it costs the business no more, the workspace was never activated, or `agreed` with a typed
    reason, which is audited as `billing.change_agreed`). A HeldChange: it was stored as the
    workspace's pending change, to apply at its first renewal after the notice period.
    Does not commit."""
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.billing.accounts import merge_overrides
    from callobuzz_rag.billing.pricing import combine, overrides_from_stored

    account: BillingAccount = ctx.account
    eff = ctx.effective
    if eff.exempt or _never_active(ctx.tenant):
        return None
    merged = merge_overrides(account.overrides or {}, changes)
    base = (
        fields_from_row(ctx.pricing)
        if ctx.pricing is not None
        else PricingFields(currency=eff.currency)
    )
    new = combine(base, overrides_from_stored(merged), currency=eff.currency, exempt=False).fields
    result = impact(eff.fields, new)
    if not needs_notice(result):
        return None
    why = needs_agreement_reason(agreed, reason)
    if why is not None:
        await record_audit(
            session,
            request,
            "billing.change_agreed",
            tenant_id=ctx.tenant.id,
            tenant_label=ctx.tenant.name,
            target_type="billing_account",
            target_id=str(account.id),
            details={"tenant_id": str(ctx.tenant.id), "change": dict(changes), "reason": why},
        )
        return None
    pending = {"overrides": dict(changes)}
    return await _store_pending(
        session, ctx, pending, eff.fields, new, result, notice_days, now, request
    )


async def hold_plan_move(
    session: AsyncSession,
    ctx: Any,
    new_plan: Plan,
    *,
    agreed: bool,
    reason: str | None,
    notice_days: int,
    now: datetime,
    request: Any,
) -> HeldChange | None:
    """The same for moving a workspace to another plan: None to move it now, a HeldChange when
    the new plan costs it more and the move waits for the notice period."""
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.billing.pricing import combine, overrides_from_stored

    account: BillingAccount = ctx.account
    eff = ctx.effective
    if eff.exempt or _never_active(ctx.tenant):
        return None
    # What the workspace would pay on the target plan: the version in force for the period it
    # would pay it in (now, if the move applies at once) and for the period the held move would
    # start in. A raise that is already scheduled on the target plan counts: the move must not
    # hand the business a price nobody announced to it.
    versions = await plan_versions(session, new_plan.id)
    held_start = ist_midnight(
        applies_from(account, earliest_effective(now, notice_days), eff.fields.billing_period)
    )
    now_start = period_start(account, now, billing_period=eff.fields.billing_period)
    result: Impact = "same"
    new = eff.fields
    seen: set[uuid.UUID | None] = set()
    for at in (now_start, held_start):
        row = version_in_force(versions, at)
        if row is not None and row.id in seen:
            continue
        seen.add(row.id if row is not None else None)
        base = fields_from_row(row) if row is not None else PricingFields(currency=eff.currency)
        candidate = combine(
            base, overrides_from_stored(account.overrides), currency=eff.currency, exempt=False
        ).fields
        found = impact(eff.fields, candidate)
        if _IMPACT_RANK[found] >= _IMPACT_RANK[result]:
            result, new = found, candidate
    if not needs_notice(result):
        return None
    why = needs_agreement_reason(agreed, reason)
    if why is not None:
        await record_audit(
            session,
            request,
            "billing.change_agreed",
            tenant_id=ctx.tenant.id,
            tenant_label=ctx.tenant.name,
            target_type="billing_account",
            target_id=str(account.id),
            details={
                "tenant_id": str(ctx.tenant.id),
                "change": {"plan_id": str(new_plan.id)},
                "reason": why,
            },
        )
        return None
    pending = {"plan_id": str(new_plan.id)}
    return await _store_pending(
        session, ctx, pending, eff.fields, new, result, notice_days, now, request
    )


async def _store_pending(
    session: AsyncSession,
    ctx: Any,
    pending: dict[str, Any],
    old: PricingFields,
    new: PricingFields,
    result: Impact,
    notice_days: int,
    now: datetime,
    request: Any,
) -> HeldChange:
    from callobuzz_rag.accounts.audit import record_audit

    account: BillingAccount = ctx.account
    start = ist_midnight(
        applies_from(account, earliest_effective(now, notice_days), old.billing_period)
    )
    account.pending_change = pending  # one pending change per workspace: this replaces any
    account.pending_change_at = start
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.pending_change_scheduled",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "change": pending,
            "effective_at": start.isoformat(),
        },
    )
    return HeldChange(pending, start, result, old, new)


async def cancel_pending(session: AsyncSession, ctx: Any, request: Any) -> dict[str, Any]:
    """Drop a workspace's pending change and audit it. Does not commit.

    Raises:
        APIError 404 `pending_change_not_found`.
    """
    from callobuzz_rag.accounts.audit import record_audit

    account: BillingAccount = ctx.account
    if not account.pending_change:
        raise APIError(404, "This workspace has no pending change.", "pending_change_not_found")
    gone = dict(account.pending_change)
    when = account.pending_change_at
    account.pending_change = None
    account.pending_change_at = None
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.pending_change_cancelled",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "change": gone,
            "effective_at": when.isoformat() if when else None,
        },
    )
    return gone


async def drop_stale_overrides(
    session: AsyncSession, ctx: Any, applied: dict[str, Any], request: Any
) -> bool:
    """The deployer's latest word wins: override fields that were just applied at once must not
    be set back at the renewal by an earlier held raise of the same fields. The keys are taken
    out of a pending `overrides` change (the whole change goes when none is left), audited as
    `billing.pending_change_cancelled`. True when something was dropped. Does not commit."""
    from callobuzz_rag.accounts.audit import record_audit

    account: BillingAccount = ctx.account
    pending = dict(account.pending_change or {})
    held = dict(pending.get("overrides") or {})
    stale = {k: held[k] for k in applied if k in held}
    if not stale:
        return False
    rest = {k: v for k, v in held.items() if k not in stale}
    when = account.pending_change_at
    if rest:
        account.pending_change = {**pending, "overrides": rest}
    else:
        account.pending_change = None
        account.pending_change_at = None
    await session.flush()
    await record_audit(
        session,
        request,
        "billing.pending_change_cancelled",
        tenant_id=ctx.tenant.id,
        tenant_label=ctx.tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details={
            "tenant_id": str(ctx.tenant.id),
            "change": {"overrides": stale},
            "effective_at": when.isoformat() if when else None,
        },
    )
    return True


async def pending_effect(
    session: AsyncSession, ctx: Any, *, at: datetime | None = None
) -> tuple[PricingFields, Plan | None] | None:
    """What this workspace will pay once its pending change applies, and the plan it moves to
    (None when it stays on its plan). None when nothing is pending.

    The plan's terms are the version in force at `at` (default: the day the change applies; a
    caller asking about a renewal passes the renewal day), the same one `run_due` charges, so a
    plan-level raise that starts by then is part of the amount."""
    from callobuzz_rag.billing.accounts import merge_overrides
    from callobuzz_rag.billing.pricing import combine, overrides_from_stored

    account: BillingAccount = ctx.account
    if not account.pending_change:
        return None
    raw = dict(account.pending_change)
    when = at or account.pending_change_at
    plan_id = ctx.plan.id if getattr(ctx, "plan", None) is not None else None
    target: Plan | None = None
    if raw.get("plan_id"):
        target = await session.get(Plan, uuid.UUID(str(raw["plan_id"])))
        plan_id = target.id if target is not None else plan_id
    row = None
    if plan_id is not None and when is not None:
        row = version_in_force(await plan_versions(session, plan_id), when)
    elif target is None:
        row = ctx.pricing
    base = (
        fields_from_row(row) if row is not None else PricingFields(currency=ctx.effective.currency)
    )
    overrides = account.overrides or {}
    if raw.get("overrides"):
        overrides = merge_overrides(overrides, raw["overrides"])
    new = combine(
        base, overrides_from_stored(overrides), currency=ctx.effective.currency, exempt=False
    ).fields
    return new, target


async def notify_pending(
    session: AsyncSession,
    ctx: Any,
    held: HeldChange,
    *,
    enqueue: Callable[..., None],
    product_name: str,
    dashboard_url: str,
) -> bool:
    """Tell the workspace's owners about its pending change (once per change). Commits the
    notice row; a queue failure leaves none. True when told."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails

    tenant: Tenant = ctx.tenant
    key = pending_key(held.pending, held.at)
    exists = (
        await session.execute(
            select(BillingNotice.id).where(
                BillingNotice.tenant_id == tenant.id,
                BillingNotice.kind == "pending_change",
                BillingNotice.key == key,
            )
        )
    ).first()
    owners = await owner_emails(session, tenant.id)
    if exists is not None or not owners:
        return False
    context = {
        "business_name": tenant.name,
        "product_name": product_name,
        "plan_name": ctx.plan.name,
        "effective_on": long_date(ist_date(held.at)),
        "changes": describe_changes(held.old, held.new),
        "lower": False,
        "billing_url": dashboard_link(dashboard_url, "/plan"),
        "support_url": dashboard_link(dashboard_url, "/support"),
    }
    for to in owners:
        enqueue("billing_price_change", to, context, expires_s=7 * 24 * 3600)
    await session.execute(
        pg_insert(BillingNotice)
        .values(tenant_id=tenant.id, kind="pending_change", key=key)
        .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "key"])
    )
    await session.commit()
    return True


async def _apply_pending(
    session: AsyncSession, account_id: uuid.UUID, now: datetime
) -> tuple[str, dict[str, Any], datetime] | None:
    """Apply one workspace's due pending change under its account lock. Returns `("applied",
    change, when)`; `("cancelled", change, when)` when a held move can never happen (its plan was
    deactivated or its currency no longer fits), which drops it with an audit row so it does not
    sit in the past for ever; None when nothing was due."""
    from callobuzz_rag.accounts import plans
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.billing.accounts import merge_overrides

    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        account is None
        or not account.pending_change
        or account.pending_change_at is None
        or account.pending_change_at > now
    ):
        return None
    tenant = await session.get(Tenant, account.tenant_id)
    if tenant is None:
        return None
    change = dict(account.pending_change)
    when = account.pending_change_at
    outcome = "applied"
    refused: str | None = None
    if "overrides" in change:
        account.overrides = merge_overrides(account.overrides or {}, change["overrides"])
    if "plan_id" in change:
        try:
            await plans.set_tenant_plan(session, tenant, uuid.UUID(change["plan_id"]), request=None)
        except APIError as exc:
            if exc.status_code not in (404, 409):
                raise
            outcome, refused = "cancelled", exc.code
    account.pending_change = None
    account.pending_change_at = None
    account.updated_at = now
    await session.flush()
    details: dict[str, Any] = {
        "tenant_id": str(tenant.id),
        "change": change,
        "effective_at": when.isoformat(),
    }
    if refused is not None:
        details["reason"] = refused
    await record_audit(
        session,
        None,
        (
            "billing.pending_change_applied"
            if outcome == "applied"
            else "billing.pending_change_cancelled"
        ),
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="billing_account",
        target_id=str(account.id),
        details=details,
    )
    return outcome, change, when


async def _tell_move_cancelled(
    session_factory: Any,
    account_id: uuid.UUID,
    change: dict[str, Any],
    when: datetime,
    *,
    enqueue: Callable[..., None],
    product_name: str,
    dashboard_url: str,
) -> None:
    """Tell the owners that the plan move they were promised will not happen (once)."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails

    async with session_factory() as session:
        account = await session.get(BillingAccount, account_id)
        tenant = await session.get(Tenant, account.tenant_id) if account is not None else None
        target = await session.get(Plan, uuid.UUID(change["plan_id"]))
        if tenant is None or target is None:
            return
        key = pending_key(change, when)
        exists = (
            await session.execute(
                select(BillingNotice.id).where(
                    BillingNotice.tenant_id == tenant.id,
                    BillingNotice.kind == "pending_change_cancelled",
                    BillingNotice.key == key,
                )
            )
        ).first()
        owners = await owner_emails(session, tenant.id)
        if exists is not None or not owners:
            return
        context = {
            "business_name": tenant.name,
            "product_name": product_name,
            "plan_name": target.name,
            "billing_url": dashboard_link(dashboard_url, "/plan"),
        }
        for to in owners:
            enqueue("billing_price_change_cancelled", to, context, expires_s=7 * 24 * 3600)
        await session.execute(
            pg_insert(BillingNotice)
            .values(tenant_id=tenant.id, kind="pending_change_cancelled", key=key)
            .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "key"])
        )
        await session.commit()


async def apply_due_account_changes(
    session_factory: Any,
    now: datetime,
    *,
    enqueue: Callable[..., None] | None = None,
    product_name: str = "",
    dashboard_url: str = "",
) -> int:
    """The hourly pass: every pending change whose renewal has come is applied, once (the
    account row is locked and the change re-read there), then the balance hook re-checks the
    workspace (a dearer price can pause it). A move that can no longer happen is dropped and
    its owners told (when `enqueue` is given). Returns how many were applied."""
    async with session_factory() as session:
        due = list(
            (
                await session.execute(
                    select(BillingAccount.id).where(
                        BillingAccount.pending_change_at.is_not(None),
                        BillingAccount.pending_change_at <= now,
                    )
                )
            )
            .scalars()
            .all()
        )
    applied: list[uuid.UUID] = []
    cancelled: list[tuple[uuid.UUID, dict[str, Any], datetime]] = []
    for account_id in due:
        try:
            async with session_factory() as session:
                done = await _apply_pending(session, account_id, now)
                if done is not None:
                    await session.commit()
                    if done[0] == "applied":
                        applied.append(account_id)
                    else:
                        cancelled.append((account_id, done[1], done[2]))
        except Exception:
            logger.warning("Applying the pending change of %s failed", account_id, exc_info=True)
    if enqueue is not None:
        for account_id, change, when in cancelled:
            try:
                await _tell_move_cancelled(
                    session_factory,
                    account_id,
                    change,
                    when,
                    enqueue=enqueue,
                    product_name=product_name,
                    dashboard_url=dashboard_url,
                )
            except Exception:
                logger.warning("Cancelled-move notice for %s failed", account_id, exc_info=True)
    if applied:
        try:
            from callobuzz_rag.billing import hooks

            await hooks.after_balance_change(session_factory, applied)
        except Exception:
            logger.warning("Balance hook failed after pending changes", exc_info=True)
    return len(applied)
