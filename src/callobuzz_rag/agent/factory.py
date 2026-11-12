"""Factory for building a fully-wired AgentLoop with all tools."""

import logging
from typing import Any

from callobuzz_rag.agent.circuit_breaker import CircuitBreaker
from callobuzz_rag.agent.config import AgentConfig
from callobuzz_rag.agent.loop import AgentLoop
from callobuzz_rag.agent.rate_limiter import AgentRateLimiter
from callobuzz_rag.agent.registry import ToolRegistry
from callobuzz_rag.agent.tools.corpus_search import CorpusSearchTool
from callobuzz_rag.agent.tools.document_fetch import DocumentFetchTool

logger = logging.getLogger(__name__)

# Module-level singletons for circuit breaker and rate limiter
_circuit_breaker = CircuitBreaker()
_rate_limiter = AgentRateLimiter()


def build_agent_loop(
    llm_provider: Any,
    session: Any,
    searcher: Any | None = None,
    web_search_provider: Any | None = None,
    memory_retrieval_service: Any | None = None,
    config: AgentConfig | None = None,
) -> AgentLoop:
    """Build a fully-wired AgentLoop with all available tools.

    Creates a ToolRegistry, registers tools based on available providers,
    and returns a configured AgentLoop.
    """
    registry = ToolRegistry()

    # Always available: corpus search (if searcher provided)
    if searcher is not None:
        registry.register(CorpusSearchTool(searcher=searcher))

    # Always available: document fetch
    registry.register(DocumentFetchTool(session=session))

    # Optional: web search (requires provider)
    if web_search_provider is not None:
        from callobuzz_rag.agent.tools.web_search import WebSearchTool

        registry.register(WebSearchTool(provider=web_search_provider))

    # Optional: memory search (requires memory retrieval service)
    if memory_retrieval_service is not None:
        from callobuzz_rag.agent.tools.memory_search import MemorySearchTool

        registry.register(MemorySearchTool(memory_service=memory_retrieval_service))

    return AgentLoop(
        llm_provider=llm_provider,
        tool_registry=registry,
        config=config or AgentConfig(),
        circuit_breaker=_circuit_breaker,
        rate_limiter=_rate_limiter,
    )
