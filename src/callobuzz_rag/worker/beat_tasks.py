"""Periodic task definitions for Celery Beat.

Tasks are registered with stable task names. Feature epics replace stubs
with real implementations:

- run_evaluation       -> Epic 3-05 (RAGAS Evaluation Runner) + Epic 3-11
- detect_knowledge_gaps -> Epic 3-09 (BERTopic Knowledge Gap Detection)
- run_optimization     -> Epic 3-10 (Bayesian Auto-Optimizer)
- check_and_trigger_eval -> Epic 3-11 (Config-Change Triggers)

Each task routes to the 'periodic' queue to isolate from ingestion tasks.
"""

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from sqlalchemy import CursorResult
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


def _as_uuid(value: Any | None) -> Any | None:
    """Celery hands ids over as strings; the models compare UUIDs."""
    import uuid

    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _create_webhook_dispatcher() -> Any | None:
    """Create a WebhookDispatcher for use in periodic tasks.

    Deferred import — webhook module may not be available in all environments.
    Returns None if creation fails (webhooks will be silently skipped).
    """
    try:
        from callobuzz_rag.core.config import get_settings
        from callobuzz_rag.core.webhooks import WebhookDispatcher
        from callobuzz_rag.db.engine import create_engine, create_session_factory

        settings = get_settings()
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        return WebhookDispatcher(settings, session_factory=factory)
    except Exception:
        logger.debug("Webhook dispatcher not available in worker", exc_info=True)
        return None


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.run_evaluation",
    queue="periodic",
)
def run_evaluation_task(**kwargs: Any) -> dict[str, Any]:
    """Run RAGAS evaluation on recent queries.

    kwargs (all optional): corpus_id, sample_size, run_id — run_id is the queued
    row POST /admin/eval/trigger created and returned to its caller. `kind="golden"` runs
    a queued golden-set run instead (conversation C7).
    """
    from callobuzz_rag.evaluation.scheduler import run_scheduled_eval

    result = run_scheduled_eval(
        corpus_id=kwargs.get("corpus_id"),
        sample_size=kwargs.get("sample_size"),
        run_id=kwargs.get("run_id"),
        kind=kwargs.get("kind"),
    )
    return result if result else {"status": "failed"}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.evaluation.scheduler.check_and_trigger_eval",
    queue="periodic",
)
def check_and_trigger_eval_task() -> None:
    """Check if retrieval settings changed and trigger eval if so."""
    from callobuzz_rag.evaluation.scheduler import check_and_trigger_eval

    check_and_trigger_eval()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.detect_knowledge_gaps",
    queue="periodic",
)
def detect_knowledge_gaps_task(**kwargs: Any) -> dict[str, Any]:
    """Detect knowledge gaps via BERTopic clustering.

    kwargs (all optional): corpus_id, report_id — report_id is the queued
    GapReport an API trigger created and returned to its caller.
    """
    corpus_id = _as_uuid(kwargs.get("corpus_id"))
    report_id = _as_uuid(kwargs.get("report_id"))
    try:
        result = asyncio.run(_run_gap_detection_async(corpus_id=corpus_id, report_id=report_id))
        return {
            "gap_report_id": str(result.id),
            "status": result.status,
            "cluster_count": result.cluster_count or 0,
            "query_count": result.query_count or 0,
        }
    except Exception:
        logger.exception("Gap detection task failed")
        return {"status": "failed", "task": "detect_knowledge_gaps"}


async def _run_gap_detection_async(
    corpus_id: Any | None = None, report_id: Any | None = None
) -> Any:
    """Async inner function for detect_knowledge_gaps_task."""
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.embeddings.factory import create_embedding_provider
    from callobuzz_rag.gaps.detector import GapDetector

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    embedder = create_embedding_provider(settings)
    dispatcher = _create_webhook_dispatcher()

    try:
        detector = GapDetector(
            session_factory=factory,
            embedding_provider=embedder,
            settings=settings,
            webhook_dispatcher=dispatcher,
        )
        return await detector.run(corpus_id=corpus_id, report_id=report_id)
    finally:
        await embedder.close()
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.run_optimization",
    queue="periodic",
)
def run_optimization_task(**kwargs: Any) -> dict[str, Any]:
    """Run Bayesian auto-optimization of retrieval parameters.

    kwargs (all optional): corpus_id, n_trials, run_id — run_id is the queued
    row POST /admin/optimize/trigger created and returned to its caller.
    """
    corpus_id = _as_uuid(kwargs.get("corpus_id"))
    run_id = _as_uuid(kwargs.get("run_id"))
    n_trials = int(kwargs.get("n_trials") or 50)
    try:
        result = asyncio.run(
            _run_optimization_async(corpus_id=corpus_id, n_trials=n_trials, run_id=run_id)
        )
        return {
            "optimization_run_id": str(result.id),
            "status": result.status,
            "n_trials": result.n_trials,
            "pareto_count": len(result.pareto_configs or []),
        }
    except Exception:
        logger.exception("Optimization task failed")
        return {"status": "failed", "task": "run_optimization"}


async def _run_optimization_async(
    corpus_id: Any | None = None, n_trials: int = 50, run_id: Any | None = None
) -> Any:
    """Async inner function for run_optimization_task."""
    from callobuzz_rag.analytics.optimizer import RetrievalOptimizer
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.evaluation.golden import GoldenDatasetManager
    from callobuzz_rag.evaluation.runner import EvaluationRunner

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    dispatcher = _create_webhook_dispatcher()

    try:
        eval_runner = EvaluationRunner(session_factory=factory, webhook_dispatcher=dispatcher)
        async with factory() as session:
            golden_manager = GoldenDatasetManager(session=session)
            optimizer = RetrievalOptimizer(
                eval_runner=eval_runner,
                golden_manager=golden_manager,
                session_factory=factory,
                webhook_dispatcher=dispatcher,
            )
            return await optimizer.optimize(n_trials=n_trials, corpus_id=corpus_id, run_id=run_id)
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.cleanup_expired_reviews",
    queue="periodic",
)
def cleanup_expired_reviews_task(self: Any) -> dict[str, Any]:
    """Clean up expired pending review jobs — delete stored files, update status."""
    return asyncio.run(_cleanup_expired_reviews())


async def _cleanup_expired_reviews() -> dict[str, Any]:
    """Async implementation of expired review cleanup."""
    from sqlalchemy import select

    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.ingest_job import IngestJob
    from callobuzz_rag.storage.factory import object_storage

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    cleaned = 0

    try:
        async with session_factory() as session:
            result = await session.execute(
                select(IngestJob).where(
                    IngestJob.status == "pending_review",
                    IngestJob.expires_at < now,
                )
            )
            expired_jobs = result.scalars().all()

            if not expired_jobs:
                return {"cleaned": 0}

            storage = object_storage(settings)
            for job in expired_jobs:
                storage_cleanup_failed = False
                if job.storage_key:
                    try:
                        await storage.delete(settings.s3_bucket, job.storage_key)
                    except Exception:
                        logger.warning(
                            "Failed to delete stored file for expired job %s: %s",
                            job.id,
                            job.storage_key,
                            exc_info=True,
                        )
                        storage_cleanup_failed = True
                        job.errors = {
                            **(job.errors or {}),
                            "storage_cleanup_failed": True,
                            "storage_key": job.storage_key,
                        }
                job.status = "expired"
                job.finished_at = now
                if not storage_cleanup_failed:
                    job.storage_key = None  # Clear key after successful cleanup
                cleaned += 1

            await session.commit()
    finally:
        await engine.dispose()

    logger.info("Cleaned up %d expired pending review jobs", cleaned)
    return {"cleaned": cleaned}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.cleanup_accounts",
    queue="periodic",
)
def cleanup_accounts_task() -> dict[str, int]:
    """Delete expired codes, dead sessions and abandoned sign-ups (accounts A4)."""
    from callobuzz_rag.accounts import cleanup

    return asyncio.run(cleanup.run())


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.cleanup_expired_memories",
    queue="periodic",
)
def cleanup_expired_memories_task(self: Any) -> dict[str, Any]:
    """Clean up expired memories (past expires_at) and enforce per-corpus cap."""
    return asyncio.run(_cleanup_expired_memories())


async def _cleanup_expired_memories() -> dict[str, Any]:
    """Delete expired memories and enforce per-corpus memory cap."""
    from sqlalchemy import delete, func, select

    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.memory import Memory

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    expired_count = 0
    capped_count = 0

    try:
        async with session_factory() as session:
            # 1. Delete expired memories (expires_at < now)
            if settings.memory_ttl_days > 0:
                result = await session.execute(delete(Memory).where(Memory.expires_at < now))
                expired_count = cast("CursorResult[Any]", result).rowcount or 0

            # 2. Enforce per-corpus memory cap
            max_entries = settings.memory_max_entries
            if max_entries > 0:
                count_query = (
                    select(Memory.corpus_id, func.count(Memory.id).label("cnt"))
                    .group_by(Memory.corpus_id)
                    .having(func.count(Memory.id) > max_entries)
                )
                over_limit = (await session.execute(count_query)).all()

                for corpus_id, cnt in over_limit:
                    excess = cnt - max_entries
                    oldest = await session.execute(
                        select(Memory.id)
                        .where(Memory.corpus_id == corpus_id)
                        .order_by(Memory.created_at.asc())
                        .limit(excess)
                    )
                    ids_to_delete = [row[0] for row in oldest.all()]
                    if ids_to_delete:
                        await session.execute(delete(Memory).where(Memory.id.in_(ids_to_delete)))
                        capped_count += len(ids_to_delete)

            await session.commit()
    finally:
        await engine.dispose()

    total = expired_count + capped_count
    if total > 0:
        logger.info("Memory cleanup: %d expired, %d capped", expired_count, capped_count)
    return {"expired": expired_count, "capped": capped_count}
