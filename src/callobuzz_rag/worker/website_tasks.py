"""Celery tasks for website learning (BIZ1): the engine's worker reads a business's own site.

Both tasks run on the `ingestion` queue next to uploads (D6): learning is crawl and embedding
work, and it must not share a process with the API. The logic is `website/learn.py`; this module
wires the real collaborators (database, fetcher, the records pipeline, the vector store).
"""

import asyncio
import logging
import uuid
from dataclasses import asdict
from typing import Any

from callobuzz_rag.core.config import Settings, get_settings
from callobuzz_rag.fairuse import rails  # PIP3 fair use
from callobuzz_rag.tiers.lanes import LaneTimeout  # PIP3 fair use
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.website.learn_site",
    queue="ingestion",
    acks_late=True,
    ignore_result=True,
)
def learn_site_task(source_id: str, kind: str = "first", trigger: str = "manual") -> dict[str, Any]:
    """Read a whole site (`kind` `first` or `full`)."""
    return asyncio.run(_learn(source_id, kind=kind, trigger=trigger, urls=None))


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.website.learn_urls",
    queue="ingestion",
    acks_late=True,
    ignore_result=True,
)
def learn_urls_task(
    source_id: str, urls: list[str], kind: str = "page", trigger: str = "manual"
) -> dict[str, Any]:
    """Read just these URLs of a site (a page re-read, a pushed URL, a widget hint)."""
    return asyncio.run(_learn(source_id, kind=kind, trigger=trigger, urls=urls))


def build_deps(settings: Settings, session_factory: Any, vector_store: Any, ingest: Any) -> Any:
    """`LearnDeps` with the real fetcher: one per site, scoped to its origin."""
    # PIP3 fair use: every fetch spends the business's daily budget and new pages are counted
    from callobuzz_rag.fairuse import budget as fair_budget
    from callobuzz_rag.website.fetch import WebsiteFetcher
    from callobuzz_rag.website.learn import LearnDeps

    fair_use = bool(getattr(settings, "fair_use_enabled", True))

    def fetcher(source: Any) -> WebsiteFetcher:
        spend = fair_budget.FetchBudget(session_factory, source.tenant_id) if fair_use else None
        return WebsiteFetcher(settings, origin=source.origin, scope=source.scope, budget=spend)

    async def pages_left(session: Any, corpus: Any) -> int | None:
        return await fair_budget.pages_left(session, corpus.tenant_id)

    async def on_pages(tenant_id: uuid.UUID, n: int) -> None:
        await fair_budget.add_pages(session_factory, tenant_id, n)

    async def reserve_pages(tenant_id: uuid.UUID, n: int) -> int:
        return await fair_budget.reserve_pages(session_factory, tenant_id, n)

    async def release_pages(tenant_id: uuid.UUID, n: int) -> None:
        await fair_budget.release_pages(session_factory, tenant_id, n)

    def usage_alert(tenant_id: uuid.UUID) -> None:
        from callobuzz_rag.fairuse import alerts

        alerts.schedule_usage(session_factory, tenant_id, alerts.WEBSITE_METRICS, settings)

    return LearnDeps(
        settings=settings,
        session_factory=session_factory,
        fetcher=fetcher,
        ingest=ingest,
        vector_store=vector_store,
        pages_left=pages_left if fair_use else None,
        on_pages=on_pages if fair_use else None,
        reserve_pages=reserve_pages if fair_use else None,
        release_pages=release_pages if fair_use else None,
        usage_alert=usage_alert if fair_use else None,
    )
    # /PIP3 fair use


# PIP3 fair use
async def _drain_alerts() -> None:
    """Let the fair-use notices this task scheduled finish before its engine closes: their
    claims are already committed, so a notice cut off here would never be sent."""
    from callobuzz_rag.fairuse import alerts

    await alerts.drain_usage()


async def _crawl_limit(settings: Settings, session_factory: Any) -> int | None:
    """The engine's crawl concurrency rail (None: no limit); never fails a run."""
    from callobuzz_rag.accounts.settings_store import get_engine_settings

    try:
        return (await get_engine_settings(session_factory)).pipelines.crawl_concurrency
    except Exception:
        logger.warning("Crawl concurrency rail unreadable; no limit applied", exc_info=True)
        return None


async def _crawl_redis(settings: Settings, limit: int | None) -> Any:
    if not limit:
        return None
    import redis.asyncio as aioredis

    return aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url, socket_timeout=2, socket_connect_timeout=2
    )


# /PIP3 fair use


async def _learn(
    source_id: str, *, kind: str, trigger: str, urls: list[str] | None
) -> dict[str, Any]:
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.website import WebsiteSource
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider
    from callobuzz_rag.website.learn import learn_source, learn_urls
    from callobuzz_rag.worker.tasks import _run_records_pipeline

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    sid = uuid.UUID(source_id)
    try:
        async with session_factory() as session:
            source = await session.get(WebsiteSource, sid)
            if source is None:
                return {"status": "stopped", "reason": "gone"}
            from callobuzz_rag.db.models.corpus import Corpus

            corpus = await session.get(Corpus, source.corpus_id)
            collection = corpus.qdrant_collection if corpus is not None else None

        async def ingest(
            batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID
        ) -> dict[str, Any]:
            return await _run_records_pipeline(
                [asdict(record) for record in batch], str(corpus_id), str(job_id), collection
            )

        deps = build_deps(settings, session_factory, vector_store, ingest)
        # PIP3 fair use: at most `crawl_concurrency` sites are read at once; a read that finds
        # every slot taken for ten minutes goes back on the queue
        limit = await _crawl_limit(settings, session_factory)
        redis = await _crawl_redis(settings, limit)
        try:
            async with rails.crawl_slot(redis, limit, tenant_id=source.tenant_id):
                if urls is None:
                    result = await learn_source(sid, kind=kind, trigger=trigger, deps=deps)
                else:
                    result = await learn_urls(sid, urls, kind=kind, trigger=trigger, deps=deps)
        except LaneTimeout:
            if urls is None:
                learn_site_task.apply_async(args=(source_id, kind, trigger), countdown=60)
            else:
                learn_urls_task.apply_async(args=(source_id, urls, kind, trigger), countdown=60)
            return {"status": "requeued", "reason": "crawl_slots_busy"}
        finally:
            if redis is not None:
                await redis.aclose()
        # /PIP3 fair use
        logger.info("Website learning of %s ended: %s", source_id, result.status)
        return {
            "status": result.status,
            "run_id": str(result.run_id) if result.run_id else None,
            "counts": result.counts,
            "reason": result.reason,
        }
    finally:
        await vector_store.close()
        await _drain_alerts()
        await engine.dispose()


# BIZ2 freshness
@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.website.schedule_checks",
    queue="periodic",
    acks_late=True,
    ignore_result=True,
)
def schedule_checks_task() -> dict[str, int]:
    """The beat tick: hand due pages to `check_pages_task`, restart stalled or never-run sites."""
    return asyncio.run(_schedule())


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.website.check_pages",
    queue="ingestion",
    acks_late=True,
    ignore_result=True,
)
def check_pages_task(
    source_id: str, page_ids: list[str], trigger: str = "schedule"
) -> dict[str, Any]:
    """Check these learned pages of one site (sitemap, conditional request, hash)."""
    return asyncio.run(_check(source_id, page_ids, trigger))


async def _schedule() -> dict[str, int]:
    from datetime import UTC, datetime

    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.website.check import schedule_checks

    settings = get_settings()
    engine = create_engine(settings)
    try:
        return await schedule_checks(
            create_session_factory(engine),
            settings,
            dispatch_check=lambda source, ids, trigger: check_pages_task.delay(
                source, ids, trigger
            ),
            dispatch_learn=lambda source, kind, trigger: learn_site_task.delay(
                source, kind, trigger
            ),
            now=datetime.now(UTC),
        )
    finally:
        await engine.dispose()


async def _check(source_id: str, page_ids: list[str], trigger: str) -> dict[str, Any]:
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.website import WebsiteSource
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider
    from callobuzz_rag.website.check import check_pages
    from callobuzz_rag.worker.tasks import _run_records_pipeline

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    sid = uuid.UUID(source_id)
    try:
        async with session_factory() as session:
            source = await session.get(WebsiteSource, sid)
            if source is None:
                return {"status": "stopped", "reason": "gone"}
            corpus = await session.get(Corpus, source.corpus_id)
            collection = corpus.qdrant_collection if corpus is not None else None

        async def ingest(
            batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID
        ) -> dict[str, Any]:
            return await _run_records_pipeline(
                [asdict(record) for record in batch], str(corpus_id), str(job_id), collection
            )

        deps = build_deps(settings, session_factory, vector_store, ingest)
        # PIP3 fair use: a freshness check is crawling too
        limit = await _crawl_limit(settings, session_factory)
        redis = await _crawl_redis(settings, limit)
        try:
            async with rails.crawl_slot(redis, limit, tenant_id=source.tenant_id):
                done = await check_pages(
                    sid, [uuid.UUID(i) for i in page_ids], trigger=trigger, deps=deps
                )
        except LaneTimeout:
            check_pages_task.apply_async(args=(source_id, page_ids, trigger), countdown=60)
            return {"status": "requeued", "reason": "crawl_slots_busy"}
        finally:
            if redis is not None:
                await redis.aclose()
        # /PIP3 fair use
        logger.info("Website check of %s ended: %s", source_id, done.result.status)
        return {
            "status": done.result.status,
            "run_id": str(done.result.run_id) if done.result.run_id else None,
            "counts": done.result.counts,
        }
    finally:
        await vector_store.close()
        await _drain_alerts()
        await engine.dispose()


# /BIZ2 freshness


# PIP3 fair use
@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.website.resume_limited",
    queue="periodic",
    acks_late=True,
    ignore_result=True,
)
def resume_limited_task() -> dict[str, int]:
    """Hourly: restart the site reads a daily fair-use limit cut short on an earlier day."""
    return asyncio.run(_resume())


async def _resume() -> dict[str, int]:
    from datetime import UTC, datetime

    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.website.resume import resume_limited

    settings = get_settings()
    if not settings.website_learning_enabled:
        return {"resumed": 0}
    engine = create_engine(settings)
    try:
        started = await resume_limited(
            create_session_factory(engine),
            dispatch_learn=lambda source, kind, trigger: learn_site_task.delay(
                source, kind, trigger
            ),
            now=datetime.now(UTC),
        )
        return {"resumed": len(started)}
    finally:
        await engine.dispose()


# /PIP3 fair use
