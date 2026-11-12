"""Core agent loop — LLM tool-calling iteration."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.config import AgentConfig
from callobuzz_rag.agent.context import AgentRunContext
from callobuzz_rag.agent.registry import ToolRegistry

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """You are an AI assistant with access to tools for searching and \
retrieving information.

Instructions:
1. Analyze the user's query carefully.
2. Decide which tools to use to find the answer. You can call multiple tools.
3. After searching, evaluate if the results are relevant. If not, rewrite the query \
and search again.
4. Before giving your final answer, verify it is supported by the evidence you found.
5. Cite your sources when possible.
6. If you cannot find a good answer, say so honestly rather than guessing.

Be concise and accurate. Prefer information from the corpus over general knowledge."""


@dataclass
class AgentResult:
    """Result from a complete agent run."""

    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    total_tokens: int = 0
    fallback: bool = False
    error: str | None = None


class AgentLoop:
    """Core agent loop using LLM tool-calling."""

    def __init__(
        self,
        llm_provider: Any,  # LiteLLMProvider or similar
        tool_registry: ToolRegistry,
        config: AgentConfig,
        circuit_breaker: Any | None = None,
        rate_limiter: Any | None = None,
    ) -> None:
        self._llm = llm_provider
        self._registry = tool_registry
        self._config = config
        self._circuit_breaker = circuit_breaker
        self._rate_limiter = rate_limiter

    async def run(
        self,
        query: str,
        context: AgentRunContext,
        conversation_history: list[dict[str, str]] | None = None,
        memories: list[str] | None = None,
    ) -> AgentResult:
        """Execute the agent loop.

        Args:
            query: User's question.
            context: Per-request state tracker.
            conversation_history: Previous messages for context.
            memories: Relevant memories to inject.

        Returns:
            AgentResult with final answer and metadata.
        """
        import litellm  # deferred

        # Rate limit check
        if self._rate_limiter and self._rate_limiter.is_rate_limited(context.corpus_id):
            return AgentResult(content="", fallback=True, error="Rate limit exceeded")

        from callobuzz_rag.agent.observability import AgentTracer

        tracer = AgentTracer()
        tracer.start_agent_run(query, context.corpus_id)

        tools = self._registry.get_enabled_tools(self._config)
        tool_specs = [t.to_llm_tool_spec() for t in tools]
        tool_map = {t.name: t for t in tools}

        # Build messages
        messages = self._build_messages(query, conversation_history, memories)
        tool_call_log: list[dict[str, Any]] = []

        model = self._config.model or None  # None = use litellm default

        for _i in range(self._config.max_iterations):
            if context.should_stop():
                break

            context.increment_iteration()
            iter_span = tracer.start_iteration(context.iteration)

            try:
                kwargs: dict[str, Any] = {
                    "messages": messages,
                    "temperature": 0.1,  # Low for reasoning
                    "max_tokens": 2048,
                }
                if model:
                    kwargs["model"] = model
                if tool_specs:
                    kwargs["tools"] = tool_specs

                iter_start = time.monotonic()
                # L5a demo: the hard daily ceiling on model calls
                from callobuzz_rag.demo import budget as demo_budget

                await demo_budget.check_model_call(None, model)
                # /L5a demo
                response = await litellm.acompletion(**kwargs)
            except Exception as exc:
                logger.error("Agent LLM call failed: %s", exc)
                tracer.end_iteration(iter_span, "error")
                tracer.end_agent_run(
                    context.tokens_used,
                    context.iteration,
                    len(tool_call_log),
                    fallback=True,
                )
                return AgentResult(
                    content="",
                    tool_calls=tool_call_log,
                    iterations=context.iteration,
                    total_tokens=context.tokens_used,
                    fallback=True,
                    error=str(exc),
                )

            # Track tokens
            if response.usage:
                context.add_tokens(response.usage.total_tokens)

            # Record LLM call in tracer
            llm_duration = (time.monotonic() - iter_start) * 1000
            usage = response.usage
            tracer.record_llm_call(
                iter_span,
                model=model or "default",
                prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
                completion_tokens=(getattr(usage, "completion_tokens", 0) if usage else 0),
                duration_ms=llm_duration,
            )

            choice = response.choices[0]
            assistant_msg = choice.message

            # Check if LLM wants to call tools
            if assistant_msg.tool_calls:
                # Add assistant message (with tool calls) to history
                messages.append(
                    {
                        "role": "assistant",
                        "content": assistant_msg.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in assistant_msg.tool_calls
                        ],
                    }
                )

                # Execute tools (parallel if multiple)
                tool_results = await self._execute_tools(
                    assistant_msg.tool_calls, tool_map, context
                )

                # Add tool results to messages
                # strict=True: _execute_tools returns exactly one result per call.
                # A mismatch would silently drop a tool result from the log.
                for tc, result in zip(assistant_msg.tool_calls, tool_results, strict=True):
                    tool_call_log.append(
                        {
                            "tool": result.tool_name,
                            "success": result.success,
                            "duration_ms": result.duration_ms,
                        }
                    )

                    # Record in tracer for observability
                    try:
                        tool_params = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, Exception):
                        tool_params = {}
                    tracer.record_tool_call(
                        iter_span,
                        tool_name=result.tool_name,
                        params=tool_params,
                        result_output=result.output[:500] if result.output else "",
                        success=result.success,
                        duration_ms=result.duration_ms or 0.0,
                    )

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": (
                                result.output if result.success else f"Error: {result.error}"
                            ),
                        }
                    )

                    if result.success:
                        context.add_evidence(result.output)

                tracer.end_iteration(iter_span, "tool_call")
            else:
                # LLM returned final answer
                tracer.end_iteration(iter_span, "final_answer")
                tracer.end_agent_run(context.tokens_used, context.iteration, len(tool_call_log))
                return AgentResult(
                    content=assistant_msg.content or "",
                    tool_calls=tool_call_log,
                    iterations=context.iteration,
                    total_tokens=context.tokens_used,
                    fallback=False,
                )

        # Max iterations reached — force final generation
        messages.append(
            {
                "role": "user",
                "content": (
                    "Please provide your best answer based on the information gathered so far."
                ),
            }
        )

        try:
            # L5a demo: the hard daily ceiling on model calls
            from callobuzz_rag.demo import budget as demo_budget

            await demo_budget.check_model_call(None, model)
            # /L5a demo
            final = await litellm.acompletion(
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
                **({"model": model} if model else {}),
            )
            if final.usage:
                context.add_tokens(final.usage.total_tokens)
            tracer.end_agent_run(context.tokens_used, context.iteration, len(tool_call_log))
            return AgentResult(
                content=final.choices[0].message.content or "",
                tool_calls=tool_call_log,
                iterations=context.iteration,
                total_tokens=context.tokens_used,
                fallback=False,
            )
        except Exception as exc:
            tracer.end_agent_run(
                context.tokens_used,
                context.iteration,
                len(tool_call_log),
                fallback=True,
            )
            return AgentResult(
                content="",
                tool_calls=tool_call_log,
                iterations=context.iteration,
                total_tokens=context.tokens_used,
                fallback=True,
                error=str(exc),
            )

    def _build_messages(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None,
        memories: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Build the initial message list for the agent."""
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        ]

        # Inject memories if available
        if memories:
            memory_text = "\n".join(f"- {m}" for m in memories)
            messages.append(
                {
                    "role": "system",
                    "content": (f"Known context from previous interactions:\n{memory_text}"),
                }
            )

        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history)

        messages.append({"role": "user", "content": query})
        return messages

    async def _execute_tools(
        self,
        tool_calls: list[Any],
        tool_map: dict[str, AgentTool],
        context: AgentRunContext,
    ) -> list[ToolResult]:
        """Execute tool calls, in parallel if multiple."""

        async def _run_one(tc: Any) -> ToolResult:
            name = tc.function.name
            tool = tool_map.get(name)
            if tool is None:
                return ToolResult(
                    tool_name=name,
                    output="",
                    success=False,
                    error=f"Unknown tool: {name}",
                )

            # Circuit breaker check
            if self._circuit_breaker and not self._circuit_breaker.is_available(name):
                return ToolResult(
                    tool_name=name,
                    output="",
                    success=False,
                    error="Tool temporarily disabled (circuit breaker)",
                )

            try:
                params = json.loads(tc.function.arguments)
            except json.JSONDecodeError as e:
                return ToolResult(
                    tool_name=name,
                    output="",
                    success=False,
                    error=f"Invalid JSON arguments: {e}",
                )

            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    tool.execute(params, context),
                    timeout=tool.timeout,
                )
                return result
            except TimeoutError:
                duration = (time.monotonic() - start) * 1000
                return ToolResult(
                    tool_name=name,
                    output="",
                    success=False,
                    duration_ms=duration,
                    error=f"Tool timed out after {tool.timeout}s",
                )
            except Exception as exc:
                duration = (time.monotonic() - start) * 1000
                logger.error("Tool %s failed: %s", name, exc)
                return ToolResult(
                    tool_name=name,
                    output="",
                    success=False,
                    duration_ms=duration,
                    error=str(exc),
                )

        results = await asyncio.gather(*[_run_one(tc) for tc in tool_calls])
        for r in results:
            context.record_tool_call(r)
            if self._circuit_breaker:
                if r.success:
                    self._circuit_breaker.record_success(r.tool_name)
                else:
                    self._circuit_breaker.record_failure(r.tool_name)
        return list(results)
