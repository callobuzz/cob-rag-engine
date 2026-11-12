"""Tests for semantic memory extraction and retrieval."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.llm.models import LLMResponse, LLMUsage


class TestMemoryExtraction:
    @pytest.mark.asyncio
    async def test_extract_memories(self):
        from callobuzz_rag.memory.extraction import MemoryExtractionService

        session = AsyncMock()
        session.flush = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        llm = AsyncMock()
        llm.complete_json.return_value = LLMResponse(
            content='[{"type": "fact", "content": "User uses PostgreSQL"}]',
            model="test",
            usage=LLMUsage(),
        )

        embedder = AsyncMock()
        embedder.embed_query.return_value = MagicMock(vector=[0.1] * 1024)

        svc = MemoryExtractionService(session, llm, embedder)
        memories = await svc.extract_and_store(
            uuid.uuid4(), uuid.uuid4(), "I use PostgreSQL", "Good choice!"
        )
        assert len(memories) == 1
        assert memories[0].content == "User uses PostgreSQL"

    @pytest.mark.asyncio
    async def test_extract_empty(self):
        from callobuzz_rag.memory.extraction import MemoryExtractionService

        session = AsyncMock()
        llm = AsyncMock()
        llm.complete_json.return_value = LLMResponse(content="[]", model="test", usage=LLMUsage())

        svc = MemoryExtractionService(session, llm, AsyncMock())
        memories = await svc.extract_and_store(uuid.uuid4(), uuid.uuid4(), "Hi", "Hello!")
        assert memories == []

    @pytest.mark.asyncio
    async def test_extract_invalid_json(self):
        from callobuzz_rag.memory.extraction import MemoryExtractionService

        session = AsyncMock()
        llm = AsyncMock()
        llm.complete_json.return_value = LLMResponse(
            content="not json", model="test", usage=LLMUsage()
        )

        svc = MemoryExtractionService(session, llm, AsyncMock())
        memories = await svc.extract_and_store(uuid.uuid4(), uuid.uuid4(), "Hi", "Hello!")
        assert memories == []

    @pytest.mark.asyncio
    async def test_extract_filters_invalid_types(self):
        from callobuzz_rag.memory.extraction import MemoryExtractionService

        session = AsyncMock()
        session.flush = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        llm = AsyncMock()
        llm.complete_json.return_value = LLMResponse(
            content=(
                '[{"type": "invalid", "content": "Bad type"}, {"type": "fact", "content": "Good"}]'
            ),
            model="test",
            usage=LLMUsage(),
        )

        embedder = AsyncMock()
        embedder.embed_query.return_value = MagicMock(vector=[0.1] * 1024)

        svc = MemoryExtractionService(session, llm, embedder)
        memories = await svc.extract_and_store(uuid.uuid4(), uuid.uuid4(), "test", "test")
        assert len(memories) == 1
        assert memories[0].memory_type == "fact"


class TestCosineSimilarity:
    def test_identical_vectors(self):
        from callobuzz_rag.memory.extraction import _cosine_similarity

        a = [1.0, 0.0, 0.0]
        assert _cosine_similarity(a, a) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        from callobuzz_rag.memory.extraction import _cosine_similarity

        assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_empty_vectors(self):
        from callobuzz_rag.memory.extraction import _cosine_similarity

        assert _cosine_similarity([], []) == 0.0

    def test_different_lengths(self):
        from callobuzz_rag.memory.extraction import _cosine_similarity

        assert _cosine_similarity([1, 0], [1, 0, 0]) == 0.0


class TestMemoryRetrieval:
    @pytest.mark.asyncio
    async def test_search_returns_similar(self):
        from callobuzz_rag.memory.retrieval import MemoryRetrievalService

        session = AsyncMock()
        mem = MagicMock()
        mem.embedding = [0.1] * 1024
        mem.content = "User likes Python"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mem]
        session.execute = AsyncMock(return_value=mock_result)

        embedder = AsyncMock()
        embedder.embed_query.return_value = MagicMock(vector=[0.1] * 1024)

        svc = MemoryRetrievalService(session, embedder)
        results = await svc.search_memories("corpus-1", "Python preferences")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_empty_corpus(self):
        from callobuzz_rag.memory.retrieval import MemoryRetrievalService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        embedder = AsyncMock()
        embedder.embed_query.return_value = MagicMock(vector=[0.1] * 10)

        svc = MemoryRetrievalService(session, embedder)
        results = await svc.search_memories("corpus-1", "anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_embedding_failure(self):
        from callobuzz_rag.memory.retrieval import MemoryRetrievalService

        session = AsyncMock()
        embedder = AsyncMock()
        embedder.embed_query.side_effect = RuntimeError("Embedding failed")

        svc = MemoryRetrievalService(session, embedder)
        results = await svc.search_memories("corpus-1", "test")
        assert results == []
