"""End-to-end memory extraction and retrieval test.

Extract memories from conversation -> verify stored -> search -> verify found.
Requires Docker (PostgreSQL via testcontainers).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.llm.models import LLMResponse, LLMUsage
from tests.integration.conftest import new_tenant_id


@pytest.mark.integration
async def test_memory_extraction_and_retrieval(db_session):
    """Test: extract memories from exchange -> store -> retrieve by similarity."""
    from callobuzz_rag.memory.extraction import MemoryExtractionService
    from callobuzz_rag.memory.retrieval import MemoryRetrievalService

    corpus = Corpus(
        name="Memory Test Corpus", description="Test", tenant_id=await new_tenant_id(db_session)
    )
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    conv = Conversation(corpus_id=corpus.id, title="Memory test")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    llm = AsyncMock()
    llm.complete_json.return_value = LLMResponse(
        content='[{"type": "fact", "content": "User uses PostgreSQL for their main database"}, '
        '{"type": "preference", "content": "User prefers async Python frameworks"}]',
        model="test",
        usage=LLMUsage(),
    )

    # The two extracted memories must embed to *distinguishable* vectors. The previous
    # fake varied one of 1024 dimensions by 0.1, giving cosine ~0.9999 — above the
    # service's 0.9 DUPLICATE_THRESHOLD — so the second memory deduped into the first
    # and this test asserted 2 rows against a DB that only ever held 1.
    # Keyed on content rather than call order, so it does not depend on iteration order.
    async def _embed(text):
        vector = [0.0] * 1024
        if "PostgreSQL" in text or "database" in text:
            vector[0], vector[1] = 1.0, 0.3
        else:
            vector[2], vector[3] = 1.0, 0.3
        return MagicMock(vector=vector)

    embedder = AsyncMock()
    embedder.embed_query = _embed

    extraction_svc = MemoryExtractionService(db_session, llm, embedder)
    stored = await extraction_svc.extract_and_store(
        corpus_id=corpus.id,
        conversation_id=conv.id,
        user_message="I use PostgreSQL for our main database and prefer async Python",
        assistant_message="Great choices! PostgreSQL is solid.",
    )
    await db_session.commit()
    assert len(stored) == 2

    result = await db_session.execute(select(Memory).where(Memory.corpus_id == corpus.id))
    db_memories = result.scalars().all()
    assert len(db_memories) == 2
    types = {m.memory_type for m in db_memories}
    assert "fact" in types
    assert "preference" in types

    retrieval_svc = MemoryRetrievalService(db_session, embedder)
    found = await retrieval_svc.search_memories(corpus.id, "database preferences")
    assert len(found) >= 1
    assert any("PostgreSQL" in m.content for m in found)


@pytest.mark.integration
async def test_memory_dedup(db_session):
    """Test that similar memories are deduplicated (updated, not duplicated)."""
    from callobuzz_rag.memory.extraction import MemoryExtractionService

    corpus = Corpus(
        name="Dedup Test", description="Test", tenant_id=await new_tenant_id(db_session)
    )
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    conv = Conversation(corpus_id=corpus.id, title="Dedup test")
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    same_vector = [0.5] * 1024
    embedder = AsyncMock()
    embedder.embed_query = AsyncMock(return_value=MagicMock(vector=same_vector))

    llm = AsyncMock()

    llm.complete_json.return_value = LLMResponse(
        content='[{"type": "fact", "content": "User uses PostgreSQL"}]',
        model="test",
        usage=LLMUsage(),
    )
    svc = MemoryExtractionService(db_session, llm, embedder)
    first = await svc.extract_and_store(corpus.id, conv.id, "I use PostgreSQL", "Cool!")
    await db_session.commit()
    assert len(first) == 1

    llm.complete_json.return_value = LLMResponse(
        content='[{"type": "fact", "content": "User uses PostgreSQL 16"}]',
        model="test",
        usage=LLMUsage(),
    )
    await svc.extract_and_store(corpus.id, conv.id, "We upgraded to PG 16", "Nice!")
    await db_session.commit()

    result = await db_session.execute(select(Memory).where(Memory.corpus_id == corpus.id))
    all_memories = result.scalars().all()
    assert len(all_memories) == 1
    assert "PostgreSQL 16" in all_memories[0].content
