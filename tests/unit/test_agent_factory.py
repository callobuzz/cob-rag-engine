"""Tests for agent factory."""

from unittest.mock import MagicMock

from callobuzz_rag.agent.config import AgentConfig


class TestBuildAgentLoop:
    def test_builds_with_all_providers(self):
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
            searcher=MagicMock(),
            web_search_provider=MagicMock(),
            memory_retrieval_service=MagicMock(),
            config=AgentConfig(),
        )
        assert loop is not None
        assert len(loop._registry._tools) == 4

    def test_builds_minimal(self):
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
        )
        assert loop is not None
        assert len(loop._registry._tools) == 1

    def test_builds_with_searcher_only(self):
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
            searcher=MagicMock(),
        )
        assert len(loop._registry._tools) == 2

    def test_has_circuit_breaker(self):
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
        )
        assert loop._circuit_breaker is not None

    def test_has_rate_limiter(self):
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
        )
        assert loop._rate_limiter is not None
