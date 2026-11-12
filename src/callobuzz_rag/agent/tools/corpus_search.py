"""Corpus search agent tool -- wraps existing hybrid retrieval."""

import logging
import time
from typing import Any

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.context import AgentRunContext

logger = logging.getLogger(__name__)


class CorpusSearchTool(AgentTool):
    """Search the document corpus using hybrid dense+sparse retrieval."""

    def __init__(self, searcher: Any, top_k: int = 10) -> None:
        self._searcher = searcher
        self._top_k = top_k

    @property
    def name(self) -> str:
        return "corpus_search"

    @property
    def description(self) -> str:
        return (
            "Search the knowledge base for documents relevant to a query. "
            "Returns text chunks with relevance scores."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find relevant documents",
                },
                "category": {
                    "type": "string",
                    "description": "Filter by content category (product, operations, memory, customer-service)",
                },
                "user_id": {
                    "type": "string",
                    "description": "Filter by user ID (for memory scoping)",
                },
            },
            "required": ["query"],
        }

    async def execute(self, params: dict[str, Any], context: AgentRunContext) -> ToolResult:
        from callobuzz_rag.retrieval.models import SearchFilters

        query = params.get("query", "")
        if not query:
            return ToolResult(tool_name=self.name, output="", success=False, error="Empty query")

        start = time.monotonic()
        try:
            filters = SearchFilters(
                corpus_id=context.corpus_id,
                category=params.get("category"),
                user_id=params.get("user_id"),
            )
            results = await self._searcher.search(
                query=query,
                filters=filters,
                top_k_dense=self._top_k,
                top_k_sparse=self._top_k,
            )

            if not results:
                return ToolResult(
                    tool_name=self.name,
                    output="No relevant documents found.",
                    success=True,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # Format results for LLM
            formatted = []
            for i, r in enumerate(results[: self._top_k], 1):
                formatted.append(f"[{i}] (score: {r.score:.3f}) {r.text[:500]}")

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
