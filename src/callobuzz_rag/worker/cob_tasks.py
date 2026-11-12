"""Celery task that keeps COB's knowledge base equal to the docs shipped with the engine."""

import asyncio
import logging
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from callobuzz_rag.core.config import get_settings
from callobuzz_rag.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="callobuzz_rag.cob_sync",
    queue="periodic",
    ignore_result=True,
)
def cob_sync_task(eval: bool = True, force: bool = False) -> dict[str, Any]:
    """Sync the COB docs (idempotent: a current knowledge base is a no-op).

    `force` re-runs a sync at the same version (the operator's Resync button). `eval` asks for
    the post-sync evaluation (`cob/eval/synced.py`), which never blocks or undoes the sync.
    """
    return asyncio.run(_sync(run_eval=eval, force=force))


async def _sync(*, run_eval: bool, force: bool) -> dict[str, Any]:
    from sqlalchemy import select

    from callobuzz_rag.api.services.document_removal import remove_document
    from callobuzz_rag.cob.kb import RECORD_PREFIX, load_docs_index
    from callobuzz_rag.cob.sync import run_sync
    from callobuzz_rag.cob.system import engine_embedding, ensure_cob_system, get_system_corpus
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.db.models.cob_status import CobStatus
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.pipeline.records import TextRecord
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider
    from callobuzz_rag.worker.tasks import _run_records_pipeline

    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)

    async def ingest(
        batch: list[TextRecord], corpus_id: uuid.UUID, collection: str, job_id: uuid.UUID
    ) -> dict[str, Any]:
        return await _run_records_pipeline(
            [asdict(record) for record in batch], str(corpus_id), str(job_id), collection
        )

    async def existing_ids(corpus_id: uuid.UUID) -> set[str]:
        async with session_factory() as session:
            rows = await session.execute(
                select(Document.external_id).where(
                    Document.corpus_id == corpus_id,
                    Document.external_id.like(f"{RECORD_PREFIX}%"),
                )
            )
            return {r for r in rows.scalars().all() if r}

    async def remove(corpus_id: uuid.UUID, external_ids: list[str]) -> int:
        removed = 0
        async with session_factory() as session:
            corpus = await session.get(Corpus, corpus_id)
            if corpus is None:
                return 0
            for start in range(0, len(external_ids), 100):
                chunk = external_ids[start : start + 100]
                docs = (
                    (
                        await session.execute(
                            select(Document).where(
                                Document.corpus_id == corpus_id, Document.external_id.in_(chunk)
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                for doc in docs:
                    await remove_document(session, vector_store, corpus, doc)
                    removed += 1
                await session.commit()
        return removed

    async def recreate(corpus_id: uuid.UUID, collection: str, dims: int) -> None:
        # COB9: platform docs live in this collection too and exist only in Postgres once the
        # collection is dropped, so with any present the collection is rebuilt from the
        # stored chunks under the new embedding (the built-in docs are replaced afterwards).
        from callobuzz_rag.cob.platform import BUILTIN_ID_PREFIX
        from callobuzz_rag.retrieval.reindex import rebuild_corpus

        async with session_factory() as session:
            corpus = await session.get(Corpus, corpus_id)
            platform_docs = (
                await session.execute(
                    select(Document.id).where(
                        Document.corpus_id == corpus_id,
                        ~Document.external_id.like(f"{BUILTIN_ID_PREFIX}%")
                        | Document.external_id.is_(None),
                    )
                )
            ).first()
            if corpus is not None and platform_docs is not None:
                from callobuzz_rag.embeddings.factory import create_embedding_provider

                embedder = create_embedding_provider(settings, cache=False)
                try:
                    await rebuild_corpus(session, corpus, vector_store, embedder, dims)
                finally:
                    await embedder.close()
                return
        try:
            await vector_store.delete_collection(collection)
        except Exception:  # it may not exist yet
            logger.info("COB collection %s was not there to drop", collection, exc_info=True)
        await vector_store.create_collection(collection, dims)

    try:
        try:
            index = load_docs_index()
            async with session_factory() as session:
                await ensure_cob_system(session, settings=settings, vector_store=vector_store)
                await session.commit()
                if await get_system_corpus(session) is None:  # pragma: no cover - just created
                    raise RuntimeError("the COB workspace could not be created")
        except Exception as exc:
            # Before run_sync there is nobody to record the failure: do it here, or the card
            # shows "Queued" until an app restart long after.
            logger.error("COB docs sync could not start", exc_info=True)
            message = str(exc)[:500]
            async with session_factory() as session:
                row = await session.get(CobStatus, 1)
                if row is not None:
                    row.sync_state = "failed"
                    row.last_error = message
                    row.updated_at = datetime.now(UTC)
                    await session.commit()
            return {"failed": True, "error": message}
        outcome = await run_sync(
            session_factory,
            index=index,
            embedding=engine_embedding(settings),
            ingest=ingest,
            remove=remove,
            existing_ids=existing_ids,
            recreate_collection=recreate,
            now=lambda: datetime.now(UTC),
            force=force,
        )
        # COB7 post-sync evaluation (start)
        if run_eval and not outcome.failed and (force or not outcome.skipped):
            from callobuzz_rag.cob.eval.synced import evaluate_after_sync, system_search

            await evaluate_after_sync(
                session_factory,
                index,
                lambda session, corpus: system_search(session, settings, corpus, vector_store),
            )
        # COB7 post-sync evaluation (end)
        return asdict(outcome)
    finally:
        await vector_store.close()
        await engine.dispose()
