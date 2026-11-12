"""Re-embed strategy — blue-green re-embedding from stored chunks.

Phase A strategy: re-embeds existing chunks into a new Qdrant collection
using the target embedding model. Memories are staged (not swapped until cutover).
"""

import logging
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from callobuzz_rag.chunking.index_text import index_text
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.config_resolver import ResolvedEmbeddingConfig
from callobuzz_rag.core.exceptions import MigrationError
from callobuzz_rag.db.models.chunk import Chunk
from callobuzz_rag.db.models.corpus_migration import CorpusMigration
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.embedding import Embedding as EmbeddingModel
from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.embeddings.base import EmbeddingProvider
from callobuzz_rag.embeddings.factory import create_embedding_provider
from callobuzz_rag.pipeline.payload import chunk_payload
from callobuzz_rag.retrieval.sparse import text_to_sparse_vector
from callobuzz_rag.vectordb.base import VectorPoint
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

logger = logging.getLogger(__name__)


async def preflight_probe(
    settings: Settings, to_config: dict[str, Any], api_key: str | None = None
) -> EmbeddingProvider:
    """Build the target embedder and probe it. Returns embedder on success."""
    resolved = ResolvedEmbeddingConfig(
        provider=to_config["provider"],
        model=to_config["model"],
        dimensions=to_config["dimensions"],
        # The corpus's own hosted-embedding key; the snapshot never stores a key.
        api_key=api_key or to_config.get("api_key"),
    )
    # Uncached: this runs inside a worker task's own event loop.
    embedder = create_embedding_provider(settings, resolved=resolved, cache=False)
    result = await embedder.embed_texts(["migration preflight probe"])
    if not result:
        raise MigrationError("Preflight probe returned no embeddings")
    actual_dims = result[0].dimensions
    expected_dims = to_config["dimensions"]
    if actual_dims != expected_dims:
        raise MigrationError(
            f"Preflight dimension mismatch: expected {expected_dims}, got {actual_dims}"
        )
    logger.info("Preflight probe OK: %s dims=%d", to_config["model"], actual_dims)
    return embedder


async def count_active_chunks(session: AsyncSession, corpus_id: uuid.UUID) -> int:
    """Count chunks belonging to active document versions."""
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


async def count_memories_with_embedding(session: AsyncSession, corpus_id: uuid.UUID) -> int:
    stmt = select(func.count(Memory.id)).where(
        Memory.corpus_id == corpus_id,
        Memory.embedding.isnot(None),
    )
    result = await session.execute(stmt)
    return result.scalar_one() or 0


async def re_embed_chunks(
    session: AsyncSession,
    migration: CorpusMigration,
    embedder: EmbeddingProvider,
    vector_store: QdrantVectorProvider,
    settings: Settings,
) -> None:
    """Re-embed all active chunks in batches into the new collection."""
    corpus_id = migration.corpus_id
    batch_size = settings.migration_batch_size
    offset = 0

    while True:
        stmt = (
            select(Chunk)
            .join(DocumentVersion, Chunk.document_version_id == DocumentVersion.id)
            .where(
                Chunk.corpus_id == corpus_id,
                DocumentVersion.status == "active",
            )
            .options(
                selectinload(Chunk.document_version).selectinload(DocumentVersion.document),
                selectinload(Chunk.metadata_record),
            )
            .order_by(Chunk.id)
            .offset(offset)
            .limit(batch_size)
        )
        result = await session.execute(stmt)
        chunks = list(result.scalars().all())
        if not chunks:
            break

        texts = [index_text(c.text, c.structure_path) for c in chunks]
        embeddings = await embedder.embed_texts(texts)

        points: list[VectorPoint] = []
        emb_models: list[EmbeddingModel] = []
        for chunk, emb, text in zip(chunks, embeddings, texts, strict=True):
            document = chunk.document_version.document
            payload = chunk_payload(
                chunk,
                corpus_id,
                document.id,
                document.doc_metadata,
                chunk.metadata_record.keywords if chunk.metadata_record else None,
            )
            sparse_data = text_to_sparse_vector(text)
            sparse_vec = sparse_data.to_sparse_vector() if not sparse_data.is_empty() else None
            points.append(
                VectorPoint(
                    id=str(chunk.id),
                    vector=emb.vector,
                    payload=payload.to_dict(),
                    sparse_vector=sparse_vec,
                )
            )
            emb_models.append(
                EmbeddingModel(
                    chunk_id=chunk.id,
                    model=migration.to_config["model"],
                    model_version=emb.model_version,
                    vector_ref=str(chunk.id),
                    vector_type="text",
                    dimensions=emb.dimensions,
                )
            )

        await vector_store.upsert(migration.new_collection, points)
        session.add_all(emb_models)
        migration.chunks_done += len(chunks)
        await session.flush()

        offset += batch_size
        logger.info(
            "Migration %s: re-embedded %d/%d chunks",
            migration.id,
            migration.chunks_done,
            migration.chunks_total,
        )


async def stage_memories(
    session: AsyncSession,
    migration: CorpusMigration,
    embedder: EmbeddingProvider,
    settings: Settings,
) -> None:
    """Re-embed memories with the target model into embedding_staged."""
    corpus_id = migration.corpus_id
    batch_size = settings.migration_batch_size
    offset = 0

    while True:
        stmt = (
            select(Memory)
            .where(
                Memory.corpus_id == corpus_id,
                Memory.embedding.isnot(None),
            )
            .order_by(Memory.id)
            .offset(offset)
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

        migration.memories_done += len(memories)
        await session.flush()
        offset += batch_size
        logger.info(
            "Migration %s: staged %d/%d memories",
            migration.id,
            migration.memories_done,
            migration.memories_total,
        )
