"""WhatsApp background work (TV6): answering a chat, the sweep that never loses an event, and the
daily purge of stored events.

`whatsapp.process_thread` runs on the `periodic` queue (the compose worker already consumes it).
The webhook wakes it after storing an event; `whatsapp.sweep` wakes it again, once a minute, for
any chat that still has an unprocessed event (a lost wake-up, a worker that died) and releases an
event a dead worker left half done (`stalled`).

Every task builds its own engine and providers inside `asyncio.run()` and closes them.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from callobuzz_rag.worker.celery_app import celery_app

if TYPE_CHECKING:
    from callobuzz_rag.channels.whatsapp.inbound import InboundDeps

logger = logging.getLogger(__name__)

TASK_PROCESS = "callobuzz_rag.whatsapp.process_thread"
TASK_SWEEP = "callobuzz_rag.whatsapp.sweep"
TASK_PURGE = "callobuzz_rag.whatsapp.purge_events"
TASK_FOLLOW_UPS = "callobuzz_rag.whatsapp.follow_ups"

PENDING_AFTER = timedelta(seconds=60)  # an event waiting longer than this was not picked up
STALLED_AFTER = timedelta(minutes=10)  # an event "in flight" longer than this lost its worker
EVENT_RETENTION = timedelta(days=7)


def _director_factory(settings: Any, session_factory: Any) -> Any:
    """`(corpus, profile) -> Director` for a worker: the same director the widget uses, built
    without a request (`surfaces.director_for_worker`)."""

    def make(corpus: Any, profile: Any) -> Any:
        from callobuzz_rag.conversation import surfaces

        # a visitor's chat: the polite busy reply after the usual wait, never a worker held for
        # as long as the lanes stay full (golden runs are the patient ones)
        return surfaces.director_for_worker(
            session_factory, settings, corpus, use_draft=False, profile=profile, patient=False
        )

    return make


def build_deps(settings: Any, session_factory: Any) -> "InboundDeps":
    """The answer loop's real collaborators for one task run."""
    from callobuzz_rag.channels.whatsapp import fees
    from callobuzz_rag.channels.whatsapp.graph import graph_client
    from callobuzz_rag.channels.whatsapp.inbound import InboundDeps
    from callobuzz_rag.conversation.leads.crypto import lead_crypto_from_settings
    from callobuzz_rag.storage.factory import object_storage

    return InboundDeps(
        session_factory=session_factory,
        settings=settings,
        crypto=lead_crypto_from_settings(settings),
        graph=graph_client(settings),
        director_factory=_director_factory(settings, session_factory),
        after_send=fees.charge_send,
        storage=object_storage(settings),
    )


async def _with_engine() -> Any:
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory

    settings = get_settings()
    engine = create_engine(settings)
    return settings, engine, create_session_factory(engine)


async def _process(thread_id: uuid.UUID) -> dict[str, int]:
    from callobuzz_rag.channels.whatsapp import inbound
    from callobuzz_rag.channels.whatsapp.availability import engine_ready

    settings, engine, session_factory = await _with_engine()
    try:
        if not engine_ready(settings):
            return {"skipped": 1}
        return await inbound.process_thread(build_deps(settings, session_factory), thread_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name=TASK_PROCESS,
    queue="periodic",
    max_retries=0,
    ignore_result=True,
)
def process_thread_task(thread_id: str) -> dict[str, int]:
    """Answer a chat's pending events, in order, one at a time."""
    return asyncio.run(_process(uuid.UUID(thread_id)))


async def sweep_with(session_factory: Any, now: datetime) -> dict[str, int]:
    """One sweep: release events a dead worker held, wake chats with something waiting."""
    from sqlalchemy import and_, or_, select, update

    from callobuzz_rag.channels.whatsapp import dispatch
    from callobuzz_rag.db.models.channel import ChannelEvent

    cutoff = now - STALLED_AFTER
    async with session_factory() as session:
        # The clock starts when a worker took the event (`claimed_at`), not when Meta delivered
        # it: an event that waited in the queue is not "stalled" the moment it is claimed.
        stalled = await session.execute(
            update(ChannelEvent)
            .where(
                ChannelEvent.status == "processing",
                or_(
                    ChannelEvent.claimed_at < cutoff,
                    and_(ChannelEvent.claimed_at.is_(None), ChannelEvent.received_at < cutoff),
                ),
            )
            .values(status="failed", error_code="stalled", processed_at=now)
        )
        waiting = (
            (
                await session.execute(
                    select(ChannelEvent.thread_id)
                    .where(
                        ChannelEvent.status == "received",
                        ChannelEvent.thread_id.is_not(None),
                        ChannelEvent.received_at < now - PENDING_AFTER,
                        or_(ChannelEvent.retry_at.is_(None), ChannelEvent.retry_at <= now),
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        await session.commit()
    for thread_id in waiting:
        dispatch.queue_thread(thread_id)
    return {"stalled": int(getattr(stalled, "rowcount", 0) or 0), "woken": len(waiting)}


async def _sweep() -> dict[str, int]:
    from callobuzz_rag.channels.whatsapp.availability import engine_ready

    settings, engine, session_factory = await _with_engine()
    try:
        if not engine_ready(settings):
            return {"skipped": 1}
        return await sweep_with(session_factory, datetime.now(UTC))
    finally:
        await engine.dispose()


@celery_app.task(name=TASK_SWEEP, queue="periodic", ignore_result=True)  # type: ignore[untyped-decorator]
def sweep_task() -> dict[str, int]:
    """Wake the chats whose events nobody picked up; release events a dead worker held."""
    return asyncio.run(_sweep())


async def _follow_ups() -> dict[str, int]:
    from callobuzz_rag.channels.whatsapp import followups
    from callobuzz_rag.channels.whatsapp.availability import engine_ready

    settings, engine, session_factory = await _with_engine()
    try:
        if not engine_ready(settings):
            return {"skipped": 1}
        return await followups.run_follow_ups(
            build_deps(settings, session_factory), datetime.now(UTC)
        )
    finally:
        await engine.dispose()


@celery_app.task(name=TASK_FOLLOW_UPS, queue="periodic", ignore_result=True)  # type: ignore[untyped-decorator]
def follow_ups_task() -> dict[str, int]:
    """Send the opt-in follow-ups that are due (off unless the owner turned them on)."""
    return asyncio.run(_follow_ups())


async def _purge() -> dict[str, int]:
    from sqlalchemy import delete

    from callobuzz_rag.db.models.channel import ChannelEvent

    _settings, engine, session_factory = await _with_engine()
    try:
        async with session_factory() as session:
            result = await session.execute(
                delete(ChannelEvent).where(
                    ChannelEvent.received_at < datetime.now(UTC) - EVENT_RETENTION,
                    ChannelEvent.status.in_(("done", "ignored", "failed")),
                )
            )
            await session.commit()
        return {"purged": int(getattr(result, "rowcount", 0) or 0)}
    finally:
        await engine.dispose()


@celery_app.task(name=TASK_PURGE, queue="periodic", ignore_result=True)  # type: ignore[untyped-decorator]
def purge_events_task() -> dict[str, int]:
    """Delete webhook events older than seven days (the message text lives in the chat)."""
    return asyncio.run(_purge())
