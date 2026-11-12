"""Document fetch agent tool — retrieve full document text by ID."""

import logging
import time
from typing import Any
from uuid import UUID

from callobuzz_rag.agent.base import AgentTool, ToolResult
from callobuzz_rag.agent.context import AgentRunContext

logger = logging.getLogger(__name__)


class DocumentFetchTool(AgentTool):
    """Fetch the full text of a specific document by its ID."""

    def __init__(self, session: Any) -> None:
        self._session = session

    @property
    def name(self) -> str:
        return "document_fetch"

    @property
    def description(self) -> str:
        return (
            "Retrieve the full text of a specific document version by its ID. "
            "Use when you need more detail from a document found via search."
        )

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "document_version_id": {
                    "type": "string",
                    "description": "UUID of the document version to fetch chunks for",
                },
            },
            "required": ["document_version_id"],
        }

    @property
    def max_output_tokens(self) -> int:
        return 4000

    async def execute(self, params: dict[str, Any], context: AgentRunContext) -> ToolResult:
        from sqlalchemy import select

        from callobuzz_rag.db.models.chunk import Chunk

        doc_version_id = params.get("document_version_id", "")
        if not doc_version_id:
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                error="Missing document_version_id",
            )

        start = time.monotonic()
        try:
            doc_uuid = UUID(doc_version_id)
        except ValueError:
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                error="Invalid document_version_id format",
            )

        try:
            corpus_uuid = UUID(context.corpus_id)
        except (ValueError, AttributeError):
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                error="Invalid corpus_id in context",
            )

        try:
            result = await self._session.execute(
                select(Chunk)
                .where(
                    Chunk.document_version_id == doc_uuid,
                    Chunk.corpus_id == corpus_uuid,
                )
                .order_by(Chunk.chunk_index)
            )
            chunks = result.scalars().all()

            if not chunks:
                return ToolResult(
                    tool_name=self.name,
                    output="Document not found or has no chunks.",
                    success=True,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            text = "\n\n".join(c.text for c in chunks if c.text)
            if len(text) > self.max_output_tokens * 4:
                text = text[: self.max_output_tokens * 4] + "\n\n[Document truncated]"

            return ToolResult(
                tool_name=self.name,
                output=text,
                success=True,
                duration_ms=(time.monotonic() - start) * 1000,
                metadata={"chunk_count": len(chunks)},
            )
        except Exception as exc:
            return ToolResult(
                tool_name=self.name,
                output="",
                success=False,
                duration_ms=(time.monotonic() - start) * 1000,
                error=str(exc),
            )
