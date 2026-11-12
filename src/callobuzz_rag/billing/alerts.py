"""Low-balance levels and pause transitions, decided from a locked billing account (BIL4).

`alert_level` and `plan_transition` are pure. `snapshot` reads one account under a row lock
(`SELECT ... FOR UPDATE`), so two hooks running at once on the same account decide one after the
other: the second sees the first's `alert_level`/`paused_at` and sends nothing again.

Basis of the percentages: a prepaid account's `max(last top-up, included credit)` (what the owner
thinks of as "full"), a postpaid account's credit limit, whose remaining part is
`credit_limit + available`. One notice per downward crossing: `alert_level` stores the lowest
level already sent and is re-armed once the amount is back above the highest threshold.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sqlalchemy import select

from callobuzz_rag.billing import ledger
from callobuzz_rag.billing.enforcement import NOT_BILLED, BillingState, account_state
from callobuzz_rag.billing.money import Currency
from callobuzz_rag.billing.pricing import EffectivePricing, effective_pricing
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def alert_basis(
    *,
    payment_type: str,
    credit_limit_minor: int,
    included_credit_minor: int,
    last_top_up_minor: int,
) -> int:
    if payment_type == "postpaid":
        return credit_limit_minor
    return max(last_top_up_minor, included_credit_minor)


def alert_level(
    available: int, basis: int, thresholds: Sequence[int], *, paused: bool
) -> int | None:
    """The lowest threshold (percent) the amount has reached, or None when it is above all of
    them. A threshold `p > 0` is reached when `available <= basis * p / 100`; 0 means paused."""
    reached: list[int] = []
    for p in thresholds:
        if p == 0:
            if paused:
                reached.append(0)
        elif available * 100 <= basis * p:
            reached.append(p)
    return min(reached) if reached else None


@dataclass(frozen=True)
class Transition:
    paused_at_set: bool = False  # the workspace just became paused
    paused_at_cleared: bool = False  # ... or just resumed
    low_level: int | None = None  # send the low-balance notice for this level
    new_alert_level: int | None = None  # what `alert_level` becomes


def plan_transition(
    *, was_paused: bool, previous_level: int | None, paused: bool, level: int | None
) -> Transition:
    """What changes for one account given what was stored and what is true now."""
    set_paused = paused and not was_paused
    cleared = was_paused and not paused
    if paused:
        return Transition(paused_at_set=set_paused, new_alert_level=0)
    if cleared:
        # Resuming into a band stores the real band and sends no low notice on the way up.
        return Transition(paused_at_cleared=True, new_alert_level=level)
    # Not paused: a downward crossing of a threshold above 0 sends one notice; rising back above
    # the highest threshold (level None) re-arms; rising inside the thresholds keeps the memory.
    if level is None:
        return Transition(paused_at_cleared=cleared, new_alert_level=None)
    if previous_level is None or level < previous_level:
        return Transition(paused_at_cleared=cleared, low_level=level, new_alert_level=level)
    return Transition(paused_at_cleared=cleared, new_alert_level=previous_level)


NoticeKind = Literal["low", "paused"]


@dataclass(frozen=True)
class Snapshot:
    account: BillingAccount
    tenant_id: uuid.UUID
    tenant_name: str
    state: BillingState
    level: int | None  # the threshold reached now (0 = paused), None above all
    shown_minor: int  # what the notices say is left (a postpaid account: the remaining credit)
    currency: Currency
    payment_type: str


async def snapshot(session: "AsyncSession", account_id: uuid.UUID) -> Snapshot | None:
    """The account (locked), its workspace and the facts the transition needs. None when the
    account or workspace is gone, or the workspace is not billed and holds no stale state."""
    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if account is None:
        return None
    tenant = await session.get(Tenant, account.tenant_id)
    if tenant is None:
        return None
    eff = await effective_pricing(session, account.tenant_id)
    if eff.exempt:
        return unbilled_snapshot(account, tenant, eff)
    owed = await ledger.owed_micro(session, account)
    available = ledger.available_minor_of(int(account.balance_minor), owed)
    return build_snapshot(account, tenant, eff, available)


def unbilled_snapshot(
    account: BillingAccount, tenant: Tenant, eff: EffectivePricing
) -> Snapshot | None:
    """A workspace that is no longer billed (exempt, or a plan that charges nobody) but still
    carries stored pause or alert state: a snapshot that is not paused and has no level, so the
    stored state is cleared (resumed, no notice). None when nothing stale is stored."""
    if account.paused_at is None and account.alert_level is None:
        return None
    return Snapshot(
        account=account,
        tenant_id=account.tenant_id,
        tenant_name=tenant.name,
        state=NOT_BILLED,
        level=None,
        shown_minor=0,
        currency=eff.currency,
        payment_type=eff.fields.payment_type,
    )


def build_snapshot(
    account: BillingAccount, tenant: Tenant, eff: EffectivePricing, available: int
) -> Snapshot | None:
    """The pure half of `snapshot`. A workspace billed for neither payer has no alerts, but a
    forced pause still counts (the operator asked for it)."""
    state = account_state(eff, account, available)
    if not state.billed and not state.paused:
        return unbilled_snapshot(account, tenant, eff)
    f = eff.fields
    shown = available + f.credit_limit_minor if f.payment_type == "postpaid" else available
    level: int | None = None
    if state.billed:
        basis = alert_basis(
            payment_type=f.payment_type,
            credit_limit_minor=f.credit_limit_minor,
            included_credit_minor=f.included_credit_minor,
            last_top_up_minor=int(account.last_top_up_minor or 0),
        )
        level = alert_level(shown, basis, f.alert_thresholds_pct, paused=state.paused)
    elif state.paused:
        level = 0
    return Snapshot(
        account=account,
        tenant_id=account.tenant_id,
        tenant_name=tenant.name,
        state=state,
        level=level,
        shown_minor=shown,
        currency=eff.currency,
        payment_type=f.payment_type,
    )
