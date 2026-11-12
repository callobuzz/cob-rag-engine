"""Migration verification — drain gate, chunk/memory reconciliation."""

import asyncio
import logging
import time
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import MigrationError
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus_migration import CorpusMigration
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.embedding import Embedding as EmbeddingModel
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

logger = logging.getLogger(__name__)


async def drain_gate(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    gate_start: float,
    settings: Settings,
) -> None:
    """Wait until no in-flight ingest jobs predate the gate.

    The pipeline runs in a single DB transaction — per-step statuses are
    flush-only. From another session, an in-flight job is visible only in
    its committed status (queued) until the final commit flips it terminal.
    """
    from datetime import UTC, datetime

    gate_start_dt = datetime.fromtimestamp(gate_start, tz=UTC)
    timeout = settings.migration_drain_timeout_seconds
    poll_interval = 5.0
    start = time.monotonic()

    while True:
        stmt = select(func.count(IngestJob.id)).where(
            IngestJob.corpus_id == corpus_id,
            IngestJob.status.notin_(
                ["completed", "failed", "cancelled", "pending_review", "expired", "skipped"]
            ),
            IngestJob.created_at < gate_start_dt,
        )
        result = await session.execute(stmt)
        count = result.scalar_one() or 0

        if count == 0:
            logger.info("Drain gate passed for corpus %s", corpus_id)
            return

        elapsed = time.monotonic() - start
        if elapsed > timeout:
            raise MigrationError(
                f"Drain gate timeout ({timeout}s): {count} in-flight jobs for corpus {corpus_id}"
            )

        logger.info(
            "Drain gate: %d in-flight jobs, waiting (%.0fs / %ds)...",
            count,
            elapsed,
            timeout,
        )
        await asyncio.sleep(poll_interval)


async def reconcile_chunks(
    session: AsyncSession,
    migration: CorpusMigration,
    embedder: EmbeddingProvider,
    vector_store: QdrantVectorProvider,
    settings: Settings,
) -> int:
    """Re-embed any active chunks missing from the new collection.

    Returns count of chunks reconciled.
    """
    from callobuzz_rag.migration.strategies import re_embed_chunks

    corpus_id = migration.corpus_id

    active_chunk_count = await _count_active_chunks(session, corpus_id)
    new_model = migration.to_config["model"]

    audit_stmt = (
        select(func.count(EmbeddingModel.id))
        .join(Chunk, EmbeddingModel.chunk_id == Chunk.id)
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(
            Chunk.corpus_id == corpus_id,
            DocumentVersion.status == "active",
            EmbeddingModel.model == new_model,
        )
    )
    result = await session.execute(audit_stmt)
    audit_count = result.scalar_one() or 0

    missing = active_chunk_count - audit_count
    if missing > 0:
        logger.warning(
            "Reconciliation: %d chunks missing audit rows for model %s, re-embedding",
            missing,
            new_model,
        )
        migration.chunks_done = 0
        migration.chunks_total = missing
        await re_embed_chunks(session, migration, embedder, vector_store, settings)

    logger.info(
        "Chunk reconciliation complete: %d active, %d audited, %d reconciled",
        active_chunk_count,
        audit_count,
        missing,
    )
    return missing


async def reconcile_memories(
    session: AsyncSession,
    migration: CorpusMigration,
    embedder: EmbeddingProvider,
    settings: Settings,
) -> int:
    """Re-embed any memories where embedding_staged is NULL but embedding is not.

    Loops until fully drained (no stragglers).
    """
    corpus_id = migration.corpus_id
    total_reconciled = 0
    batch_size = settings.migration_batch_size

    while True:
        stmt = (
            select(Memory)
            .where(
                Memory.corpus_id == corpus_id,
                Memory.embedding.isnot(None),
                Memory.embedding_staged.is_(None),
            )
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        memories = list(result.scalars().all())
        if not memories:
            break

        texts = [m.content for m in memories]
        embeddings = await embedder.embed_texts(texts)
        for mem, emb in zip(memories, embeddings, strict=True):
            mem.embedding_staged = emb.vector
        await session.flush()
        total_reconciled += len(memories)

    logger.info("Memory reconciliation: %d stragglers re-embedded", total_reconciled)
    return total_reconciled


async def _count_active_chunks(session: AsyncSession, corpus_id: uuid.UUID) -> int:
    stmt = (
        select(func.count(Chunk.id))
        .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
        .where(
            Chunk.corpus_id == corpus_id,
            DocumentVersion.status == "active",
        )
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0
