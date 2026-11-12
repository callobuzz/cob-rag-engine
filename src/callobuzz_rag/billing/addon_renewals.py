"""Monthly add-ons renew from the balance (P5).

A grant that `renews` (a monthly own-key unlock, a monthly service such as priority support) is
charged again when its period ends: one `ADD_ON_PURCHASE` entry keyed `addon:<grant id>:<period
start>`, then `ends_at` moves one period on and `renewed_through` records the date. It runs inside
the hourly billing schedule, one transaction per grant under the account lock, and is idempotent
(I4): a rerun, or a catch-up after the beat was down (each missed period is charged once, in
order), never charges a period twice.

It never pauses a workspace (D17): when the balance cannot cover the price without reaching the
pause line (the same rule as buying from the balance, `addons.past_the_pause_line`), the grant is
left to end at its `ends_at`, marked cancelled so it is not tried again, and the owners are told
once (`add_on_ended`). With billing off nothing is charged: a renewing grant is simply extended.
A grant the owner cancelled or the operator revoked is never renewed.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select

from callobuzz_rag.billing import accounts, addons, hooks, ledger
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.subscriptions import add_period
from callobuzz_rag.db.models.add_on import AddOnGrant

logger = logging.getLogger(__name__)

MAX_PERIODS_PER_GRANT: Final = 36  # a catch-up never loops unboundedly; the next hour continues
BATCH: Final = 500
LOW_BALANCE: Final = "Your balance was too low to renew it."
CURRENCY_CHANGED: Final = "Your billing currency changed, so it could not be renewed."
Enqueue = Callable[..., None]


@dataclass(frozen=True, slots=True)
class Ended:
    """A renewing add-on that was not renewed, for the e-mail to the owners."""

    tenant_id: uuid.UUID
    add_on_name: str
    reason: str


@dataclass(slots=True)
class RenewalResult:
    renewed: int = 0  # periods charged or extended
    charged_accounts: list[uuid.UUID] = field(default_factory=list)
    ended: list[Ended] = field(default_factory=list)


def next_end(old_end: datetime) -> datetime:
    """The end of the period after the one that ended at `old_end`: the same day one month on.
    Always monthly, whatever the account's own billing period (the price is per month)."""
    day = old_end.date()
    following = add_period(day, "monthly", day.day)
    return datetime(following.year, following.month, following.day, tzinfo=UTC)


def _due(grant: AddOnGrant, moment: datetime) -> bool:
    return (
        grant.renews
        and grant.revoked_at is None
        and grant.cancelled_at is None
        and grant.ends_at is not None
        and grant.ends_at <= moment
    )


async def _renew_one(
    session_factory: Any, grant_id: uuid.UUID, moment: datetime
) -> tuple[int, uuid.UUID | None, Ended | None]:
    """(periods renewed, the account to re-check after a charge, why it ended)."""
    async with session_factory() as session:
        # the tenant first (a column, nothing cached), then the account lock, then the grant:
        # what another worker changed while this one waited for the lock is what it reads
        tenant_id = (
            await session.execute(select(AddOnGrant.tenant_id).where(AddOnGrant.id == grant_id))
        ).scalar_one_or_none()
        if tenant_id is None:
            return 0, None, None
        account = await accounts.account_for_update(session, tenant_id)
        grant = await session.get(AddOnGrant, grant_id)
        if grant is None or not _due(grant, moment):
            return 0, None, None
        ctx = await accounts.load_context(session, tenant_id)
        fields = ctx.effective.fields
        charge = billing_enabled() and not ctx.effective.exempt and grant.price_minor > 0
        renewed = 0
        charged = False
        ended: Ended | None = None
        while grant.ends_at is not None and grant.ends_at <= moment:
            if renewed >= MAX_PERIODS_PER_GRANT:
                break
            old_end = grant.ends_at
            if charge:
                reason = None
                if grant.currency != ctx.effective.currency:
                    reason = CURRENCY_CHANGED
                else:
                    available = await ledger.available_minor(session, account)
                    if addons.past_the_pause_line(fields, available, grant.price_minor):
                        reason = LOW_BALANCE
                if reason is not None:
                    grant.cancelled_at = moment  # never tried again; it ends at `ends_at`
                    ended = Ended(tenant_id, grant.name, reason)
                    break
                await ledger.append_entries(
                    session,
                    account.id,
                    [
                        ledger.NewEntry(
                            ledger.EntryType.ADD_ON_PURCHASE,
                            -grant.price_minor,
                            f"addon:{grant.id}:{old_end.date().isoformat()}",
                            reference_type="add_on_grant",
                            reference_id=str(grant.id),
                            memo_public=f"Add-on renewed: {grant.name}"[:200],
                        )
                    ],
                    actor=ledger.SYSTEM_ACTOR,
                    now=moment,
                )
                charged = True
            grant.ends_at = next_end(old_end)
            grant.renewed_through = old_end.date()
            renewed += 1
        await session.commit()
        return renewed, account.id if charged else None, ended


async def renew_due(session_factory: Any, *, now: datetime | None = None) -> RenewalResult:
    """Renew every grant whose period has ended. One transaction per grant: a failure on one
    is logged and the others go on. The caller sends `result.ended` notices."""
    moment = now or datetime.now(UTC)
    result = RenewalResult()
    async with session_factory() as session:
        ids = list(
            (
                await session.execute(
                    select(AddOnGrant.id)
                    .where(
                        AddOnGrant.renews.is_(True),
                        AddOnGrant.revoked_at.is_(None),
                        AddOnGrant.cancelled_at.is_(None),
                        AddOnGrant.ends_at <= moment,
                    )
                    .order_by(AddOnGrant.ends_at)
                    .limit(BATCH)
                )
            )
            .scalars()
            .all()
        )
    for grant_id in ids:
        try:
            renewed, account_id, ended = await _renew_one(session_factory, grant_id, moment)
        except Exception as exc:
            logger.error("Add-on renewal %s failed (%s)", grant_id, type(exc).__name__)
            continue
        result.renewed += renewed
        if account_id is not None:
            result.charged_accounts.append(account_id)
        if ended is not None:
            result.ended.append(ended)
    if result.charged_accounts:
        try:
            await hooks.after_balance_change(session_factory, result.charged_accounts)
        except Exception as exc:
            logger.warning("Balance hook failed after add-on renewals (%s)", type(exc).__name__)
    return result


async def send_ended_notices(
    session_factory: Any, ended: list[Ended], *, enqueue: Enqueue, dashboard_url: str
) -> int:
    """Tell each workspace's owners that an add-on ended (once: the grant is marked, so it is
    never in a later run's list). Never raises; returns how many mails were queued."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails
    from callobuzz_rag.db.models.tenant import Tenant

    sent = 0
    for item in ended:
        try:
            async with session_factory() as session:
                tenant = await session.get(Tenant, item.tenant_id)
                if tenant is None:
                    continue
                recipients = await owner_emails(session, item.tenant_id)
            context = {
                "tenant_name": tenant.name,
                "add_on_name": item.add_on_name,
                "reason": item.reason,
                "shop_url": dashboard_link(dashboard_url, "/plan"),
            }
            for to in recipients:
                enqueue("add_on_ended", to, context, expires_s=7 * 24 * 3600)
                sent += 1
        except Exception:
            logger.warning("Add-on ended mail was not queued", exc_info=True)
    return sent
