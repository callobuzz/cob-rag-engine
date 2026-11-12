"""Beat tasks of the no-surprises epic (TV3): the renewal reminder, scheduled price changes, and
pending per-workspace changes becoming due. All idempotent, so a rerun or a catch-up is safe."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


async def _with_session_factory(run: Callable[[Any], Awaitable[Any]]) -> Any:
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    engine = create_engine(get_settings())
    try:
        return await run(create_session_factory(engine))
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.billing_renewal_reminders", queue="periodic", ignore_result=True
)
def billing_renewal_reminders_task() -> dict[str, int]:
    """Daily at 09:00 India: remind the owners of workspaces that renew soon."""
    from datetime import UTC, datetime

    from callobuzz_rag.billing.renewal import renewal_pass

    async def run(factory: Any) -> dict[str, int]:
        return await renewal_pass(factory, datetime.now(UTC))

    result: dict[str, int] = asyncio.run(_with_session_factory(run))
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.activate_scheduled_pricing", queue="periodic", ignore_result=True
)
def activate_scheduled_pricing_task() -> dict[str, int]:
    """Hourly: scheduled prices nobody was told about are announced, a scheduled price whose day
    has come becomes the plan's current one, and a workspace's pending change whose renewal has
    come is applied."""
    from datetime import UTC, datetime

    from callobuzz_rag.billing.notice import hourly_pass

    async def run(factory: Any) -> dict[str, int]:
        return await hourly_pass(factory, datetime.now(UTC))

    result: dict[str, int] = asyncio.run(_with_session_factory(run))
    return result
