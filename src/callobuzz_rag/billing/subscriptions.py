"""Subscriptions, included credit and expiry: the hourly billing schedule.

For every workspace whose plan has a fee or an included credit: on the first run after it becomes
subscription-bearing (or when its trial ends) the first period is charged, then one period every
month or year on the account's `billing_day` (clamped to 1..28). Periods that were missed (the
beat was down) are charged in order on catch-up, each under its own idempotency key
(`sub:<period start>`, `inc:<period start>`), so a rerun never charges twice.

A prepaid workspace is charged even when its balance cannot cover the fee: the balance goes
negative and the workspace pauses, rather than service silently being given away (D10).
Included credit is a credit lot that, unless the plan says otherwise, expires at the end of the
period; only the unused part of it expires, and never more than the balance still holds. The
expiry pass runs before the charge pass so an old period's credit expires against the balance it
actually had.

Dates are UTC. Automated movements live in the ledger only (`actor_label = "system"`).
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from callobuzz_rag.billing import hooks, posting
from callobuzz_rag.billing.accounts import account_for_update
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.ledger import (
    SYSTEM_ACTOR,
    EntryType,
    NewEntry,
    append_entries,
    month_of,
)
from callobuzz_rag.billing.lots import expire_amount
from callobuzz_rag.billing.notice import ist_midnight
from callobuzz_rag.billing.pricing import effective_pricing
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_statement import BillingStatement
from callobuzz_rag.db.models.credit_lot import CreditLot

logger = logging.getLogger(__name__)

MAX_PERIODS_PER_RUN = 36  # a catch-up never loops unboundedly; the next hour continues


# ---------------------------------------------------------------- pure date rules


def clamp_day(billing_day: int) -> int:
    return min(max(billing_day, 1), 28)


def add_period(start: date, period: str, billing_day: int) -> date:
    """The next charge date after `start`: the same `billing_day` one month (or one year) on."""
    day = clamp_day(billing_day)
    if period == "yearly":
        return date(start.year + 1, start.month, day)
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return date(year, month, day)


def first_charge_on(today: date, trial_ends_at: datetime | None) -> date:
    """When a workspace that just became subscription-bearing is first charged: today, or the
    day its trial ends, whichever is later."""
    if trial_ends_at is not None:
        trial_end = trial_ends_at.astimezone(UTC).date()
        if trial_end > today:
            return trial_end
    return today


def due_dates(next_charge_on: date, today: date, period: str, billing_day: int) -> list[date]:
    """Every charge date from `next_charge_on` up to and including `today`, in order (at most
    `MAX_PERIODS_PER_RUN`)."""
    out: list[date] = []
    cursor = next_charge_on
    while cursor <= today and len(out) < MAX_PERIODS_PER_RUN:
        out.append(cursor)
        cursor = add_period(cursor, period, billing_day)
    return out


def _midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _label(period: str, day: date) -> str:
    return f"Subscription ({'yearly' if period == 'yearly' else day.strftime('%B %Y')})"


# ---------------------------------------------------------------- the database side


@dataclass(frozen=True)
class ScheduleResult:
    account_id: uuid.UUID
    charged: list[date]
    expired_minor: int = 0


async def _expire_lots(session: Any, account: BillingAccount, now: datetime) -> int:
    """Expire the account's lots that reached their date; returns the minor units removed."""
    lots = list(
        (
            await session.execute(
                select(CreditLot)
                .where(
                    CreditLot.account_id == account.id,
                    CreditLot.remaining_minor > 0,
                    CreditLot.expires_at <= now,
                )
                .order_by(CreditLot.expires_at, CreditLot.created_at)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    total = 0
    for lot in lots:
        amount = expire_amount(lot.remaining_minor, account.balance_minor)
        lot.remaining_minor = 0
        if amount > 0:
            [result] = await append_entries(
                session,
                account.id,
                [
                    NewEntry(
                        EntryType.EXPIRY,
                        -amount,
                        f"exp:{lot.id}",
                        reference_type="credit_lot",
                        reference_id=str(lot.id),
                        memo_public="Unused credit expired",
                    )
                ],
                actor=SYSTEM_ACTOR,
                now=now,
            )
            lot.expired_entry_id = result.entry_id
            total += amount
    return total


async def _post_pending_usage(session_factory: Any, tenant_id: uuid.UUID, now: datetime) -> None:
    """Post the account's charged-but-unposted usage first, so an expiry judges the balance
    that usage already used up (credit it consumed must not expire a second time, taking the
    later posting from paid money instead)."""
    async with session_factory() as session:
        account_id = (
            await session.execute(
                select(BillingAccount.id).where(BillingAccount.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()
    if account_id is None:
        return
    moved = False
    while True:
        result = await posting.post_account(session_factory, account_id, now=now)
        if result is not None and result.minor:
            moved = True
        if result is None or result.rows < posting.BATCH_LIMIT:
            break
    if moved:
        try:
            await hooks.after_balance_change(session_factory, [account_id])
        except Exception as exc:
            logger.warning("Balance hook failed after posting (%s)", type(exc).__name__)


async def _period_for(session: Any, account_id: uuid.UUID, charge_day: date, now: datetime) -> date:
    """The statement month a charge for `charge_day` is booked in: its own month, unless that
    month is already frozen into a statement (a catch-up after downtime), when it lands in the
    current month so a sent statement keeps adding up. The memo still names the period covered."""
    month = charge_day.replace(day=1)
    frozen = (
        await session.execute(
            select(BillingStatement.id).where(
                BillingStatement.account_id == account_id, BillingStatement.month == month
            )
        )
    ).first()
    return month_of(now) if frozen is not None else month


async def _charge_account(
    session_factory: Any, tenant_id: uuid.UUID, now: datetime
) -> ScheduleResult | None:
    today = now.astimezone(UTC).date()
    await _post_pending_usage(session_factory, tenant_id, now)
    async with session_factory() as session:
        account = await account_for_update(session, tenant_id)
        eff = await effective_pricing(session, tenant_id)
        f = eff.fields
        expired = await _expire_lots(session, account, now)
        charged: list[date] = []
        bearing = not eff.exempt and (f.subscription_fee_minor > 0 or f.included_credit_minor > 0)
        if not bearing:
            # No fee and no credit now: forget the schedule, so restoring a fee later starts a
            # fresh period instead of billing the months in between.
            account.next_charge_on = None
        else:
            if account.ledger_seq == 0 and account.currency != eff.currency:
                account.currency = eff.currency  # until the first entry the plan's currency rules
            if account.next_charge_on is None:
                first = first_charge_on(today, account.trial_ends_at)
                account.next_charge_on = first
                if account.period_anchor is None:
                    account.period_anchor = first
            for charge_day in due_dates(
                account.next_charge_on, today, f.billing_period, account.billing_day
            ):
                entries: list[NewEntry] = []
                period = await _period_for(session, account.id, charge_day, now)
                # TV3 notice: each period is charged at the price in force when it started
                fd = (
                    await effective_pricing(session, tenant_id, at=ist_midnight(charge_day))
                ).fields
                # /TV3 notice
                if fd.subscription_fee_minor > 0:
                    entries.append(
                        NewEntry(
                            EntryType.SUBSCRIPTION_CHARGE,
                            -fd.subscription_fee_minor,
                            f"sub:{charge_day.isoformat()}",
                            reference_type="subscription_period",
                            reference_id=charge_day.isoformat(),
                            memo_public=_label(fd.billing_period, charge_day),
                            period=period,
                        )
                    )
                if fd.included_credit_minor > 0:
                    next_day = add_period(charge_day, fd.billing_period, account.billing_day)
                    entries.append(
                        NewEntry(
                            EntryType.INCLUDED_CREDIT,
                            fd.included_credit_minor,
                            f"inc:{charge_day.isoformat()}",
                            reference_type="subscription_period",
                            reference_id=charge_day.isoformat(),
                            memo_public="Included credit",
                            period=period,
                            expires_at=_midnight(next_day) if fd.included_credit_expires else None,
                        )
                    )
                if entries:
                    await append_entries(session, account.id, entries, actor=SYSTEM_ACTOR, now=now)
                charged.append(charge_day)
                account.next_charge_on = add_period(
                    charge_day, fd.billing_period, account.billing_day
                )
        # a lot granted by the catch-up above whose own date is already past expires now,
        # not an hour later after usage has spent it first
        expired += await _expire_lots(session, account, now)
        if not charged and not expired:
            await session.commit()  # next_charge_on may have moved
            return None
        await session.commit()
        return ScheduleResult(account.id, charged, expired)


async def run_due(session_factory: Any, *, now: datetime | None = None) -> list[ScheduleResult]:
    """The hourly pass: expire lots that reached their date, charge the subscription and grant
    the included credit of every period that is due. One transaction per workspace; a failing
    workspace is logged and the others go on. After each commit the balance hook runs."""
    if not billing_enabled():
        return []
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        from callobuzz_rag.db.models.tenant import Tenant

        tenant_ids = list(
            (await session.execute(select(Tenant.id).where(Tenant.is_system.is_(False))))
            .scalars()
            .all()
        )
        with_lots = set(
            (
                await session.execute(
                    select(BillingAccount.tenant_id)
                    .join(CreditLot, CreditLot.account_id == BillingAccount.id)
                    .where(CreditLot.remaining_minor > 0, CreditLot.expires_at <= moment)
                )
            )
            .scalars()
            .all()
        )
        priced = set(await _tenants_with_money_terms(session))
    results: list[ScheduleResult] = []
    for tenant_id in tenant_ids:
        if tenant_id not in priced and tenant_id not in with_lots:
            continue
        try:
            result = await _charge_account(session_factory, tenant_id, moment)
        except Exception as exc:
            logger.error(
                "Billing schedule for tenant %s failed (%s)", tenant_id, type(exc).__name__
            )
            continue
        if result is not None:
            results.append(result)
    if results:
        try:
            await hooks.after_balance_change(session_factory, [r.account_id for r in results])
        except Exception as exc:
            logger.warning(
                "Balance hook failed after the billing schedule (%s)", type(exc).__name__
            )
    return results


async def _tenants_with_money_terms(session: Any) -> list[uuid.UUID]:
    """Workspaces whose plan prices a subscription or an included credit, or whose account
    overrides one: the only ones the schedule has anything to do for. A workspace on a plan with
    no such term and no override is skipped without a query each."""
    from callobuzz_rag.db.models.plan_pricing import PlanPricing
    from callobuzz_rag.db.models.tenant import Tenant

    by_plan = {
        row.plan_id
        for row in (
            await session.execute(
                select(PlanPricing.plan_id).where(
                    PlanPricing.is_current.is_(True),
                    (PlanPricing.subscription_fee_minor > 0)
                    | (PlanPricing.included_credit_minor > 0),
                )
            )
        ).all()
    }
    tenants = (
        {
            t.id
            for t in (
                await session.execute(
                    select(Tenant.id, Tenant.plan_id).where(Tenant.plan_id.in_(list(by_plan)))
                )
            ).all()
        }
        if by_plan
        else set()
    )
    # accounts that override a fee or credit, or that are mid-schedule (to stop it)
    overridden = {
        row.tenant_id
        for row in (
            await session.execute(
                select(BillingAccount.tenant_id).where(
                    (BillingAccount.next_charge_on.is_not(None))
                    | (BillingAccount.overrides.has_key("subscription_fee_minor"))
                    | (BillingAccount.overrides.has_key("included_credit_minor"))
                )
            )
        ).all()
    }
    return list(tenants | overridden)
