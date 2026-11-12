"""Semantic memory extraction — Tier 2."""

import json
import logging
import math
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.memory import MEMORY_TYPES, Memory

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """Analyze this conversation exchange and extract key memories.

For each memory, provide:
- type: "fact" (objective info), "preference" (user likes/dislikes), or "entity" (person/org/product)
- content: A concise statement of the memory

Return a JSON array. Only include clear, useful information. Return [] if nothing worth remembering.

Example:
[
  {{"type": "fact", "content": "User's company uses PostgreSQL for their main database"}},
  {{"type": "preference", "content": "User prefers Python over JavaScript"}}
]

Exchange:
User: {user_message}
Assistant: {assistant_message}

Extracted memories (JSON array):"""

DUPLICATE_THRESHOLD = 0.9


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class MemoryExtractionService:
    """Extract and store semantic memories from conversations."""

    def __init__(
        self,
        session: AsyncSession,
        llm_provider: Any,
        embedding_provider: Any,
        embedding_model: str | None = None,
        settings: Any | None = None,
        corpus_id: UUID | None = None,
    ) -> None:
        self._session = session
        self._llm = llm_provider
        self._embedder = embedding_provider
        self._embedding_model = embedding_model
        self._settings = settings
        self._corpus_id = corpus_id

    async def extract_and_store(
        self,
        corpus_id: UUID,
        conversation_id: UUID,
        user_message: str,
        assistant_message: str,
    ) -> list[Memory]:
        """Extract memories from an exchange and store (with dedup)."""
        from callobuzz_rag.llm.models import LLMMessage

        prompt = EXTRACTION_PROMPT.format(
            user_message=user_message[:1000],
            assistant_message=assistant_message[:1000],
        )

        try:
            response = await self._llm.complete_json(
                [LLMMessage(role="user", content=prompt)],
                max_tokens=500,
                temperature=0.1,
            )
            memories_raw = json.loads(response.content)
        except Exception as e:
            logger.warning("Memory extraction failed: %s", e)
            return []

        if not isinstance(memories_raw, list):
            return []

        dual_target = await self._get_dual_write_target(corpus_id)

        stored = []
        for mem_data in memories_raw:
            if not isinstance(mem_data, dict):
                continue
            mem_type = mem_data.get("type", "fact")
            content = mem_data.get("content", "").strip()
            if not content or mem_type not in MEMORY_TYPES:
                continue

            # Generate embedding
            try:
                embed_result = await self._embedder.embed_query(content)
                embedding = embed_result.vector
            except Exception as e:
                logger.warning("Memory embedding failed: %s", e)
                embedding = None

            # Dual-embed for staging (best-effort)
            staged_embedding: list[float] | None = None
            if dual_target and embedding:
                try:
                    staged_result = await dual_target.embedder.embed_query(content)
                    staged_embedding = staged_result.vector
                except Exception:
                    logger.warning("Memory dual-embed failed (best-effort)")

            # Dedup check
            if embedding:
                existing = await self._find_duplicate(corpus_id, embedding)
                if existing:
                    existing.content = content
                    existing.embedding = embedding
                    existing.embedding_model = self._embedding_model
                    if dual_target:
                        existing.embedding_staged = staged_embedding
                    existing.metadata_ = {
                        **(existing.metadata_ or {}),
                        "updated_from_conversation": str(conversation_id),
                    }
                    stored.append(existing)
                    continue

            # Store new memory
            memory = Memory(
                corpus_id=corpus_id,
                conversation_id=conversation_id,
                memory_type=mem_type,
                content=content,
                embedding=embedding,
                embedding_model=self._embedding_model,
                embedding_staged=staged_embedding,
                metadata_={"source_conversation": str(conversation_id)},
            )
            self._session.add(memory)
            stored.append(memory)

        if stored:
            await self._session.flush()
        return stored

    async def _find_duplicate(self, corpus_id: UUID, embedding: list[float]) -> Memory | None:
        """Find an existing memory with cosine similarity > threshold."""
        result = await self._session.execute(
            select(Memory).where(
                Memory.corpus_id == corpus_id,
                Memory.embedding.isnot(None),
            )
        )
        existing_memories = result.scalars().all()

        for mem in existing_memories:
            if mem.embedding and _cosine_similarity(embedding, mem.embedding) > DUPLICATE_THRESHOLD:
                return mem
        return None

    async def _get_dual_write_target(self, corpus_id: UUID) -> Any:
        """Check for active migration requiring dual-write on memories."""
        if self._settings is None:
            return None
        try:
            from callobuzz_rag.migration.context import get_migration_context

            return await get_migration_context(self._session, corpus_id, self._settings)
        except Exception:
            return None
