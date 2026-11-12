"""Celery tasks for document ingestion."""

import asyncio
import logging
import uuid
from typing import Any

from callobuzz_rag.core.config import Settings, get_settings
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.ingest_document",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def ingest_document_task(
    self: Any,
    storage_key: str,
    file_name: str,
    mime_type: str,
    corpus_id: str,
    job_id: str,
    qdrant_collection: str | None = None,
    mode: str = "auto",
    target_document_id: str | None = None,
    auto_confirm: bool = False,
    doc_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Celery task that runs the ingestion pipeline.

    File bytes are NOT passed directly (too large for message broker).
    The file is already in file storage (uploaded by the API endpoint).
    This task reads it from file storage, then runs the pipeline.
    """
    logger.info("Starting ingestion for %s (job=%s)", file_name, job_id)

    # Update Celery state
    self.update_state(state="PROCESSING", meta={"file": file_name, "step": "starting"})

    # Run async pipeline in sync context
    result: dict[str, Any] = asyncio.run(
        _run_pipeline(
            storage_key,
            file_name,
            mime_type,
            corpus_id,
            job_id,
            qdrant_collection=qdrant_collection,
            mode=mode,
            target_document_id=target_document_id,
            auto_confirm=auto_confirm,
            doc_metadata=doc_metadata,
        )
    )

    if _is_transient_embedding_failure(result) and self.request.retries < self.max_retries:
        # The pipeline has already rolled back and marked the job failed; a
        # retry re-runs it against the same job row, which it reuses. Only
        # failures AT the embedding step qualify: nothing has been written to
        # the vector store yet, so a re-run cannot duplicate vectors.
        delay = min(300, 30 * 2**self.request.retries)
        logger.warning(
            "Embedding service unreachable for job %s; retrying in %ss (%s/%s)",
            job_id,
            delay,
            self.request.retries + 1,
            self.max_retries,
        )
        raise self.retry(countdown=delay)

    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.ingest_records",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def ingest_records_task(
    self: Any,
    records: list[dict[str, Any]],
    corpus_id: str,
    job_id: str,
    qdrant_collection: str | None = None,
) -> dict[str, Any]:
    """Import a batch of text records (POST /api/v1/ingest/records)."""
    logger.info("Starting record import of %d records (job=%s)", len(records), job_id)
    self.update_state(state="PROCESSING", meta={"records": len(records)})
    result: dict[str, Any] = asyncio.run(
        _run_records_pipeline(records, corpus_id, job_id, qdrant_collection)
    )
    if _is_transient_embedding_failure(result) and self.request.retries < self.max_retries:
        # Same reasoning as ingest_document_task: nothing reached the vector store.
        delay = min(300, 30 * 2**self.request.retries)
        logger.warning("Embedding service unreachable for job %s; retrying in %ss", job_id, delay)
        raise self.retry(countdown=delay)
    return result


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.delete_storage_prefix",
    max_retries=5,
    acks_late=True,
)
def delete_storage_prefix_task(
    self: Any, bucket: str, prefix: str, reason: str = ""
) -> dict[str, Any]:
    """Delete every stored object under a folder prefix of a deleted corpus or workspace.

    Queued by `storage.cleanup.schedule_prefix_cleanup` after the rows are gone. Bounded:
    the S3 client gives up within seconds, and the task retries 5 times (30 s, 1, 2, 4,
    8 min) before a final ERROR naming the prefix; `python -m callobuzz_rag.storage.gc`
    removes whatever is left.
    """
    from callobuzz_rag.storage import cleanup
    from callobuzz_rag.storage.factory import cleanup_object_storage

    try:
        storage = cleanup_object_storage(get_settings())
        count = asyncio.run(storage.delete_prefix(bucket, prefix))
    except ValueError:
        logger.error(
            "Refusing storage cleanup of %s/%s (%s): not a folder prefix",
            bucket,
            prefix,
            reason,
            exc_info=True,
        )
        return {"status": "failed", "prefix": prefix, "error": "not a folder prefix"}
    except Exception as e:
        if self.request.retries < self.max_retries:
            delay = min(600, 30 * 2**self.request.retries)
            logger.warning(
                "Storage cleanup of %s/%s failed (%s); retrying in %ss (%s/%s)",
                bucket,
                prefix,
                e,
                delay,
                self.request.retries + 1,
                self.max_retries,
            )
            raise self.retry(exc=e, countdown=delay) from e
        logger.error(
            "Stored files under %s/%s were NOT deleted (%s) after %s attempts; they are "
            "orphans until removed. Run: %s",
            bucket,
            prefix,
            reason,
            self.max_retries + 1,
            cleanup.GC_HINT,
            exc_info=True,
        )
        return {"status": "failed", "prefix": prefix, "error": str(e)}
    logger.info("Deleted %d stored objects under %s/%s (%s)", count, bucket, prefix, reason)
    return {"status": "deleted", "prefix": prefix, "objects": count}


async def _run_records_pipeline(
    records: list[dict[str, Any]],
    corpus_id: str,
    job_id: str,
    qdrant_collection: str | None = None,
) -> dict[str, Any]:
    from dataclasses import asdict

    from callobuzz_rag.accounts.caps import ensure_document_room_for_job
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
    from callobuzz_rag.pipeline.records import TextRecord

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    providers = _create_providers(settings)
    try:
        async with session_factory() as session:
            await _use_corpus_embedder(session, settings, providers, corpus_id)
            orchestrator = IngestionOrchestrator(
                session=session,
                storage=providers["storage"],
                parser=providers["parser"],
                chunker=providers["chunker"],
                enricher=providers["enricher"],
                embedder=providers["embedder"],
                vector_store=providers["vector_store"],
                bucket=settings.s3_bucket,
                collection=qdrant_collection or settings.qdrant_collection,
                settings=settings,
                document_room=ensure_document_room_for_job,
            )
            outcome = await orchestrator.ingest_records(
                [TextRecord(**r) for r in records], uuid.UUID(corpus_id), uuid.UUID(job_id)
            )
        if outcome.status == "completed" and (outcome.created or outcome.updated):
            try:
                from callobuzz_rag.retrieval.query_cache import QueryResponseCache

                cache = QueryResponseCache(settings)
                await cache.invalidate_corpus(corpus_id)
                await cache.close()
            except Exception:
                logger.warning("Failed to invalidate query cache", exc_info=True)
        return {"job_id": job_id, **asdict(outcome)}
    finally:
        await providers["embedder"].close()
        await providers["vector_store"].close()
        if hasattr(providers["storage"], "close"):
            await providers["storage"].close()
        await engine.dispose()


# Connection-level failures of the embedding backend (TEI restarting, a hosted
# API blip). Content errors, auth errors and bad input are not in this list and
# fail immediately, as before.
_TRANSIENT_MARKERS = (
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "RemoteProtocolError",
    "Connection refused",
    "Connection reset",
    "ServiceUnavailable",
    "503 Service Unavailable",
)


def _is_transient_embedding_failure(result: dict[str, Any]) -> bool:
    if result.get("status") != "failed" or result.get("failed_at_step") != "embedding":
        return False
    error = str(result.get("error") or "")
    return any(marker in error for marker in _TRANSIENT_MARKERS)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="callobuzz_rag.migrate_corpus",
    max_retries=3,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    queue="migrations",
)
def migrate_corpus_task(
    self: Any,
    migration_id: str,
    rollback: bool = False,
) -> dict[str, Any]:
    """Celery task that runs a corpus embedding migration (forward or rollback)."""
    logger.info("Starting migration task: %s (rollback=%s)", migration_id, rollback)
    self.update_state(state="PROCESSING", meta={"migration_id": migration_id})
    result: dict[str, Any] = asyncio.run(_run_migration(migration_id, rollback=rollback))
    return result


async def _run_migration(
    migration_id: str,
    rollback: bool = False,
) -> dict[str, Any]:
    """Async migration execution — forward or rollback."""
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.migration.service import (
        complete_cutover,
        complete_rollback,
    )
    from callobuzz_rag.migration.strategies import (
        count_active_chunks,
        count_memories_with_embedding,
        preflight_probe,
        re_embed_chunks,
        stage_memories,
    )
    from callobuzz_rag.migration.verify import (
        drain_gate,
        reconcile_chunks,
        reconcile_memories,
    )
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)

    try:
        async with session_factory() as session:
            from callobuzz_rag.db.models.corpus import Corpus
            from callobuzz_rag.db.models.corpus_migration import CorpusMigration

            migration = await session.get(CorpusMigration, uuid.UUID(migration_id))
            if migration is None:
                return {"status": "error", "error": "Migration not found"}

            corpus = await session.get(Corpus, migration.corpus_id)
            if corpus is None:
                migration.status = "failed"
                migration.error = "Corpus deleted"
                await session.commit()
                return {"status": "failed", "error": "Corpus deleted"}

            target_config = migration.from_config if rollback else migration.to_config

            try:
                if migration.status == "queued":
                    migration.status = "rolling_back" if rollback else "running"
                    from datetime import UTC, datetime

                    migration.started_at = datetime.now(UTC)
                    await session.flush()

                from callobuzz_rag.embeddings.factory import corpus_embedding_key

                embedder = await preflight_probe(
                    settings,
                    target_config,
                    api_key=corpus_embedding_key(corpus, settings, target_config["model"]),
                )

                if not rollback:
                    await vector_store.create_collection(
                        migration.new_collection, target_config["dimensions"]
                    )

                    migration.chunks_total = await count_active_chunks(session, migration.corpus_id)
                    migration.memories_total = await count_memories_with_embedding(
                        session, migration.corpus_id
                    )
                    await session.flush()

                    await re_embed_chunks(session, migration, embedder, vector_store, settings)
                    await stage_memories(session, migration, embedder, settings)

                    migration.status = "verifying"
                    await session.flush()

                    import time

                    gate_start = time.monotonic()
                    await drain_gate(session, migration.corpus_id, gate_start, settings)
                    await reconcile_chunks(session, migration, embedder, vector_store, settings)
                    await reconcile_memories(session, migration, embedder, settings)
                    await complete_cutover(session, migration, settings)
                else:
                    await stage_memories(session, migration, embedder, settings)
                    await complete_rollback(session, migration, settings)

                await session.commit()

                # Post-cutover sweep: re-embed straggler memories created
                # between reconciliation and commit (design §5.3 step 8)
                if not rollback:
                    try:
                        from sqlalchemy import select as sa_select

                        from callobuzz_rag.db.models.memory import Memory

                        to_model = migration.to_config["model"]
                        straggler_stmt = sa_select(Memory).where(
                            Memory.corpus_id == migration.corpus_id,
                            Memory.embedding.isnot(None),
                            Memory.embedding_model != to_model,
                        )
                        straggler_result = await session.execute(straggler_stmt)
                        stragglers = list(straggler_result.scalars().all())
                        if stragglers:
                            texts = [m.content for m in stragglers]
                            new_embs = await embedder.embed_texts(texts)
                            for mem, emb in zip(stragglers, new_embs, strict=True):
                                mem.embedding = emb.vector
                                mem.embedding_model = to_model
                            await session.commit()
                            logger.info(
                                "Post-cutover sweep: re-embedded %d straggler memories",
                                len(stragglers),
                            )
                    except Exception:
                        logger.warning("Post-cutover sweep failed (non-fatal)", exc_info=True)

                try:
                    from callobuzz_rag.api.routes.corpus import invalidate_corpus_query_cache

                    await invalidate_corpus_query_cache(settings, str(migration.corpus_id))
                except Exception:
                    logger.warning("Failed to invalidate query cache post-migration")

                return {
                    "status": str(migration.status),
                    "migration_id": migration_id,
                    "chunks_done": migration.chunks_done,
                    "memories_done": migration.memories_done,
                }

            except Exception as e:
                logger.error("Migration %s failed: %s", migration_id, e, exc_info=True)
                await session.rollback()
                async with session_factory() as err_session:
                    mig = await err_session.get(CorpusMigration, uuid.UUID(migration_id))
                    if mig:
                        mig.status = "failed"
                        mig.error = str(e)[:2000]
                        await err_session.commit()
                return {"status": "failed", "error": str(e)}

    finally:
        await vector_store.close()
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.detect_embedding_drift",
)
def detect_embedding_drift_task() -> dict[str, Any]:
    """Beat task: scan all corpora for embedding drift, create pending migrations."""
    logger.info("Running embedding drift detection")
    result: dict[str, Any] = asyncio.run(_run_drift_detection())
    return result


async def _run_drift_detection() -> dict[str, Any]:
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.migration.service import detect_drift

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            created = await detect_drift(session, settings)
            await session.commit()
            return {"created_count": len(created), "status": "ok"}
    finally:
        await engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.prune_migrated_collections",
)
def prune_migrated_collections_task() -> dict[str, Any]:
    """Beat task: drop old collections past their retention window."""
    logger.info("Running migration collection pruning")
    result: dict[str, Any] = asyncio.run(_run_pruning())
    return result


async def _run_pruning() -> dict[str, Any]:
    from datetime import UTC, datetime

    from sqlalchemy import select

    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.corpus_migration import CorpusMigration
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    pruned = 0

    try:
        async with session_factory() as session:
            now = datetime.now(UTC)
            stmt = select(CorpusMigration).where(
                CorpusMigration.status.in_(("completed", "rolled_back")),
                CorpusMigration.prune_after.isnot(None),
                CorpusMigration.prune_after < now,
            )
            result = await session.execute(stmt)
            migrations = list(result.scalars().all())

            for migration in migrations:
                corpus = await session.get(Corpus, migration.corpus_id)
                if corpus is None:
                    continue

                if migration.status == "completed":
                    target = migration.old_collection
                else:
                    target = migration.new_collection

                if target == corpus.qdrant_collection:
                    logger.warning("Skipping prune of %s — still the live collection", target)
                    continue

                try:
                    await vector_store.delete_collection(target)
                    migration.prune_after = None
                    pruned += 1
                    logger.info("Pruned collection %s (migration %s)", target, migration.id)
                except Exception:
                    logger.warning("Failed to prune collection %s", target, exc_info=True)

            await session.commit()
    finally:
        await vector_store.close()
        await engine.dispose()

    return {"pruned": pruned, "status": "ok"}


def _create_embedding_provider(settings: Settings) -> Any:
    """Create embedding provider based on config."""
    from callobuzz_rag.embeddings.factory import create_embedding_provider

    return create_embedding_provider(settings)


def _create_transcription_provider(settings: Settings) -> Any | None:
    """Create transcription provider based on config."""
    from callobuzz_rag.transcription.factory import create_transcription_provider

    return create_transcription_provider(settings)


async def _use_corpus_embedder(
    session: Any, settings: Settings, providers: dict[str, Any], corpus_id: str
) -> None:
    """Swap in the corpus's own embedding model when its collection holds one.

    Built uncached (this task has its own event loop) and closed with the other
    providers in the task's `finally`.
    """
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.embeddings.factory import (
        corpus_embedding_config,
        create_embedding_provider,
    )

    corpus = await session.get(Corpus, uuid.UUID(corpus_id))
    resolved = corpus_embedding_config(corpus, settings) if corpus is not None else None
    if resolved is None:
        return
    await providers["embedder"].close()
    providers["embedder"] = create_embedding_provider(settings, resolved, cache=False)


def _create_providers(settings: Settings) -> dict[str, Any]:
    """Provider factory — single place to swap implementations."""
    from callobuzz_rag.chunking.chunker import chunker_from_settings
    from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider
    from callobuzz_rag.parsing.docling_parser import DoclingParserProvider
    from callobuzz_rag.parsing.ocr_langs import warn_ocr_config
    from callobuzz_rag.storage.factory import object_storage
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    warn_ocr_config(settings.ocr_engine, settings.ocr_langs)

    return {
        "storage": object_storage(settings),
        "parser": DoclingParserProvider(
            ocr_engine=settings.ocr_engine, ocr_langs=settings.ocr_langs
        ),
        "chunker": chunker_from_settings(settings),
        "enricher": ExtractiveEnrichmentProvider(),
        "embedder": _create_embedding_provider(settings),
        "vector_store": QdrantVectorProvider(settings),
        "transcriber": _create_transcription_provider(settings),
    }


async def _run_pipeline(
    storage_key: str,
    file_name: str,
    mime_type: str,
    corpus_id: str,
    job_id: str,
    qdrant_collection: str | None = None,
    mode: str = "auto",
    target_document_id: str | None = None,
    auto_confirm: bool = False,
    doc_metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Async pipeline execution."""
    from callobuzz_rag.accounts.caps import document_token_limit, ensure_document_room_for_job
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator

    settings = get_settings()

    async def token_limit(session: Any, corpus: uuid.UUID) -> int | None:
        return await document_token_limit(session, corpus, settings.max_document_tokens)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    providers = _create_providers(settings)

    collection = qdrant_collection or settings.qdrant_collection

    try:
        # Read the file from file storage
        file_bytes: bytes = await providers["storage"].download(settings.s3_bucket, storage_key)

        # Run pipeline
        async with session_factory() as session:
            await _use_corpus_embedder(session, settings, providers, corpus_id)
            orchestrator = IngestionOrchestrator(
                session=session,
                storage=providers["storage"],
                parser=providers["parser"],
                chunker=providers["chunker"],
                enricher=providers["enricher"],
                embedder=providers["embedder"],
                vector_store=providers["vector_store"],
                bucket=settings.s3_bucket,
                collection=collection,
                transcriber=providers.get("transcriber"),
                settings=settings,
                document_room=ensure_document_room_for_job,
                document_token_limit=token_limit,
            )
            from callobuzz_rag.pipeline.models import IngestMode

            result = await orchestrator.ingest(
                file_bytes=file_bytes,
                file_name=file_name,
                mime_type=mime_type,
                corpus_id=uuid.UUID(corpus_id),
                storage_key=storage_key,
                mode=IngestMode(mode),
                target_document_id=(uuid.UUID(target_document_id) if target_document_id else None),
                auto_confirm=auto_confirm,
                job_id=uuid.UUID(job_id),
                doc_metadata=doc_metadata,
            )

            # Handle pending_review — update job and return early
            from callobuzz_rag.pipeline.models import JobStatus

            if result.status == JobStatus.PENDING_REVIEW:
                from sqlalchemy import select

                from callobuzz_rag.db.models.ingest_job import IngestJob

                job_obj = await session.execute(
                    select(IngestJob).where(IngestJob.id == uuid.UUID(job_id))
                )
                job = job_obj.scalar_one_or_none()
                if job:
                    job.status = "pending_review"
                    job.review_metadata = {
                        **(job.review_metadata or {}),
                        "similar_documents": result.similar_documents or [],
                    }
                    await session.commit()

                return {
                    "status": "pending_review",
                    "job_id": job_id,
                    "similar_documents": result.similar_documents,
                }

        # Invalidate query cache for this corpus on successful ingestion
        if result.chunks_created and result.chunks_created > 0:
            try:
                from callobuzz_rag.retrieval.query_cache import QueryResponseCache

                cache = QueryResponseCache(settings)
                await cache.invalidate_corpus(corpus_id)
                await cache.close()
            except Exception:
                logger.warning("Failed to invalidate query cache", exc_info=True)

        return {
            "status": str(result.status),
            "chunks_created": result.chunks_created,
            "vectors_stored": result.vectors_stored,
            "error": result.error,
            "failed_at_step": result.failed_at_step,
            "warnings": result.warnings,
        }
    finally:
        await providers["embedder"].close()
        await providers["vector_store"].close()
        if hasattr(providers["storage"], "close"):
            await providers["storage"].close()
        await engine.dispose()
