"""The value report beat task (TV4): freeze every closed month and e-mail the latest.

Runs hourly; `business/value_freeze.py` freezes any closed month that has no row (so downtime on
the 1st loses nothing), e-mails the latest month in the first week only, and writes and sends
nothing twice, so a rerun or a catch-up after downtime is safe.
"""

import asyncio
import logging

from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.freeze_value_reports", queue="periodic", ignore_result=True
)
def freeze_value_reports_task() -> dict[str, int]:
    return asyncio.run(_freeze())


async def _freeze() -> dict[str, int]:
    from callobuzz_rag.business.value_freeze import freeze_and_send
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    try:
        return await freeze_and_send(create_session_factory(engine), settings)
    finally:
        await engine.dispose()
