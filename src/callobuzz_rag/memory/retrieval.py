"""Memory retrieval — similarity search against stored memories."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.memory.extraction import _cosine_similarity

logger = logging.getLogger(__name__)


class MemoryRetrievalService:
    """Search memories by semantic similarity."""

    def __init__(self, session: AsyncSession, embedding_provider: Any) -> None:
        self._session = session
        self._embedder = embedding_provider

    async def search_memories(
        self,
        corpus_id: str | UUID,
        query: str,
        top_k: int = 5,
    ) -> list[Memory]:
        """Find memories most similar to the query."""
        try:
            embed_result = await self._embedder.embed_query(query)
            query_embedding = embed_result.vector
        except Exception as e:
            logger.warning("Memory search embedding failed: %s", e)
            return []

        result = await self._session.execute(
            select(Memory).where(
                Memory.corpus_id == corpus_id,
                Memory.embedding.isnot(None),
            )
        )
        all_memories = result.scalars().all()

        scored = []
        for mem in all_memories:
            if mem.embedding:
                sim = _cosine_similarity(query_embedding, mem.embedding)
                scored.append((mem, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [mem for mem, _ in scored[:top_k]]
