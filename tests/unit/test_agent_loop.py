"""Tests for the agent loop."""

import json
import sys
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.config import AgentConfig
from callobuzz_rag.agent.context import AgentRunContext
from callobuzz_rag.agent.loop import AgentLoop, AgentResult
from callobuzz_rag.agent.registry import ToolRegistry


class TestAgentResult:
    def test_fields(self):
        result = AgentResult(
            content="The answer is 42",
            tool_calls=[],
            iterations=2,
            total_tokens=500,
            fallback=False,
        )
        assert result.content == "The answer is 42"
        assert result.iterations == 2
        assert result.total_tokens == 500
        assert result.fallback is False
        assert result.error is None

    def test_defaults(self):
        result = AgentResult(content="hello")
        assert result.tool_calls == []
        assert result.iterations == 0
        assert result.total_tokens == 0
        assert result.fallback is False
        assert result.error is None

    def test_fallback_with_error(self):
        result = AgentResult(
            content="",
            fallback=True,
            error="LLM unavailable",
        )
        assert result.fallback is True
        assert result.error == "LLM unavailable"


def _make_llm_response(
    content: str = "answer",
    tool_calls: list | None = None,
    total_tokens: int = 100,
    prompt_tokens: int = 80,
    completion_tokens: int = 20,
) -> MagicMock:
    """Build a mock litellm response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls
    resp.choices[0].finish_reason = "stop"
    resp.usage = MagicMock(
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    return resp


def _make_tool_call(
    name: str = "corpus_search",
    arguments: dict[str, Any] | None = None,
    call_id: str | None = None,
) -> MagicMock:
    """Build a mock tool call object."""
    tc = MagicMock()
    tc.id = call_id or str(uuid.uuid4())
    tc.function.name = name
    tc.function.arguments = json.dumps(arguments or {"query": "test"})
    return tc


def _make_loop(
    registry: ToolRegistry | None = None,
    max_iterations: int = 3,
) -> AgentLoop:
    config = AgentConfig(
        enabled=True,
        max_iterations=max_iterations,
        token_budget=8000,
        timeout=30.0,
    )
    return AgentLoop(
        llm_provider=None,  # Not used; we mock litellm directly
        tool_registry=registry or ToolRegistry(),
        config=config,
    )


def _mock_litellm(acompletion_fn: Any) -> MagicMock:
    """Create a mock litellm module with the given acompletion function."""
    mock = MagicMock()
    mock.acompletion = acompletion_fn
    return mock


class TestAgentLoopDirectAnswer:
    @pytest.mark.asyncio
    async def test_direct_answer_no_tool_calls(self):
        """LLM returns final answer without calling any tools."""
        loop = _make_loop()
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        mock_response = _make_llm_response(content="The answer is 42", total_tokens=100)
        mock_mod = _mock_litellm(AsyncMock(return_value=mock_response))

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("What is the meaning of life?", ctx)

        assert result.content == "The answer is 42"
        assert result.iterations == 1
        assert result.fallback is False
        assert result.total_tokens == 100
        assert result.tool_calls == []

    @pytest.mark.asyncio
    async def test_tokens_tracked(self):
        """Token usage should be tracked in context."""
        loop = _make_loop()
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        mock_response = _make_llm_response(total_tokens=250)
        mock_mod = _mock_litellm(AsyncMock(return_value=mock_response))

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            await loop.run("query", ctx)

        assert ctx.tokens_used == 250


class TestAgentLoopIterations:
    @pytest.mark.asyncio
    async def test_max_iterations_forces_answer(self):
        """Agent should stop after max_iterations and force final answer."""
        loop = _make_loop(max_iterations=1)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=1)

        # First call: LLM wants to call a tool
        tool_call = _make_tool_call(name="corpus_search")
        resp_with_tools = _make_llm_response(content="", tool_calls=[tool_call], total_tokens=50)

        # Second call (forced final): LLM gives answer
        final_resp = _make_llm_response(content="Based on my search...", total_tokens=80)

        call_count = 0

        async def mock_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp_with_tools
            return final_resp

        mock_mod = _mock_litellm(mock_acompletion)

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("query", ctx)

        assert result.content == "Based on my search..."
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_cancellation_stops_loop(self):
        """Cancelled context should stop the loop."""
        loop = _make_loop()
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=5)
        ctx.cancel()

        final_resp = _make_llm_response(content="Forced answer", total_tokens=30)
        mock_acompletion = AsyncMock(return_value=final_resp)
        mock_mod = _mock_litellm(mock_acompletion)

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("query", ctx)

        # Loop body is skipped; forced final generation fires
        mock_acompletion.assert_called_once()
        assert result.content == "Forced answer"


class TestAgentLoopToolExecution:
    @pytest.mark.asyncio
    async def test_tool_execution_and_response(self):
        """Tool results should be fed back to LLM."""
        mock_tool = MagicMock(spec=AgentTool)
        mock_tool.name = "corpus_search"
        mock_tool.description = "Search the corpus"
        mock_tool.parameters_schema = {
            "type": "object",
            "properties": {"query": {"type": "string"}},
        }
        mock_tool.timeout = 15.0
        mock_tool.to_llm_tool_spec.return_value = {
            "type": "function",
            "function": {
                "name": "corpus_search",
                "description": "Search the corpus",
                "parameters": mock_tool.parameters_schema,
            },
        }
        mock_tool.execute = AsyncMock(
            return_value=ToolResult(
                tool_name="corpus_search",
                output="Found: relevant document",
                success=True,
                duration_ms=50.0,
            )
        )

        registry = ToolRegistry()
        registry.register(mock_tool)

        config = AgentConfig(
            enabled=True,
            max_iterations=3,
            token_budget=8000,
            timeout=30.0,
            enabled_tools=["corpus_search"],
        )
        loop = AgentLoop(llm_provider=None, tool_registry=registry, config=config)
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        # First call: LLM calls tool
        tool_call = _make_tool_call(name="corpus_search")
        resp1 = _make_llm_response(content="", tool_calls=[tool_call], total_tokens=60)
        # Second call: LLM gives final answer
        resp2 = _make_llm_response(content="The document says X", total_tokens=90)

        call_count = 0

        async def mock_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp1
            return resp2

        mock_mod = _mock_litellm(mock_acompletion)

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("What does the doc say?", ctx)

        assert result.content == "The document says X"
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["tool"] == "corpus_search"
        assert result.tool_calls[0]["success"] is True
        mock_tool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        """Unknown tool name should produce error fed back to LLM."""
        loop = _make_loop()  # empty registry
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        tool_call = _make_tool_call(name="nonexistent_tool")
        resp1 = _make_llm_response(content="", tool_calls=[tool_call], total_tokens=50)
        resp2 = _make_llm_response(content="Sorry, I could not find that.", total_tokens=50)

        call_count = 0

        async def mock_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp1
            return resp2

        mock_mod = _mock_litellm(mock_acompletion)

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("query", ctx)

        assert result.tool_calls[0]["success"] is False

    @pytest.mark.asyncio
    async def test_invalid_json_tool_args(self):
        """Invalid JSON in tool arguments should produce error."""
        loop = _make_loop()
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        tool_call = MagicMock()
        tool_call.id = "tc1"
        tool_call.function.name = "corpus_search"
        tool_call.function.arguments = "not-valid-json{"

        resp1 = _make_llm_response(content="", tool_calls=[tool_call], total_tokens=50)
        resp2 = _make_llm_response(content="Fallback answer", total_tokens=50)

        call_count = 0

        async def mock_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return resp1
            return resp2

        mock_mod = _mock_litellm(mock_acompletion)

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("query", ctx)

        assert result.tool_calls[0]["success"] is False


class TestAgentLoopErrorHandling:
    @pytest.mark.asyncio
    async def test_llm_error_returns_fallback(self):
        """LLM failure should return fallback result."""
        loop = _make_loop()
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)

        mock_mod = _mock_litellm(AsyncMock(side_effect=RuntimeError("API down")))

        with patch.dict(sys.modules, {"litellm": mock_mod}):
            result = await loop.run("query", ctx)

        assert result.fallback is True
        assert result.error == "API down"
        assert result.content == ""


class TestBuildMessages:
    def test_basic_messages(self):
        loop = _make_loop()
        msgs = loop._build_messages("hello", None, None)
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "hello"

    def test_with_memories(self):
        loop = _make_loop()
        msgs = loop._build_messages("query", None, ["fact 1", "fact 2"])
        # System prompt + memory system msg + user
        assert len(msgs) == 3
        assert "fact 1" in msgs[1]["content"]
        assert "fact 2" in msgs[1]["content"]

    def test_with_conversation_history(self):
        loop = _make_loop()
        history = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        msgs = loop._build_messages("new question", history, None)
        # system + 2 history + user
        assert len(msgs) == 4
        assert msgs[1]["content"] == "prev question"
        assert msgs[-1]["content"] == "new question"
