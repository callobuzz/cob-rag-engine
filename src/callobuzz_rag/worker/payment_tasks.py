"""Online payment background work (P1): process a stored webhook event, and the five-minute sweep.

`callobuzz_rag.process_payment_event` runs on the `periodic` queue (the compose worker already
consumes it). The webhook queues it after storing an event; `callobuzz_rag.sweep_payments` retries
events that failed, expires stale orders, re-reads payments still `authorized` and re-reads open
orders, so a webhook that never arrived loses nothing. Each task builds its own engine inside
`asyncio.run()` and closes it.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

TASK_PROCESS = "callobuzz_rag.process_payment_event"
TASK_SWEEP = "callobuzz_rag.sweep_payments"


async def _engine() -> Any:
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    return settings, engine, create_session_factory(engine)


async def process_one(settings: Any, session_factory: Any, provider: Any, event_id: str) -> str:
    """Process one stored event, then mail and notify for what it did."""
    from callobuzz_rag.billing.payments import capture, events

    async with session_factory() as session:
        out = await events.process(session, provider, event_id)
    for result in out.results:
        await capture.run_after_commit(session_factory, settings, result)
    if out.attention:
        await capture.run_after_commit(
            session_factory, settings, capture.attention_result(out.attention)
        )
    return out.status


async def _process(event_id: str) -> str:
    from callobuzz_rag.billing.payments.factory import provider_from_settings

    settings, engine, session_factory = await _engine()
    try:
        async with session_factory() as session:
            provider = await provider_from_settings(settings, session)
        if provider is None:
            return "off"
        return await process_one(settings, session_factory, provider, event_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name=TASK_PROCESS, queue="periodic", max_retries=0, ignore_result=True
)
def process_payment_event_task(event_id: str) -> str:
    """Process one stored payment event against the provider's truth."""
    return asyncio.run(_process(event_id))


async def sweep_with(settings: Any, session_factory: Any, provider: Any) -> dict[str, int]:
    from callobuzz_rag.billing.payments import capture, events

    async def follow_up(result: Any) -> None:
        await capture.run_after_commit(session_factory, settings, result)

    return await events.sweep(session_factory, provider, follow_up, now=datetime.now(UTC))


async def _sweep() -> dict[str, int]:
    from callobuzz_rag.billing.payments.factory import provider_from_settings

    settings, engine, session_factory = await _engine()
    try:
        async with session_factory() as session:
            provider = await provider_from_settings(settings, session)
        if provider is None:
            return {"skipped": 1}
        return await sweep_with(settings, session_factory, provider)
    finally:
        await engine.dispose()


@celery_app.task(name=TASK_SWEEP, queue="periodic", ignore_result=True)  # type: ignore[untyped-decorator]
def sweep_payments_task() -> dict[str, int]:
    """Retry due events, expire stale orders, heal what a lost webhook missed."""
    return asyncio.run(_sweep())


# P2 invoices
TASK_RENDER = "callobuzz_rag.render_tax_document"
TASK_SWEEP_DOCUMENTS = "callobuzz_rag.sweep_tax_documents"


async def _render_document(document_id: str) -> str:
    import uuid

    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.billing.invoicing import store
    from callobuzz_rag.storage.factory import object_storage

    settings, engine, session_factory = await _engine()
    try:
        return await store.render_and_store(
            settings,
            session_factory,
            uuid.UUID(document_id),
            storage=object_storage(settings),
            enqueue=enqueue_email,
        )
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name=TASK_RENDER, queue="periodic", max_retries=0, ignore_result=True
)
def render_tax_document_task(document_id: str) -> str:
    """Write one tax document's PDF to storage and mail it. The sweep retries a failure."""
    return asyncio.run(_render_document(document_id))


async def _sweep_documents() -> dict[str, int]:
    from callobuzz_rag.accounts.email.dispatch import enqueue_email
    from callobuzz_rag.billing.config import billing_enabled
    from callobuzz_rag.billing.invoicing import store
    from callobuzz_rag.storage.factory import object_storage

    if not billing_enabled():
        return {"skipped": 1}
    settings, engine, session_factory = await _engine()
    try:
        return await store.sweep(
            settings, session_factory, storage=object_storage(settings), enqueue=enqueue_email
        )
    finally:
        await engine.dispose()


@celery_app.task(name=TASK_SWEEP_DOCUMENTS, queue="periodic", ignore_result=True)  # type: ignore[untyped-decorator]
def sweep_tax_documents_task() -> dict[str, int]:
    """Fill every PDF and e-mail that is still missing (every ten minutes)."""
    return asyncio.run(_sweep_documents())


# /P2 invoices
