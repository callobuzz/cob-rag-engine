"""Tests for agent tools (corpus_search, web_search, document_fetch, memory_search)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.agent.context import AgentRunContext


class TestCorpusSearchTool:
    def test_name_and_schema(self):
        from callobuzz_rag.agent.tools.corpus_search import CorpusSearchTool

        tool = CorpusSearchTool(searcher=MagicMock())
        assert tool.name == "corpus_search"
        assert "query" in tool.parameters_schema["properties"]

    @pytest.mark.asyncio
    async def test_search_success(self):
        from callobuzz_rag.agent.tools.corpus_search import CorpusSearchTool

        mock_searcher = AsyncMock()
        mock_result = MagicMock()
        mock_result.score = 0.95
        mock_result.text = "Relevant chunk text"
        mock_searcher.search.return_value = [mock_result]

        tool = CorpusSearchTool(searcher=mock_searcher, top_k=5)
        ctx = AgentRunContext(corpus_id="test-corpus", token_budget=8000)
        result = await tool.execute({"query": "test query"}, ctx)

        assert result.success is True
        assert "Relevant chunk text" in result.output

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        from callobuzz_rag.agent.tools.corpus_search import CorpusSearchTool

        tool = CorpusSearchTool(searcher=MagicMock())
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": ""}, ctx)
        assert result.success is False

    def test_llm_tool_spec(self):
        from callobuzz_rag.agent.tools.corpus_search import CorpusSearchTool

        tool = CorpusSearchTool(searcher=MagicMock())
        spec = tool.to_llm_tool_spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "corpus_search"


class TestWebSearchTool:
    def test_name_and_schema(self):
        from callobuzz_rag.agent.tools.web_search import WebSearchTool

        tool = WebSearchTool(provider=MagicMock())
        assert tool.name == "web_search"
        assert tool.timeout == 10.0

    @pytest.mark.asyncio
    async def test_search_success(self):
        from callobuzz_rag.agent.tools.web_search import WebSearchTool
        from callobuzz_rag.websearch.base import WebSearchResult

        mock_provider = AsyncMock()
        mock_provider.search.return_value = [
            WebSearchResult(title="Test", url="https://example.com", snippet="Test snippet"),
        ]

        tool = WebSearchTool(provider=mock_provider)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": "test"}, ctx)

        assert result.success is True
        assert "Test snippet" in result.output


class TestWebSearchProviders:
    def test_serper_instantiation(self):
        from callobuzz_rag.websearch.serper import SerperProvider

        provider = SerperProvider(api_key="test-key")
        assert provider is not None

    def test_brave_instantiation(self):
        from callobuzz_rag.websearch.brave import BraveProvider

        provider = BraveProvider(api_key="test-key")
        assert provider is not None

    def test_factory_off(self):
        from callobuzz_rag.websearch.factory import create_web_search_provider

        settings = MagicMock()
        settings.web_search_provider = "off"
        assert create_web_search_provider(settings) is None

    def test_factory_serper(self):
        from callobuzz_rag.websearch.factory import create_web_search_provider
        from callobuzz_rag.websearch.serper import SerperProvider

        settings = MagicMock()
        settings.web_search_provider = "serper"
        settings.web_search_api_key.get_secret_value.return_value = "key"
        result = create_web_search_provider(settings)
        assert isinstance(result, SerperProvider)


class TestDocumentFetchTool:
    def test_name_and_schema(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        tool = DocumentFetchTool(session=MagicMock())
        assert tool.name == "document_fetch"
        assert "document_version_id" in tool.parameters_schema["properties"]
        assert tool.max_output_tokens == 4000

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        session = AsyncMock()
        chunk = MagicMock()
        chunk.text = "Document content here"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [chunk]
        session.execute = AsyncMock(return_value=mock_result)

        tool = DocumentFetchTool(session=session)
        ctx = AgentRunContext(corpus_id=str(uuid.uuid4()), token_budget=8000)
        result = await tool.execute({"document_version_id": str(uuid.uuid4())}, ctx)
        assert result.success is True
        assert "Document content" in result.output
        assert result.metadata["chunk_count"] == 1

    @pytest.mark.asyncio
    async def test_fetch_no_chunks(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        tool = DocumentFetchTool(session=session)
        ctx = AgentRunContext(corpus_id=str(uuid.uuid4()), token_budget=8000)
        result = await tool.execute({"document_version_id": str(uuid.uuid4())}, ctx)
        assert result.success is True
        assert "not found" in result.output

    @pytest.mark.asyncio
    async def test_fetch_invalid_uuid(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        tool = DocumentFetchTool(session=MagicMock())
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"document_version_id": "not-a-uuid"}, ctx)
        assert result.success is False
        assert "Invalid" in result.error

    @pytest.mark.asyncio
    async def test_fetch_missing_id(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        tool = DocumentFetchTool(session=MagicMock())
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({}, ctx)
        assert result.success is False
        assert "Missing" in result.error

    @pytest.mark.asyncio
    async def test_fetch_truncation(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        session = AsyncMock()
        chunk = MagicMock()
        chunk.text = "x" * 20000  # Exceeds 4000*4=16000
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [chunk]
        session.execute = AsyncMock(return_value=mock_result)

        tool = DocumentFetchTool(session=session)
        ctx = AgentRunContext(corpus_id=str(uuid.uuid4()), token_budget=8000)
        result = await tool.execute({"document_version_id": str(uuid.uuid4())}, ctx)
        assert result.success is True
        assert "[Document truncated]" in result.output

    def test_llm_tool_spec(self):
        from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

        tool = DocumentFetchTool(session=MagicMock())
        spec = tool.to_llm_tool_spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "document_fetch"


class TestMemorySearchTool:
    def test_name_and_schema(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        tool = MemorySearchTool(memory_service=MagicMock())
        assert tool.name == "memory_search"
        assert "query" in tool.parameters_schema["properties"]

    @pytest.mark.asyncio
    async def test_search_success(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        memory_svc = AsyncMock()
        mem = MagicMock()
        mem.memory_type = "fact"
        mem.content = "User prefers Python"
        memory_svc.search_memories.return_value = [mem]

        tool = MemorySearchTool(memory_service=memory_svc)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": "preferences"}, ctx)
        assert result.success is True
        assert "Python" in result.output
        assert result.metadata["memory_count"] == 1

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        memory_svc = AsyncMock()
        memory_svc.search_memories.return_value = []

        tool = MemorySearchTool(memory_service=memory_svc)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": "unknown topic"}, ctx)
        assert result.success is True
        assert "No relevant memories" in result.output

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        tool = MemorySearchTool(memory_service=MagicMock())
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": ""}, ctx)
        assert result.success is False
        assert "Empty query" in result.error

    @pytest.mark.asyncio
    async def test_search_error_handling(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        memory_svc = AsyncMock()
        memory_svc.search_memories.side_effect = RuntimeError("DB down")

        tool = MemorySearchTool(memory_service=memory_svc)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = await tool.execute({"query": "test"}, ctx)
        assert result.success is False
        assert "DB down" in result.error

    def test_llm_tool_spec(self):
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        tool = MemorySearchTool(memory_service=MagicMock())
        spec = tool.to_llm_tool_spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "memory_search"
