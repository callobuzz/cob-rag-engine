"""End-to-end agent loop and factory test.

Build agent with tools -> verify wiring.
Uses mocked providers but real tool implementations.
"""

from unittest.mock import MagicMock

from callobuzz_rag.agent.config import AgentConfig


class TestAgentFactory:
    def test_all_tools_wired(self):
        """Test factory wires all tools when all providers are available."""
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
            searcher=MagicMock(),
            web_search_provider=MagicMock(),
            memory_retrieval_service=MagicMock(),
            config=AgentConfig(enabled=True),
        )

        tool_names = list(loop._registry._tools.keys())
        assert "corpus_search" in tool_names
        assert "document_fetch" in tool_names
        assert "web_search" in tool_names
        assert "memory_search" in tool_names

    def test_minimal_tools(self):
        """Test factory only registers document_fetch when no optional providers given."""
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
        )
        tool_names = list(loop._registry._tools.keys())
        assert "document_fetch" in tool_names
        assert len(tool_names) == 1

    def test_partial_tools_no_web_search(self):
        """Test factory omits web_search when provider not given."""
        from callobuzz_rag.agent.factory import build_agent_loop

        loop = build_agent_loop(
            llm_provider=MagicMock(),
            session=MagicMock(),
            searcher=MagicMock(),
        )
        tool_names = list(loop._registry._tools.keys())
        assert "corpus_search" in tool_names
        assert "document_fetch" in tool_names
        assert "web_search" not in tool_names
        assert "memory_search" not in tool_names


class TestCircuitBreakerIntegration:
    def test_circuit_breaker_disables_failing_tool(self):
        """Test circuit breaker trips after threshold failures."""
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=2, window_seconds=60, cooldown_seconds=10)

        assert cb.is_available("flaky_tool")
        cb.record_failure("flaky_tool")
        cb.record_failure("flaky_tool")
        assert not cb.is_available("flaky_tool")
        assert cb.is_available("good_tool")

        available = cb.get_available_tools(["flaky_tool", "good_tool"])
        assert available == ["good_tool"]

    def test_circuit_breaker_success_resets(self):
        """Test recording success clears failure state."""
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, window_seconds=60, cooldown_seconds=10)
        cb.record_failure("tool_a")
        cb.record_failure("tool_a")
        assert cb.is_available("tool_a")
        cb.record_success("tool_a")
        cb.record_failure("tool_a")
        assert cb.is_available("tool_a")

    def test_rate_limiter_per_corpus(self):
        """Test rate limiter tracks per-corpus independently."""
        from callobuzz_rag.agent.rate_limiter import AgentRateLimiter

        rl = AgentRateLimiter(max_per_minute=3)
        assert not rl.is_rate_limited("corpus-A")
        assert not rl.is_rate_limited("corpus-A")
        assert not rl.is_rate_limited("corpus-A")
        assert rl.is_rate_limited("corpus-A")
        assert not rl.is_rate_limited("corpus-B")
