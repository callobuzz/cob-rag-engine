"""Tool registry — collects and provides agent tools based on config."""

import logging
from typing import Any

from callobuzz_rag.agent.base import AgentTool
from callobuzz_rag.agent.config import AgentConfig

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry of available agent tools."""

    def __init__(self) -> None:
        self._tools: dict[str, AgentTool] = {}

    def register(self, tool: AgentTool) -> None:
        if tool.name in self._tools:
            logger.warning("Tool %r already registered — overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Registered agent tool: %s", tool.name)

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(name)

    def get_enabled_tools(self, config: AgentConfig) -> list[AgentTool]:
        enabled = []
        for tool_name in config.enabled_tools:
            tool = self._tools.get(tool_name)
            if tool is not None:
                enabled.append(tool)
            else:
                logger.debug("Tool %r in config but not registered", tool_name)
        return enabled

    def get_llm_tool_specs(self, config: AgentConfig) -> list[dict[str, Any]]:
        return [tool.to_llm_tool_spec() for tool in self.get_enabled_tools(config)]

    @property
    def all_tool_names(self) -> list[str]:
        return list(self._tools.keys())
