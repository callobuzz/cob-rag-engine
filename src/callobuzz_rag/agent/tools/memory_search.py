"""Memory search agent tool — search extracted memories from past conversations."""

import logging
import time
from typing import Any

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.context import AgentRunContext

logger = logging.getLogger(__name__)


class MemorySearchTool(AgentTool):
    """Search memories extracted from previous conversations."""

    def __init__(self, memory_service: Any) -> None:
        self._memory_service = memory_service

    @property
    def name(self) -> str:
        return "memory_search"

    @property
    def description(self) -> str:
        return (
            "Search memories from previous conversations. Use to recall user "
            "preferences, past decisions, or previously discussed topics."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memories",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: AgentRunContext) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult(tool_name=self.name, output="", success=False, error="Empty query")

        start = time.monotonic()
        try:
            memories = await self._memory_service.search_memories(
                corpus_id=context.corpus_id,
                query=query,
                top_k=5,
            )

            if not memories:
                return ToolResult(
                    tool_name=self.name,
                    output="No relevant memories found.",
                    success=True,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            formatted = []
            for m in memories:
                formatted.append(f"[{m.memory_type}] {m.content}")

            output = "\n".join(formatted)
            return ToolResult(
                tool_name=self.name,
                output=output,
                success=True,
                duration_ms=(time.monotonic() - start) * 1000,
                metadata={"memory_count": len(memories)},
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
