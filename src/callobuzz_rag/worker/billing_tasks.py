"""Billing beat tasks (BIL3): the minute posting of usage charges, the hourly subscription run
and the daily reconciliation. Each opens its own engine, like the other periodic tasks."""

import asyncio
import logging
from typing import Any

from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.post_usage_charges", queue="periodic", ignore_result=True
)
def post_usage_charges_task() -> dict[str, int]:
    return asyncio.run(_post())


async def _post() -> dict[str, int]:
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.billing.posting import post_pending
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    if not billing_enabled():
        return {"accounts": 0, "rows": 0, "minor": 0}
    engine = create_engine(get_settings())
    try:
        results = await post_pending(create_session_factory(engine))
    finally:
        await engine.dispose()
    return {
        "accounts": len({r.account_id for r in results}),
        "rows": sum(r.rows for r in results),
        "minor": sum(r.minor for r in results),
    }


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.run_billing_schedule", queue="periodic", ignore_result=True
)
def run_billing_schedule_task() -> dict[str, int]:
    return asyncio.run(_schedule())


async def _schedule() -> dict[str, int]:
    from callobuzz_rag.billing.subscriptions import run_due
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    engine = create_engine(get_settings())
    try:
        factory = create_session_factory(engine)
        results = await run_due(factory)
        renewals = await _renew_add_ons(factory)  # P5 plans
        rechecked = await _recheck_all(factory)  # BIL4 alerts
    finally:
        await engine.dispose()
    return {
        "accounts": len(results),
        "periods": sum(len(r.charged) for r in results),
        "expired_minor": sum(r.expired_minor for r in results),
        "rechecked": rechecked,  # BIL4 alerts
        **renewals,  # P5 plans
    }


# P5 plans
async def _renew_add_ons(factory: Any) -> dict[str, int]:
    """Renew monthly add-ons from the balance, tell the owners of any that ended, and announce
    new service orders the purchase path could not (a gateway payment). Never fails the run."""
    try:
        from callobuzz_rag.accounts.email.dispatch import enqueue_email
        from callobuzz_rag.accounts.settings_store import get_engine_settings
        from callobuzz_rag.billing import addon_renewals, service_orders
        from callobuzz_rag.core.config import get_settings

        settings = get_settings()
        result = await addon_renewals.renew_due(factory)
        told = await addon_renewals.send_ended_notices(
            factory, result.ended, enqueue=enqueue_email, dashboard_url=settings.dashboard_url
        )
        engine = await get_engine_settings(factory)
        announced = await service_orders.notify_provider_of_new_orders(
            factory,
            enqueue=enqueue_email,
            support_email=str(engine.branding.support_email or ""),
            superadmin_email=settings.superadmin_email,
            dashboard_url=settings.dashboard_url,
        )
    except Exception:
        logger.warning("Add-on renewals failed", exc_info=True)
        return {"add_on_periods": 0, "add_ons_ended": 0, "orders_announced": 0}
    return {
        "add_on_periods": result.renewed,
        "add_ons_ended": len(result.ended),
        "orders_announced": announced,
        "add_on_mails": told,
    }


# /P5 plans


# BIL4 alerts
async def _recheck_all(factory: Any) -> int:
    """The hourly re-check of every billing account (a pricing change can pause or resume a
    workspace without a balance change). Never fails the schedule run."""
    from callobuzz_rag.billing import hooks

    try:
        return await hooks.recheck_all(factory)
    except Exception:
        logger.warning("Billing re-check failed", exc_info=True)
        return 0


# /BIL4 alerts


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.reconcile_billing", queue="periodic", ignore_result=True
)
def reconcile_billing_task() -> dict[str, int | bool]:
    return asyncio.run(_reconcile())


async def _reconcile() -> dict[str, int | bool]:
    from callobuzz_rag.billing import reconcile
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    try:
        factory = create_session_factory(engine)
        # P1 payments: R6 runs only when a payment provider is configured
        from callobuzz_rag.billing.payments.factory import provider_from_settings

        async with factory() as session:
            provider = await provider_from_settings(settings, session)
        result = await reconcile.run(factory, provider=provider)
        if result is not None and result.heals:
            from callobuzz_rag.billing.payments import capture

            for healed in result.heals:
                await capture.run_after_commit(factory, settings, healed)
        # /P1 payments
    finally:
        await engine.dispose()
    if result is None:
        return {"ran": False}
    return {"ran": True, "ok": result.ok, "findings": len(result.findings)}


# BIL6 statements
@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.snapshot_billing_statements", queue="periodic", ignore_result=True
)
def snapshot_billing_statements_task() -> dict[str, int]:
    return asyncio.run(_snapshot_statements())


async def _snapshot_statements() -> dict[str, int]:
    """On the 1st: run the subscription schedule, post what is still pending, then freeze every closed month that has no
    statement yet. Idempotent, so a rerun or a catch-up after downtime writes nothing twice."""
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.billing.posting import post_pending
    from callobuzz_rag.billing.statements import snapshot_closed_months
    from callobuzz_rag.billing.subscriptions import run_due
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    if not billing_enabled():
        return {"written": 0}
    engine = create_engine(get_settings())
    try:
        factory = create_session_factory(engine)
        await run_due(factory)  # late subscription entries first, so no month freezes without them
        await post_pending(factory)
        written = await snapshot_closed_months(factory)
    finally:
        await engine.dispose()
    return {"written": written}


# /BIL6 statements
