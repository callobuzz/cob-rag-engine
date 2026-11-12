"""What the owner's "Plan & billing" page and the header meter need beyond the account terms
(BIL6): this month's usage, the statements list, the next charge, the top-up request rule, and
the one-line balance for the header.

Everything here is read-only. Money is never shown to a member unless the plan says so
(`members_see_money`), and statements are for owners only.
"""

import calendar
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import and_, func, select

from callobuzz_rag.accounts.caps import questions_this_month
from callobuzz_rag.billing import alerts, ledger, statements
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.enforcement import account_state
from callobuzz_rag.billing.money import MICRO_PER_MINOR, Currency
from callobuzz_rag.billing.notice import (
    account_period_start,
    applies_from,
    changes_for_owner,
    ist_date,
    ist_midnight,
    pending_effect,
    plan_versions,
    version_in_force,
)
from callobuzz_rag.billing.pricing import (
    PricingFields,
    TenantNotFoundError,
    combine,
    effective_pricing,
    fields_from_row,
    overrides_from_stored,
)
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.usage_charge import UsageCharge

TOP_UP_ACTION: Final = "billing.top_up_requested"
TOP_UP_WINDOW: Final = timedelta(hours=1)
STATEMENT_REFS: Final = 12


@dataclass(frozen=True)
class UsagePeriod:
    questions: int
    charged_minor: int  # what usage has cost so far this month, posted or not
    unpriced: int  # AI calls with no known price this month (charged nothing)


async def usage_this_period(
    session: Any, account: BillingAccount, now: datetime | None = None
) -> UsagePeriod:
    moment = now or datetime.now(UTC)
    first = moment.astimezone(UTC).date().replace(day=1)
    charged, unpriced = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageCharge.amount_micro), 0),
                func.count().filter(
                    and_(UsageCharge.usage_mode == "cost_plus", UsageCharge.cost_known.is_(False))
                ),
            ).where(
                UsageCharge.account_id == account.id,
                UsageCharge.currency == account.currency,
                UsageCharge.day >= first,
            )
        )
    ).one()
    return UsagePeriod(
        questions=await questions_this_month(session, account.tenant_id, moment),
        charged_minor=int(charged) // MICRO_PER_MINOR,
        unpriced=int(unpriced),
    )


async def recent_statements(
    session: Any, account: BillingAccount, now: datetime | None = None
) -> list[dict[str, Any]]:
    """The last twelve months (newest first), only for an account that has had a transaction."""
    if not account.ledger_seq:
        return []
    rows = await statements.summaries(session, account, now=now, limit=STATEMENT_REFS)
    return [
        {"month": f"{r.month:%Y-%m}", "closing_minor": r.closing_minor, "live": r.live}
        for r in rows
    ]


async def last_top_up_request(
    session: Any, tenant_id: uuid.UUID, now: datetime | None = None
) -> datetime | None:
    """When this workspace last asked for a top-up within the hour, else None. Read from the
    audit trail (`ix_audit_log_tenant_created`), so there is no Redis to fail: a customer asking
    to pay must never be blocked by a cache blip."""
    moment = now or datetime.now(UTC)
    last: datetime | None = (
        await session.execute(
            select(AuditLog.created_at)
            .where(
                AuditLog.tenant_id == tenant_id,
                AuditLog.action == TOP_UP_ACTION,
                AuditLog.created_at >= moment - TOP_UP_WINDOW,
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return last


def retry_after_seconds(last: datetime, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    return max(1, int((last + TOP_UP_WINDOW - moment).total_seconds()))


async def billing_meter(
    session: Any, tenant_id: uuid.UUID, *, is_member: bool
) -> dict[str, Any] | None:
    """The header's balance or credit for a billed workspace, else None. Read-only: no account
    is created (a workspace that has not been billed yet shows a zero balance)."""
    if not billing_enabled():
        return None
    try:
        eff = await effective_pricing(session, tenant_id)
    except TenantNotFoundError:
        return None
    if eff.exempt:
        return None
    account = (
        await session.execute(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    ).scalar_one_or_none()
    available: int | None = None
    if account is not None:
        available = ledger.available_minor_of(
            int(account.balance_minor), await ledger.owed_micro(session, account)
        )
    state = account_state(eff, account, available)
    if not state.billed and not state.paused:
        return None
    f = eff.fields
    visible = not is_member or f.members_see_money
    level = None
    tenant = await session.get(Tenant, tenant_id) if account is not None else None
    if account is not None and tenant is not None and available is not None:
        snap = alerts.build_snapshot(account, tenant, eff, available)
        level = snap.level if snap is not None and snap.level else None
    postpaid = f.payment_type == "postpaid"
    return {
        "payment_type": f.payment_type,
        "currency": eff.currency,
        "money_visible": visible,
        "available_minor": state.available_minor if visible else None,
        "credit_limit_minor": f.credit_limit_minor if visible and postpaid else None,
        "paused": state.paused,
        "alert_level": level,
    }


# TV3 summary
def project_month_end(charged_minor: int, *, day: int, days_in_month: int) -> int:
    """Usage charged so far this month, scaled to the whole month (integer floor)."""
    return charged_minor * days_in_month // max(day, 1)


def projected_total(
    usage_projected: int, fee_minor: int, renews_on: date | None, today: date
) -> int:
    """The month-end figure: projected usage, plus the subscription fee when a renewal falls
    after today and before the month ends."""
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if renews_on is not None and today < renews_on <= month_end:
        return usage_projected + fee_minor
    return usage_projected


@dataclass(frozen=True)
class RenewalTerms:
    amount_minor: int
    included_credit_minor: int
    new_price_from: date | None


def _terms(row: PlanPricing, overrides: dict[str, Any] | None) -> PricingFields:
    """A stored version with the customer's own terms on top."""
    return combine(
        fields_from_row(row),
        overrides_from_stored(overrides),
        currency=cast("Currency", row.currency),
        exempt=False,
    ).fields


def pending_applies_by(account: BillingAccount | None) -> bool:
    """True when the workspace's pending change is applied before its next renewal is charged:
    it is set for an IST midnight on or before the renewal day."""
    return bool(
        account is not None
        and account.pending_change
        and account.pending_change_at is not None
        and account.next_charge_on is not None
        and account.pending_change_at <= ist_midnight(account.next_charge_on)
    )


def renewal_terms(
    versions: list[PlanPricing],
    *,
    renews_on: date,
    overrides: dict[str, Any] | None,
    in_force: PlanPricing | None,
    pending: PricingFields | None = None,
) -> RenewalTerms | None:
    """The fee and included credit of the version in force on the renewal day, with the
    customer's own terms on top; `new_price_from` is the renewal day when that version differs
    from the one the workspace pays now. `pending` is the workspace's own pending change when it
    applies before this renewal is charged (the hourly job applies it first): its terms win."""
    at_renewal = version_in_force(versions, ist_midnight(renews_on))
    if at_renewal is None:
        return None
    fields = _terms(at_renewal, overrides)
    changed = in_force is not None and at_renewal.version != in_force.version
    if pending is not None:
        changed = changed or (
            pending.subscription_fee_minor,
            pending.included_credit_minor,
        ) != (fields.subscription_fee_minor, fields.included_credit_minor)
        fields = pending
    return RenewalTerms(
        amount_minor=fields.subscription_fee_minor,
        included_credit_minor=fields.included_credit_minor,
        new_price_from=renews_on if changed else None,
    )


def coming_changes(
    versions: list[PlanPricing],
    *,
    in_force: PlanPricing | None,
    overrides: dict[str, Any] | None,
    first_period_on: Callable[[datetime], date],
    plan_name: str,
) -> list[dict[str, Any]]:
    """Price changes announced for this workspace's plan and not yet paid: each version newer
    than the one in force, listed against the one before it, dated by the first period the
    workspace pays it."""
    if in_force is None:
        return []
    out: list[dict[str, Any]] = []
    previous = in_force
    for row in sorted(versions, key=lambda r: r.version):
        if row.version <= in_force.version or row.effective_at is None:
            continue
        changes = changes_for_owner(_terms(previous, overrides), _terms(row, overrides))
        if changes:
            out.append(
                {
                    "effective_on": first_period_on(row.effective_at),
                    "plan_name": plan_name,
                    "changes": changes,
                }
            )
        previous = row
    return out


async def trust_fields(
    session: Any,
    ctx: Any,
    usage: UsagePeriod,
    *,
    visible: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The Plan page's no-surprises fields: the next renewal, where this month is heading, the
    price changes on their way and the spending cap. A workspace that is not billed has none of
    them; a member without money rights gets nulls and an empty list."""
    from callobuzz_rag.accounts.settings_store import read_engine_settings_row
    from callobuzz_rag.billing.enforcement import stored_status
    from callobuzz_rag.billing.spend_cap import cap_view

    moment = now or datetime.now(UTC)
    account, eff = ctx.account, ctx.effective
    if not visible or stored_status(eff, account) == "not_billed":
        return {
            "renewal": None,
            "projected_month_end_minor": None,
            "price_changes": [],
            "spend_cap": None,
        }
    f = eff.fields
    versions = await plan_versions(session, ctx.plan.id)
    start = await account_period_start(
        session, ctx.plan.id, account, moment, billing_period=f.billing_period
    )
    in_force = version_in_force(versions, start) or ctx.pricing
    today = moment.astimezone(UTC).date()

    renewal: dict[str, Any] | None = None
    fee = 0
    if account.next_charge_on is not None and (
        f.subscription_fee_minor > 0 or f.included_credit_minor > 0
    ):
        held = (
            await pending_effect(session, ctx, at=ist_midnight(account.next_charge_on))
            if pending_applies_by(account)
            else None
        )
        terms = renewal_terms(
            versions,
            renews_on=account.next_charge_on,
            overrides=account.overrides,
            in_force=in_force,
            pending=held[0] if held is not None else None,
        )
        if terms is not None:
            renewal = {
                "on": account.next_charge_on,
                "amount_minor": terms.amount_minor,
                "included_credit_minor": terms.included_credit_minor,
                "new_price_from": terms.new_price_from,
            }
            fee = terms.amount_minor
    days = calendar.monthrange(today.year, today.month)[1]
    projected = project_month_end(usage.charged_minor, day=today.day, days_in_month=days)
    settings = (await read_engine_settings_row(session))[0]
    coming = coming_changes(
        versions,
        in_force=in_force,
        overrides=account.overrides,
        first_period_on=lambda at: applies_from(account, at, f.billing_period),
        plan_name=ctx.plan.name,
    )
    pending = await pending_effect(session, ctx)
    if pending is not None and account.pending_change_at is not None:
        later, target = pending
        lines = changes_for_owner(f, later)
        if lines:
            coming.append(
                {
                    "effective_on": ist_date(account.pending_change_at),
                    "plan_name": target.name if target is not None else ctx.plan.name,
                    "changes": lines,
                }
            )
    return {
        "renewal": renewal,
        "projected_month_end_minor": projected_total(projected, fee, account.next_charge_on, today),
        "price_changes": coming,
        "spend_cap": cap_view(
            account.spend_cap_minor,
            usage.charged_minor,
            settings.trust.spend_cap_max_minor.get(eff.currency),
        ),
    }


# /TV3 summary
