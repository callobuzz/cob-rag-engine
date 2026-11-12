"""Tests for agent tool abstraction layer."""

from unittest.mock import MagicMock

import pytest

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.config import AgentConfig, resolve_agent_config
from callobuzz_rag.agent.context import AgentRunContext
from callobuzz_rag.agent.registry import ToolRegistry


class TestToolResult:
    def test_fields(self):
        result = ToolResult(
            tool_name="corpus_search",
            output="Found 5 chunks",
            success=True,
            duration_ms=450,
        )
        assert result.tool_name == "corpus_search"
        assert result.output == "Found 5 chunks"
        assert result.success is True
        assert result.duration_ms == 450

    def test_error_result(self):
        result = ToolResult(
            tool_name="web_search",
            output="",
            success=False,
            error="API timeout",
        )
        assert result.success is False
        assert result.error == "API timeout"

    def test_frozen(self):
        result = ToolResult(tool_name="x", output="y", success=True)
        with pytest.raises(AttributeError):
            result.output = "z"  # type: ignore[misc]


class TestAgentToolABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            AgentTool()  # type: ignore[abstract]

    def test_has_execute_method(self):
        assert hasattr(AgentTool, "execute")

    def test_has_required_properties(self):
        assert hasattr(AgentTool, "name")
        assert hasattr(AgentTool, "description")
        assert hasattr(AgentTool, "parameters_schema")

    def test_to_llm_tool_spec(self):
        class _StubTool(AgentTool):
            @property
            def name(self) -> str:
                return "stub"

            @property
            def description(self) -> str:
                return "A stub tool"

            @property
            def parameters_schema(self):
                return {"type": "object", "properties": {}}

            async def execute(self, params, context):
                return ToolResult(tool_name="stub", output="ok", success=True)

        tool = _StubTool()
        spec = tool.to_llm_tool_spec()
        assert spec["type"] == "function"
        assert spec["function"]["name"] == "stub"
        assert spec["function"]["description"] == "A stub tool"


class TestAgentRunContext:
    def test_create_context(self):
        ctx = AgentRunContext(
            corpus_id="test-corpus",
            token_budget=8000,
            max_iterations=5,
            timeout=30.0,
        )
        assert ctx.corpus_id == "test-corpus"
        assert ctx.token_budget == 8000
        assert ctx.tokens_used == 0
        assert ctx.iteration == 0
        assert not ctx.is_cancelled()

    def test_add_tokens(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=100)
        ctx.add_tokens(50)
        assert ctx.tokens_used == 50
        assert not ctx.budget_exceeded()
        ctx.add_tokens(60)
        assert ctx.budget_exceeded()

    def test_record_tool_call(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        result = ToolResult(tool_name="search", output="data", success=True, duration_ms=100)
        ctx.record_tool_call(result)
        assert len(ctx.tool_history) == 1
        assert ctx.tool_history[0].tool_name == "search"

    def test_cancel(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        assert not ctx.is_cancelled()
        ctx.cancel()
        assert ctx.is_cancelled()

    def test_increment_iteration(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=8000, max_iterations=3)
        ctx.increment_iteration()
        assert ctx.iteration == 1
        assert not ctx.iterations_exceeded()
        ctx.increment_iteration()
        ctx.increment_iteration()
        assert ctx.iterations_exceeded()

    def test_should_stop_when_cancelled(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        assert not ctx.should_stop()
        ctx.cancel()
        assert ctx.should_stop()

    def test_add_evidence(self):
        ctx = AgentRunContext(corpus_id="test", token_budget=8000)
        ctx.add_evidence("chunk text")
        assert ctx.evidence == ["chunk text"]


class TestAgentConfig:
    def test_defaults(self):
        cfg = AgentConfig()
        assert cfg.enabled is False
        assert cfg.max_iterations == 5
        assert cfg.token_budget == 8000
        assert cfg.timeout == 30.0
        assert "corpus_search" in cfg.enabled_tools

    def test_custom_config(self):
        cfg = AgentConfig(
            enabled=True,
            max_iterations=10,
            enabled_tools=["corpus_search", "web_search"],
        )
        assert cfg.enabled is True
        assert cfg.max_iterations == 10
        assert "web_search" in cfg.enabled_tools


class TestResolveAgentConfig:
    def test_from_settings_defaults(self):
        settings = MagicMock()
        settings.enable_agentic_mode = False
        settings.agent_max_iterations = 5
        settings.agent_token_budget = 8000
        settings.agent_timeout = 30
        settings.agent_model = ""
        settings.agent_enabled_tools = "corpus_search,document_fetch"

        config = resolve_agent_config(settings)
        assert config.enabled is False
        assert config.max_iterations == 5
        assert "corpus_search" in config.enabled_tools
        assert "document_fetch" in config.enabled_tools

    def test_corpus_override(self):
        settings = MagicMock()
        settings.enable_agentic_mode = False
        settings.agent_max_iterations = 5
        settings.agent_token_budget = 8000
        settings.agent_timeout = 30
        settings.agent_model = ""
        settings.agent_enabled_tools = "corpus_search"

        corpus_config = {
            "agentic_mode": True,
            "agent_max_iterations": 10,
            "enabled_tools": ["corpus_search", "web_search"],
        }
        config = resolve_agent_config(settings, corpus_config)
        assert config.enabled is True
        assert config.max_iterations == 10
        assert "web_search" in config.enabled_tools

    def test_none_corpus_config(self):
        settings = MagicMock()
        settings.enable_agentic_mode = True
        settings.agent_max_iterations = 3
        settings.agent_token_budget = 4000
        settings.agent_timeout = 15
        settings.agent_model = "gpt-4o"
        settings.agent_enabled_tools = "corpus_search"

        config = resolve_agent_config(settings, None)
        assert config.enabled is True
        assert config.max_iterations == 3
        assert config.model == "gpt-4o"


class TestToolRegistry:
    def _make_mock_tool(self, name: str) -> AgentTool:
        tool = MagicMock(spec=AgentTool)
        tool.name = name
        tool.description = f"Mock {name} tool"
        tool.parameters_schema = {"type": "object", "properties": {}}
        tool.to_llm_tool_spec.return_value = {
            "type": "function",
            "function": {"name": name, "description": f"Mock {name}", "parameters": {}},
        }
        return tool

    def test_register_and_get(self):
        registry = ToolRegistry()
        tool = self._make_mock_tool("corpus_search")
        registry.register(tool)
        assert registry.get("corpus_search") is tool

    def test_get_unknown_returns_none(self):
        registry = ToolRegistry()
        assert registry.get("nonexistent") is None

    def test_get_enabled_tools(self):
        registry = ToolRegistry()
        registry.register(self._make_mock_tool("corpus_search"))
        registry.register(self._make_mock_tool("web_search"))
        registry.register(self._make_mock_tool("document_fetch"))

        config = AgentConfig(enabled_tools=["corpus_search", "web_search"])
        enabled = registry.get_enabled_tools(config)
        assert len(enabled) == 2
        names = [t.name for t in enabled]
        assert "corpus_search" in names
        assert "web_search" in names

    def test_get_llm_tool_specs(self):
        registry = ToolRegistry()
        registry.register(self._make_mock_tool("corpus_search"))

        config = AgentConfig(enabled_tools=["corpus_search"])
        specs = registry.get_llm_tool_specs(config)
        assert len(specs) == 1
        assert specs[0]["function"]["name"] == "corpus_search"

    def test_all_tool_names(self):
        registry = ToolRegistry()
        registry.register(self._make_mock_tool("a"))
        registry.register(self._make_mock_tool("b"))
        assert sorted(registry.all_tool_names) == ["a", "b"]
