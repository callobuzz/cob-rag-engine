"""The renewal reminder (TV3): a few days before a workspace renews, its owners get one e-mail
with the exact amount.

`renewal_pass` runs daily at 09:00 India time (beat, `worker/trust_tasks.py`). A workspace is
reminded when its `next_charge_on` is exactly `trust.renewal_reminder_days` away, or one day
nearer when yesterday's run missed it; a `billing_notices` row (`renewal`, the renewal date)
makes it once per renewal, however often the pass runs. The row is written in the same
transaction as the queueing: a queue that fails writes nothing, so the next run tries again.

The amount is the fee of the pricing version in force on the renewal day, with the customer's own
terms on top, so a raise that starts before the renewal shows the new amount and says so. A
workspace that is not billed (the deployer's own, COB's, an Internal plan, a plan with no fee), a
suspended one and a switched-off reminder (`0` days) get nothing.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from callobuzz_rag.accounts.settings_store import EngineSettings, get_engine_settings
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.notice import (
    ist_date,
    ist_midnight,
    long_date,
    money_text,
    pending_effect,
    period_start,
    plan_versions,
    version_in_force,
)
from callobuzz_rag.billing.pricing import build_effective
from callobuzz_rag.billing.summary import pending_applies_by, renewal_terms
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

EMAIL_EXPIRES_S = 3 * 24 * 3600
Enqueue = Callable[..., None]


async def _remind_one(
    session_factory: Any,
    account_id: uuid.UUID,
    *,
    now: datetime,
    product_name: str,
    dashboard_url: str,
    enqueue: Enqueue,
) -> bool:
    """Queue the reminder for one account when it is due and not yet sent. True when sent."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.accounts.lifecycle import owner_emails

    async with session_factory() as session:
        account = (
            await session.execute(select(BillingAccount).where(BillingAccount.id == account_id))
        ).scalar_one_or_none()
        if account is None or account.next_charge_on is None:
            return False
        tenant = await session.get(Tenant, account.tenant_id)
        if tenant is None or tenant.status != "active" or tenant.is_operator or tenant.is_system:
            return False
        plan = await session.get(Plan, tenant.plan_id)
        if plan is None or plan.is_internal:
            return False
        versions = await plan_versions(session, plan.id)
        start = period_start(account, now, billing_period="monthly")
        in_force = version_in_force(versions, start)
        if in_force is None:
            return False
        # the period length decides the start; re-read it with the real one, and when it does
        # not fit the schedule (a change of period length) with the one the workspace paid
        start = period_start(
            account,
            now,
            billing_period=in_force.billing_period,
            alternatives=[v.billing_period for v in sorted(versions, key=lambda v: -v.version)],
        )
        in_force = version_in_force(versions, start) or in_force
        eff = build_effective(tenant, plan, in_force, account)
        if eff.exempt:
            return False
        held = None
        if pending_applies_by(account):
            from types import SimpleNamespace

            held = await pending_effect(
                session,
                SimpleNamespace(account=account, pricing=in_force, effective=eff, plan=plan),
                at=ist_midnight(account.next_charge_on),
            )
        terms = renewal_terms(
            versions,
            renews_on=account.next_charge_on,
            overrides=account.overrides,
            in_force=in_force,
            pending=held[0] if held is not None else None,
        )
        if terms is None or terms.amount_minor <= 0:
            return False
        owners = await owner_emails(session, tenant.id)
        if not owners:
            return False
        key = account.next_charge_on.isoformat()
        already = (
            await session.execute(
                select(BillingNotice.id).where(
                    BillingNotice.tenant_id == tenant.id,
                    BillingNotice.kind == "renewal",
                    BillingNotice.key == key,
                )
            )
        ).first()
        if already is not None:
            return False  # sent for this renewal date
        context = {
            "business_name": tenant.name,
            "product_name": product_name,
            "renews_on": long_date(account.next_charge_on),
            "amount": money_text(terms.amount_minor, eff.currency),
            "plan_name": held[1].name if held is not None and held[1] is not None else plan.name,
            "included": (
                money_text(terms.included_credit_minor, eff.currency)
                if terms.included_credit_minor > 0
                else ""
            ),
            "new_price": terms.new_price_from is not None,
            "billing_url": dashboard_link(dashboard_url, "/plan"),
        }
        for to in owners:
            enqueue("billing_renewal_reminder", to, context, expires_s=EMAIL_EXPIRES_S)
        # The row is written only after every mail was queued: a queue that fails leaves none,
        # so tomorrow's run tries again. (The unique key keeps a racing second run to one row.)
        await session.execute(
            pg_insert(BillingNotice)
            .values(tenant_id=tenant.id, kind="renewal", key=key)
            .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "key"])
        )
        await session.commit()
        return True


async def renewal_pass(
    session_factory: Any,
    now: datetime,
    *,
    enqueue: Enqueue | None = None,
    engine_settings: EngineSettings | None = None,
    dashboard_url: str | None = None,
) -> dict[str, int]:
    """Remind every workspace that renews `renewal_reminder_days` from now (India's date).
    Returns `{"sent", "failed"}`. One workspace failing never stops the others."""
    if not billing_enabled():
        return {"sent": 0, "failed": 0}
    engine = engine_settings or await get_engine_settings(session_factory)
    days = engine.trust.renewal_reminder_days
    if days <= 0:
        return {"sent": 0, "failed": 0}
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    if dashboard_url is None:
        from callobuzz_rag.core.config import get_settings

        dashboard_url = get_settings().dashboard_url
    today = ist_date(now)
    due_days = [today + timedelta(days=days)]
    if days > 1:
        due_days.append(today + timedelta(days=days - 1))  # yesterday's run may have missed it
    async with session_factory() as session:
        ids = list(
            (
                await session.execute(
                    select(BillingAccount.id).where(BillingAccount.next_charge_on.in_(due_days))
                )
            )
            .scalars()
            .all()
        )
    sent = failed = 0
    for account_id in ids:
        try:
            if await _remind_one(
                session_factory,
                account_id,
                now=now,
                product_name=engine.branding.product_name,
                dashboard_url=dashboard_url,
                enqueue=enqueue,
            ):
                sent += 1
        except Exception:
            failed += 1
            logger.warning("Renewal reminder for account %s failed", account_id, exc_info=True)
    return {"sent": sent, "failed": failed}
