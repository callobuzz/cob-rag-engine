"""Called after every committed change of a balance (posting, subscriptions, expiry, manual
entries, add-on grants, an account PATCH): pause and resume transitions and the low-balance
notices (BIL4).

Per account, in its own transaction: lock the row, compare what is stored (`paused_at`,
`alert_level`) with what is true now, write the new values and the audit row (`billing.paused`
and `billing.resumed`), commit, and only then queue the e-mails and the webhook (mail is never
sent inside the money transaction). A hook that fails never fails the caller's change: it logs.
Enforcement itself is always live (`billing/enforcement.py`); this only drives the notices.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from callobuzz_rag.accounts.audit import Actor, record_audit
from callobuzz_rag.billing import alerts
from callobuzz_rag.billing.config import billing_enabled
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR
from callobuzz_rag.billing.money import Money
from callobuzz_rag.core.events import WebhookEvent

logger = logging.getLogger(__name__)

ALERT_EMAIL_EXPIRES_S = 24 * 3600

Dispatch = Callable[[WebhookEvent, dict[str, Any]], Awaitable[Any]]
EnqueueEmail = Callable[..., Any]


@dataclass(frozen=True)
class Notice:
    kind: alerts.NoticeKind
    tenant_id: uuid.UUID
    tenant_name: str
    level_pct: int | None
    shown_minor: int
    available_minor: int
    currency: str
    payment_type: str
    reason: str
    owners: list[str] = field(default_factory=list)


async def _owner_emails(session: Any, tenant_id: uuid.UUID) -> list[str]:
    from callobuzz_rag.accounts.lifecycle import owner_emails

    return await owner_emails(session, tenant_id)


async def _transition(
    session_factory: Any, account_id: uuid.UUID, *, actor: Actor, now: datetime
) -> list[Notice]:
    async with session_factory() as session:
        snap = await alerts.snapshot(session, account_id)
        if snap is None:
            return []
        account = snap.account
        plan = alerts.plan_transition(
            was_paused=account.paused_at is not None,
            previous_level=account.alert_level,
            paused=snap.state.paused,
            level=snap.level,
        )
        if (
            not plan.paused_at_set
            and not plan.paused_at_cleared
            and plan.low_level is None
            and plan.new_alert_level == account.alert_level
        ):
            return []
        details = {
            "reason": snap.state.reason,
            "available_minor": snap.state.available_minor,
            "currency": snap.currency,
        }
        if plan.paused_at_set:
            account.paused_at = now
        if plan.paused_at_cleared:
            account.paused_at = None
        account.alert_level = plan.new_alert_level
        for action, wanted in (
            ("billing.paused", plan.paused_at_set),
            ("billing.resumed", plan.paused_at_cleared),
        ):
            if wanted:
                await record_audit(
                    session,
                    None,
                    action,
                    tenant_id=snap.tenant_id,
                    tenant_label=snap.tenant_name,
                    target_type="billing_account",
                    target_id=str(account.id),
                    details=details,
                    actor=actor,
                )
        notices: list[Notice] = []
        kind: alerts.NoticeKind | None = "paused" if plan.paused_at_set else None
        if plan.low_level is not None:
            kind = "low"
        if kind is not None:
            notices.append(
                Notice(
                    kind=kind,
                    tenant_id=snap.tenant_id,
                    tenant_name=snap.tenant_name,
                    level_pct=plan.low_level,
                    shown_minor=snap.shown_minor,
                    available_minor=snap.state.available_minor or 0,
                    currency=snap.currency,
                    payment_type=snap.payment_type,
                    reason=snap.state.reason,
                    owners=await _owner_emails(session, snap.tenant_id),
                )
            )
        await session.commit()
    return notices


async def after_balance_change(
    session_factory: Any,
    account_ids: Sequence[uuid.UUID],
    *,
    actor: Actor = SYSTEM_ACTOR,
    now: datetime | None = None,
    dispatch: Dispatch | None = None,
    enqueue: EnqueueEmail | None = None,
) -> None:
    """Re-check each account; send what the transitions call for after their commits."""
    if not billing_enabled() or session_factory is None:
        return
    moment = now or datetime.now(UTC)
    # P6 plan choice: money that just arrived may pay for a plan change that waits for it
    try:
        from callobuzz_rag.billing.plan_change import settle_awaiting

        await settle_awaiting(session_factory, account_ids, now=moment)
    except Exception:
        logger.warning("Settling waiting plan changes failed", exc_info=True)
    # /P6 plan choice
    notices: list[Notice] = []
    for account_id in dict.fromkeys(account_ids):
        try:
            notices += await _transition(session_factory, account_id, actor=actor, now=moment)
        except Exception:
            logger.warning("Billing transition failed for account %s", account_id, exc_info=True)
    if notices:
        await notify(session_factory, notices, dispatch=dispatch, enqueue=enqueue)


async def recheck_all(session_factory: Any, *, now: datetime | None = None) -> int:
    """The hourly pass: every billing account is looked at again, so a pricing change (a new
    grace, new thresholds) moves a workspace across a line without a balance change."""
    if not billing_enabled():
        return 0
    from callobuzz_rag.db.models.billing_account import BillingAccount

    async with session_factory() as session:
        ids = list((await session.execute(select(BillingAccount.id))).scalars().all())
    for start in range(0, len(ids), 200):
        await after_balance_change(session_factory, ids[start : start + 200], now=now)
    return len(ids)


async def notify(
    session_factory: Any,
    notices: Sequence[Notice],
    *,
    dispatch: Dispatch | None = None,
    enqueue: EnqueueEmail | None = None,
) -> None:
    """E-mail and webhook for committed transitions. Every step is best effort."""
    from callobuzz_rag.accounts.email.render import dashboard_link
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    if enqueue is None:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email

        enqueue = enqueue_email
    if dispatch is None:
        from callobuzz_rag.core.webhooks import WebhookDispatcher

        dispatch = WebhookDispatcher(settings, session_factory).dispatch
    billing_url = dashboard_link(
        settings.dashboard_url, "/plan"
    )  # BIL6: the owner page is Plan & billing
    support = await _support_email(session_factory) or (settings.superadmin_email or "")
    operator = (settings.superadmin_email or "").strip()
    for n in notices:
        recipients = list(n.owners)
        if n.kind == "paused" and operator and operator not in recipients:
            recipients.append(operator)
        for to in recipients:
            try:
                enqueue(
                    "billing_paused" if n.kind == "paused" else "billing_low_balance",
                    to,
                    _context(n, billing_url, support),
                    expires_s=ALERT_EMAIL_EXPIRES_S,
                )
            except Exception as exc:
                logger.error("Billing notice mail not queued (%s)", type(exc).__name__)
        try:
            await dispatch(*_webhook(n))
        except Exception as exc:
            logger.error("Billing notice webhook failed (%s)", type(exc).__name__)


def _context(n: Notice, billing_url: str, support: str) -> Mapping[str, Any]:
    if n.kind == "paused":
        return {
            "tenant_name": n.tenant_name,
            "reason": n.reason,
            "billing_url": billing_url,
            "support_email": support,
        }
    return {
        "tenant_name": n.tenant_name,
        "level_pct": n.level_pct,
        "balance": Money(n.shown_minor, n.currency).format(),  # type: ignore[arg-type]
        "payment_type": n.payment_type,
        "billing_url": billing_url,
    }


def _webhook(n: Notice) -> tuple[WebhookEvent, dict[str, Any]]:
    base: dict[str, Any] = {
        "tenant_id": str(n.tenant_id),
        "tenant_name": n.tenant_name,
        "available_minor": n.available_minor,
        "currency": n.currency,
    }
    if n.kind == "paused":
        return WebhookEvent.BILLING_PAUSED, {**base, "reason": n.reason}
    return WebhookEvent.BILLING_LOW_BALANCE, {
        **base,
        "level_pct": n.level_pct,
        "payment_type": n.payment_type,
    }


async def _support_email(session_factory: Any) -> str:
    try:
        from callobuzz_rag.accounts.settings_store import get_engine_settings

        return str((await get_engine_settings(session_factory)).branding.support_email or "")
    except Exception:
        return ""
