"""Web search agent tool -- external web search via provider."""

import logging
import time
from typing import Any

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.context import AgentRunContext

logger = logging.getLogger(__name__)


class WebSearchTool(AgentTool):
    """Search the web for current information not in the knowledge base."""

    def __init__(self, provider: Any, max_results: int = 5) -> None:
        self._provider = provider
        self._max_results = max_results

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web for information not found in the knowledge base. "
            "Use when corpus search doesn't have the answer or for current events."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The web search query",
                },
            },
            "required": ["query"],
        }

    @property
    def timeout(self) -> float:
        return 10.0  # Web APIs can be slow

    async def execute(self, params: dict[str, Any], context: AgentRunContext) -> ToolResult:
        query = params.get("query", "")
        if not query:
            return ToolResult(tool_name=self.name, output="", success=False, error="Empty query")

        start = time.monotonic()
        try:
            results = await self._provider.search(query, max_results=self._max_results)

            if not results:
                return ToolResult(
                    tool_name=self.name,
                    output="No web results found.",
                    success=True,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            formatted = []
            for r in results:
                formatted.append(f"**{r.title}**\n{r.snippet}\nSource: {r.url}")

            output = "\n\n".join(formatted)
            return ToolResult(
                tool_name=self.name,
                output=output,
                success=True,
                duration_ms=(time.monotonic() - start) * 1000,
                metadata={"result_count": len(results)},
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
