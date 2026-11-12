"""Langfuse observability for agent loop — traces, spans, token tracking."""

import logging
from typing import Any

from callobuzz_rag.core.tracing import get_current_trace

logger = logging.getLogger(__name__)


class AgentTracer:
    """Creates Langfuse spans for agent iterations, tool calls, and LLM calls.

    If no Langfuse trace is active, all methods are no-ops.
    """

    def __init__(self) -> None:
        self._agent_span: Any | None = None

    def start_agent_run(self, query: str, corpus_id: str) -> None:
        trace = get_current_trace()
        if trace is None:
            return
        try:
            self._agent_span = trace.span(
                name="agent_run",
                input={"query": query[:500], "corpus_id": corpus_id},
            )
        except Exception:
            logger.debug("Failed to create agent Langfuse span", exc_info=True)

    def start_iteration(self, iteration: int) -> Any | None:
        if self._agent_span is None:
            return None
        try:
            return self._agent_span.span(
                name=f"iteration_{iteration}",
                input={"iteration": iteration},
            )
        except Exception:
            return None

    def record_tool_call(
        self,
        parent_span: Any | None,
        tool_name: str,
        params: dict[str, Any],
        result_output: str,
        success: bool,
        duration_ms: float,
    ) -> None:
        if parent_span is None:
            return
        try:
            tool_span = parent_span.span(
                name=f"tool:{tool_name}",
                input={"params": str(params)[:500]},
            )
            tool_span.end(
                output={"result": result_output[:500], "success": success},
                metadata={"duration_ms": duration_ms},
                level="DEFAULT" if success else "WARNING",
            )
        except Exception:
            logger.debug("Failed to record tool call span", exc_info=True)

    def record_llm_call(
        self,
        parent_span: Any | None,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_ms: float,
    ) -> None:
        if parent_span is None:
            return
        try:
            llm_span = parent_span.span(
                name="llm_call",
                input={"model": model},
            )
            llm_span.end(
                output={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                metadata={"duration_ms": duration_ms, "model": model},
            )
        except Exception:
            logger.debug("Failed to record LLM call span", exc_info=True)

    def end_iteration(self, span: Any | None, decision: str) -> None:
        if span is None:
            return
        try:
            span.end(output={"decision": decision})
        except Exception:
            # Tracing must never break the request; log it rather than swallow
            # it silently, matching the other handlers in this class.
            logger.debug("Failed to end iteration span", exc_info=True)

    def end_agent_run(
        self,
        total_tokens: int,
        iterations: int,
        tool_calls: int,
        fallback: bool = False,
    ) -> None:
        if self._agent_span is None:
            return
        try:
            self._agent_span.end(
                output={
                    "total_tokens": total_tokens,
                    "iterations": iterations,
                    "tool_calls": tool_calls,
                    "fallback": fallback,
                },
            )
        except Exception:
            logger.debug("Failed to end agent span", exc_info=True)
