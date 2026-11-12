"""Tests for circuit breaker and rate limiter."""


class TestCircuitBreaker:
    def test_available_by_default(self):
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3)
        assert cb.is_available("test_tool")

    def test_trips_after_threshold(self):
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3, window_seconds=60, cooldown_seconds=60)
        cb.record_failure("test_tool")
        cb.record_failure("test_tool")
        assert cb.is_available("test_tool")
        cb.record_failure("test_tool")
        assert not cb.is_available("test_tool")

    def test_resets_after_success(self):
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("test_tool")
        cb.record_failure("test_tool")
        cb.record_success("test_tool")
        cb.record_failure("test_tool")  # Only 1 failure now
        assert cb.is_available("test_tool")

    def test_get_available_tools(self):
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("bad_tool")
        available = cb.get_available_tools(["good_tool", "bad_tool"])
        assert "good_tool" in available
        assert "bad_tool" not in available

    def test_independent_tools(self):
        from callobuzz_rag.agent.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure("tool_a")
        assert not cb.is_available("tool_a")
        assert cb.is_available("tool_b")


class TestAgentRateLimiter:
    def test_allows_under_limit(self):
        from callobuzz_rag.agent.rate_limiter import AgentRateLimiter

        rl = AgentRateLimiter(max_per_minute=5)
        assert not rl.is_rate_limited("corpus-1")
        assert not rl.is_rate_limited("corpus-1")

    def test_blocks_over_limit(self):
        from callobuzz_rag.agent.rate_limiter import AgentRateLimiter

        rl = AgentRateLimiter(max_per_minute=2)
        assert not rl.is_rate_limited("corpus-1")
        assert not rl.is_rate_limited("corpus-1")
        assert rl.is_rate_limited("corpus-1")  # 3rd blocked

    def test_independent_per_corpus(self):
        from callobuzz_rag.agent.rate_limiter import AgentRateLimiter

        rl = AgentRateLimiter(max_per_minute=1)
        assert not rl.is_rate_limited("corpus-1")
        assert not rl.is_rate_limited("corpus-2")

    def test_check_without_record(self):
        from callobuzz_rag.agent.rate_limiter import AgentRateLimiter

        rl = AgentRateLimiter(max_per_minute=1)
        assert rl.check("corpus-1")  # check only, no record
        assert rl.check("corpus-1")  # still allowed


class TestAgentTracer:
    def test_no_op_without_langfuse(self):
        from callobuzz_rag.agent.observability import AgentTracer

        tracer = AgentTracer()
        # All methods should be no-ops when no trace is active
        tracer.start_agent_run("test query", "corpus-1")
        span = tracer.start_iteration(1)
        assert span is None
        tracer.record_tool_call(None, "test", {}, "output", True, 10.0)
        tracer.record_llm_call(None, "gpt-4", 100, 50, 500.0)
        tracer.end_iteration(None, "test")
        tracer.end_agent_run(100, 1, 1)
