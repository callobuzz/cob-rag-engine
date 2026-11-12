"""Provider balance alerts (billing BIL1): every 15 minutes, compare each provider's balance with
its alert level and tell the super admin once per crossing.

`check_provider_balances` reads the balances past the cache, moves each row's `alert_state`
(`ok` -> `low` -> `empty`) and commits **before** it queues the e-mail and the webhook, so a
retry or a crash never sends twice. Only a worsening alerts (`ok` -> `low`, `low` -> `empty`,
`ok` -> `empty`); getting back to `ok` re-arms; a provider that could not be read (`unknown`)
neither alerts nor re-arms. The super admin's address is `RAG_SUPERADMIN_EMAIL`; without one the
mail is skipped with a log line and the webhook is still sent.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select

from callobuzz_rag.analytics import provider_balances as pb
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.worker.celery_app import celery_app

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

ALERT_EMAIL_EXPIRES_S: Final = 6 * 3600
_RANK: Final[dict[str, int]] = {"ok": 0, "low": 1, "empty": 2}

EnqueueEmail = Callable[..., None]
Dispatch = Callable[[WebhookEvent, dict[str, Any]], Awaitable[None]]


@dataclass
class CheckResult:
    alerts: int = 0
    mails: int = 0
    webhooks: int = 0
    rearmed: int = 0
    skipped_no_recipient: int = 0
    alerted: list[str] = field(default_factory=list)


def _money(micro: int | None) -> str:
    if micro is None:
        return "unknown"
    sign = "-" if micro < 0 else ""
    cents = (abs(micro) + 5_000) // 10_000
    return f"{sign}${cents // 100:,}.{cents % 100:02d}"


async def _row_for(session: "AsyncSession", b: pb.ProviderBalance) -> Any:
    """The stored row carrying this provider's alert state, locked `FOR UPDATE` until the
    commit so an overlapping run (or a PUT) waits and sees the state this one leaves. Created
    for an API provider that has none yet (a conflict-safe insert), so its first alert is
    remembered."""
    from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting

    if b.id is not None:
        import uuid

        return (
            await session.execute(
                select(ProviderBalanceSetting)
                .where(ProviderBalanceSetting.id == uuid.UUID(b.id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
    row, _ = await pb.ensure_row(session, b.provider, b.label, lock=True)
    return row


async def run_checks(
    factory: "async_sessionmaker[AsyncSession]",
    settings: "Settings",
    *,
    dispatch: Dispatch,
    enqueue: EnqueueEmail,
    now: datetime | None = None,
    balances: Callable[..., Awaitable[list[pb.ProviderBalance]]] = pb.provider_balances,
) -> CheckResult:
    from callobuzz_rag.accounts.email.render import dashboard_link

    now = now or datetime.now(UTC)
    result = CheckResult()
    pending: list[Mapping[str, Any]] = []

    async with factory() as session:
        for b in await balances(settings, session, refresh=True):
            if b.status in ("unknown", "not_configured"):
                continue
            row = await _row_for(session, b)
            if row is None:
                continue
            previous = row.alert_state or "ok"
            if b.status == "ok":
                if previous != "ok":
                    row.alert_state = "ok"
                    result.rearmed += 1
                continue
            row.alert_state = b.status
            if _RANK[b.status] > _RANK.get(previous, 0):
                row.alerted_at = now
                pending.append(
                    {
                        "provider": b.provider,
                        "label": b.label,
                        "status": b.status,
                        "remaining": b.remaining_usd_micro,
                        "threshold": b.low_threshold_usd_micro,
                    }
                )
        await session.commit()

    recipient = (settings.superadmin_email or "").strip()
    costs_url = dashboard_link(settings.dashboard_url, "/admin/costs")
    for alert in pending:
        result.alerts += 1
        result.alerted.append(f"{alert['provider']}:{alert['status']}")
        if recipient:
            try:
                enqueue(
                    "provider_low_balance",
                    recipient,
                    {
                        "provider_label": alert["label"],
                        "status": alert["status"],
                        "remaining": _money(alert["remaining"]),
                        "threshold": _money(alert["threshold"])
                        if alert["threshold"] is not None
                        else "none set",
                        "costs_url": costs_url,
                    },
                    expires_s=ALERT_EMAIL_EXPIRES_S,
                )
                result.mails += 1
            except Exception as exc:
                logger.error("Provider alert mail not queued (%s)", type(exc).__name__)
        else:
            result.skipped_no_recipient += 1
            logger.warning(
                "%s balance is %s but RAG_SUPERADMIN_EMAIL is not set: no mail sent",
                alert["label"],
                alert["status"],
            )
        try:
            await dispatch(
                WebhookEvent.BILLING_PROVIDER_LOW_BALANCE,
                {
                    "provider": alert["provider"],
                    "status": alert["status"],
                    "remaining_usd": _usd(alert["remaining"]),
                    "threshold_usd": _usd(alert["threshold"]),
                },
            )
            result.webhooks += 1
        except Exception as exc:
            logger.error("Provider alert webhook failed (%s)", type(exc).__name__)
    return result


def _usd(micro: int | None) -> str | None:
    """A decimal string ("3.200000") for the webhook body; money is never a float."""
    if micro is None:
        return None
    sign = "-" if micro < 0 else ""
    whole, frac = divmod(abs(micro), 1_000_000)
    return f"{sign}{whole}.{frac:06d}"


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.check_provider_balances", queue="periodic", ignore_result=True
)
def check_provider_balances_task() -> dict[str, int]:
    return asyncio.run(_check())


async def _check() -> dict[str, int]:
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.core.webhooks import WebhookDispatcher
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)
        dispatcher = WebhookDispatcher(settings, factory)
        result = await run_checks(
            factory, settings, dispatch=dispatcher.dispatch, enqueue=enqueue_email
        )
    finally:
        await engine.dispose()
    return {
        "alerts": result.alerts,
        "mails": result.mails,
        "webhooks": result.webhooks,
        "rearmed": result.rearmed,
    }
