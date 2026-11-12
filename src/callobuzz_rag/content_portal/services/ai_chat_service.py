"""AI Chat Service — interactive refinement for Content Portal workspaces.

Provides conversational AI that understands workspace content, suggests
chunk operations (split, merge, rewrite), and can search the existing corpus.
"""

from __future__ import annotations

import json
import logging
import re
import uuid as uuid_mod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from callobuzz_rag.content_portal.services.chunk_editor import _make_history_entry
from callobuzz_rag.core.exceptions import ContentPortalError
from callobuzz_rag.llm.models import LLMMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CONTEXT_CHUNKS = 50
MAX_CHUNK_PREVIEW_CHARS = 500
DEFAULT_CHAT_TEMPERATURE = 0.3
DEFAULT_CHAT_MAX_TOKENS = 4000

TOOL_CALL_MARKER = "```tool_call"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AIChatError(ContentPortalError):
    """Base exception for AI chat operations."""


class ConversationNotFoundError(AIChatError):
    """Conversation not found for workspace."""


class ActionExecutionError(AIChatError):
    """Tool call action execution failed."""


class ParentChunkActionError(ActionExecutionError):
    """In a post-ingest review, a parent chunk's copy cannot be split, merged or
    rewritten: its text is re-derived from its children at finalize."""

    code = "parent_chunk_derived"


async def _refuse_parent_copies(
    session: AsyncSession, workspace_id: UUID, chunks: list[Any]
) -> None:
    from callobuzz_rag.content_portal.services.post_ingest_review import (
        PARENT_CHUNK_DERIVED_MESSAGE,
        engine_chunk_id_in,
        review_parent_ids,
    )

    parents = await review_parent_ids(session, workspace_id)
    if parents and any(engine_chunk_id_in(c.metadata_) in parents for c in chunks):
        raise ParentChunkActionError(PARENT_CHUNK_DERIVED_MESSAGE)


class ChunkNotInWorkspaceError(ActionExecutionError):
    """The action names a chunk that is not in this workspace (another's, or none)."""


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

CHAT_SYSTEM_PROMPT = """You are a Content Portal AI assistant helping users \
refine and organize knowledge base content.

{workspace_context}

You can suggest the following actions using tool_call blocks:

1. **split_chunk** — Split a chunk into smaller pieces.
   Arguments: chunk_id (UUID), split_points (list of character offsets)

2. **merge_chunks** — Merge two or more chunks into one.
   Arguments: chunk_ids (list of UUIDs), separator (optional string)

3. **rewrite_chunk** — Rewrite a chunk with improved text.
   Arguments: chunk_id (UUID), new_text (string)

4. **search_corpus** — Search the existing corpus for related content.
   Arguments: query (string), top_k (optional integer, default 5)

When suggesting an action, output it inside a fenced block like this:
```tool_call
{{"tool_name": "split_chunk", "arguments": {{"chunk_id": "...", "split_points": [100]}}}}
```

Only suggest actions when the user asks or it clearly improves quality.
Be concise and helpful. Reference chunk indices (e.g. "Chunk #3") when discussing content."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_workspace_context(
    chunks: list[Any],
    title: str,
) -> str:
    """Build a workspace context string for the system prompt."""
    if not chunks:
        return f"Workspace: {title}\n\nNo chunks available yet."

    lines = [f"Workspace: {title}", f"Total chunks: {len(chunks)}", ""]
    for i, chunk in enumerate(chunks[:MAX_CONTEXT_CHUNKS]):
        text = chunk.text
        if len(text) > MAX_CHUNK_PREVIEW_CHARS:
            text = text[:MAX_CHUNK_PREVIEW_CHARS] + "..."
        confidence = ""
        if hasattr(chunk, "confidence_score") and chunk.confidence_score is not None:
            confidence = f" (confidence: {chunk.confidence_score:.2f})"
        lines.append(f"Chunk #{i}{confidence}: {text}")

    if len(chunks) > MAX_CONTEXT_CHUNKS:
        lines.append(f"\n... and {len(chunks) - MAX_CONTEXT_CHUNKS} more chunks")

    return "\n".join(lines)


def _parse_tool_calls(content: str) -> list[dict[str, Any]]:
    """Extract tool_call blocks from AI response content.

    Looks for fenced blocks like:
    ```tool_call
    {"tool_name": "split_chunk", "arguments": {...}}
    ```
    """
    pattern = re.compile(
        r"```tool_call\s*\n(.*?)\n\s*```",
        re.DOTALL,
    )
    matches = pattern.findall(content)
    results: list[dict[str, Any]] = []
    for match in matches:
        try:
            parsed = json.loads(match.strip())
        except (json.JSONDecodeError, ValueError):
            logger.warning("Malformed tool_call JSON: %s", match[:200])
            continue
        if not isinstance(parsed, dict) or "tool_name" not in parsed:
            logger.warning("tool_call missing tool_name: %s", match[:200])
            continue
        results.append(parsed)
    return results


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class AIChatService:
    """Stateless service for AI-powered chat within workspaces."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    # -- internal helpers ---------------------------------------------------

    async def _get_workspace(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> Any:
        """Load workspace or raise AIChatError."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.workspace import Workspace

        result = await session.execute(select(Workspace).where(Workspace.id == workspace_id))
        workspace = result.scalar_one_or_none()
        if workspace is None:
            raise AIChatError(f"Workspace {workspace_id} not found")
        return workspace

    async def _get_workspace_chunks(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> list[Any]:
        """Load all staged chunks for a workspace."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        result = await session.execute(
            select(StagedChunk)
            .where(StagedChunk.workspace_id == workspace_id)
            .order_by(StagedChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def _get_or_create_conversation(
        self,
        session: AsyncSession,
        workspace_id: UUID,
    ) -> Any:
        """Get the latest conversation for a workspace, or create one."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.workspace_conversation import (
            WorkspaceConversation,
        )

        result = await session.execute(
            select(WorkspaceConversation)
            .where(WorkspaceConversation.workspace_id == workspace_id)
            .order_by(WorkspaceConversation.created_at.desc())
            .limit(1)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            conversation = WorkspaceConversation(workspace_id=workspace_id)
            session.add(conversation)
            await session.flush()
        return conversation

    async def _save_message(
        self,
        session: AsyncSession,
        conversation_id: UUID,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        token_usage: dict[str, Any] | None = None,
    ) -> Any:
        """Persist a chat message to the database."""
        from callobuzz_rag.content_portal.models.workspace_conversation import (
            WorkspaceMessage,
        )

        message = WorkspaceMessage(
            conversation_id=conversation_id,
            role=role,
            content=content,
            tool_calls=tool_calls,
            token_usage=token_usage,
        )
        session.add(message)
        await session.flush()
        return message

    async def _build_messages(
        self,
        session: AsyncSession,
        conversation: Any,
        workspace: Any,
        user_message: str,
    ) -> list[LLMMessage]:
        """Assemble the LLM message list from conversation history."""
        chunks = await self._get_workspace_chunks(session, workspace.id)
        context = _build_workspace_context(chunks, workspace.title)
        system_prompt = CHAT_SYSTEM_PROMPT.format(workspace_context=context)

        messages: list[LLMMessage] = [LLMMessage(role="system", content=system_prompt)]

        # Add conversation history
        if hasattr(conversation, "messages"):
            for msg in conversation.messages:
                messages.append(LLMMessage(role=msg.role, content=msg.content))

        # Add the new user message
        messages.append(LLMMessage(role="user", content=user_message))
        return messages

    # -- public API ---------------------------------------------------------

    async def send_message(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        message: str,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Send a message and get a non-streaming response."""
        workspace = await self._get_workspace(session, workspace_id)
        conversation = await self._get_or_create_conversation(session, workspace_id)

        # Save user message
        await self._save_message(session, conversation.id, "user", message)

        # Build message list and call LLM
        llm_messages = await self._build_messages(session, conversation, workspace, message)
        try:
            response = await self._llm.complete(
                llm_messages,
                model=model,
                temperature=DEFAULT_CHAT_TEMPERATURE,
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
            )
        except Exception as exc:
            raise AIChatError(f"LLM completion failed: {exc}") from exc

        # Parse tool calls from response
        tool_calls = _parse_tool_calls(response.content)
        token_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

        # Save assistant message
        assistant_msg = await self._save_message(
            session,
            conversation.id,
            "assistant",
            response.content,
            tool_calls=tool_calls or None,
            token_usage=token_usage,
        )

        return {
            "message_id": assistant_msg.id,
            "conversation_id": conversation.id,
            "role": "assistant",
            "content": response.content,
            "tool_calls": tool_calls or None,
            "token_usage": token_usage,
            "created_at": assistant_msg.created_at,
        }

    async def send_message_stream(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        message: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        """Send a message and stream SSE response chunks."""
        workspace = await self._get_workspace(session, workspace_id)
        conversation = await self._get_or_create_conversation(session, workspace_id)

        # Save user message
        await self._save_message(session, conversation.id, "user", message)

        # Build message list and stream
        llm_messages = await self._build_messages(session, conversation, workspace, message)
        collected: list[str] = []
        try:
            async for chunk in self._llm.complete_stream(
                llm_messages,
                model=model,
                temperature=DEFAULT_CHAT_TEMPERATURE,
                max_tokens=DEFAULT_CHAT_MAX_TOKENS,
            ):
                collected.append(chunk)
                yield f"data: {json.dumps({'content': chunk})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            return

        # Save the full response
        full_content = "".join(collected)
        tool_calls = _parse_tool_calls(full_content)
        await self._save_message(
            session,
            conversation.id,
            "assistant",
            full_content,
            tool_calls=tool_calls or None,
        )
        yield "data: [DONE]\n\n"

    async def get_history(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Get paginated chat history for a workspace."""
        from sqlalchemy import func, select

        from callobuzz_rag.content_portal.models.workspace_conversation import (
            WorkspaceConversation,
            WorkspaceMessage,
        )

        # Find conversation
        conv_result = await session.execute(
            select(WorkspaceConversation)
            .where(WorkspaceConversation.workspace_id == workspace_id)
            .order_by(WorkspaceConversation.created_at.desc())
            .limit(1)
        )
        conversation = conv_result.scalar_one_or_none()

        if conversation is None:
            return {
                "workspace_id": workspace_id,
                "conversation_id": None,
                "messages": [],
                "total": 0,
            }

        # Count total messages
        count_result = await session.execute(
            select(func.count())
            .select_from(WorkspaceMessage)
            .where(WorkspaceMessage.conversation_id == conversation.id)
        )
        total = count_result.scalar() or 0

        # Fetch messages
        msg_result = await session.execute(
            select(WorkspaceMessage)
            .where(WorkspaceMessage.conversation_id == conversation.id)
            .order_by(WorkspaceMessage.created_at)
            .offset(offset)
            .limit(limit)
        )
        messages = msg_result.scalars().all()

        return {
            "workspace_id": workspace_id,
            "conversation_id": conversation.id,
            "messages": [
                {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "token_usage": m.token_usage,
                    "created_at": m.created_at,
                }
                for m in messages
            ],
            "total": total,
        }

    async def execute_action(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        *,
        user: str,
        tool_name: str,
        arguments: dict[str, Any],
        corpus_id: UUID | None = None,
        query_orchestrator: QueryOrchestrator | None = None,
    ) -> dict[str, Any]:
        """Execute a tool call action on the workspace."""
        if tool_name == "split_chunk":
            return await self._execute_split(session, workspace_id, user, arguments)
        if tool_name == "merge_chunks":
            return await self._execute_merge(session, workspace_id, user, arguments)
        if tool_name == "rewrite_chunk":
            return await self._execute_rewrite(session, workspace_id, user, arguments)
        if tool_name == "search_corpus":
            return await self._execute_search(arguments, corpus_id, query_orchestrator)
        raise ActionExecutionError(f"Unknown action: {tool_name}")

    # -- private action executors -------------------------------------------

    async def _execute_split(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        user: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Split a chunk at specified character offsets."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        chunk_id = arguments.get("chunk_id")
        split_points = arguments.get("split_points", [])
        if not chunk_id:
            raise ActionExecutionError("chunk_id is required for split_chunk")
        if not split_points:
            raise ActionExecutionError("split_points is required for split_chunk")

        result = await session.execute(
            select(StagedChunk).where(
                StagedChunk.id == uuid_mod.UUID(str(chunk_id)),
                StagedChunk.workspace_id == workspace_id,
            )
        )
        chunk = result.scalar_one_or_none()
        if chunk is None:
            raise ChunkNotInWorkspaceError(f"Chunk {chunk_id} not found in workspace")
        await _refuse_parent_copies(session, workspace_id, [chunk])

        # Split text at the specified points
        text = chunk.text
        points = sorted(set(split_points))
        parts: list[str] = []
        prev = 0
        for pt in points:
            if 0 < pt < len(text):
                parts.append(text[prev:pt].strip())
                prev = pt
        parts.append(text[prev:].strip())
        parts = [p for p in parts if p]  # remove empty

        if len(parts) < 2:
            raise ActionExecutionError("Split points did not produce multiple chunks")

        # Replace original with parts
        new_ids: list[str] = []
        for i, part in enumerate(parts):
            new_chunk = StagedChunk(
                workspace_id=workspace_id,
                staged_document_id=chunk.staged_document_id,
                text=part,
                chunk_index=chunk.chunk_index + i,
                source="manual",
                edit_history=[
                    _make_history_entry(
                        user, "split_from", details={"source_chunk_id": str(chunk.id)}
                    )
                ],
            )
            session.add(new_chunk)
            await session.flush()
            new_ids.append(str(new_chunk.id))

        await session.delete(chunk)
        await session.flush()

        return {
            "action": "split_chunk",
            "success": True,
            "message": f"Split chunk into {len(parts)} parts",
            "result": {
                "original_chunk_id": str(chunk_id),
                "new_chunk_ids": new_ids,
            },
        }

    async def _execute_merge(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        user: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge multiple chunks into one."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        chunk_ids = arguments.get("chunk_ids", [])
        separator = arguments.get("separator", "\n\n")
        if len(chunk_ids) < 2:
            raise ActionExecutionError("merge_chunks requires at least 2 chunk_ids")

        chunks = []
        for cid in chunk_ids:
            result = await session.execute(
                select(StagedChunk).where(
                    StagedChunk.id == uuid_mod.UUID(str(cid)),
                    StagedChunk.workspace_id == workspace_id,
                )
            )
            chunk = result.scalar_one_or_none()
            if chunk is None:
                raise ChunkNotInWorkspaceError(f"Chunk {cid} not found in workspace")
            chunks.append(chunk)
        await _refuse_parent_copies(session, workspace_id, chunks)

        # Merge texts
        merged_text = separator.join(c.text for c in chunks)
        min_index = min(c.chunk_index for c in chunks)

        merged_chunk = StagedChunk(
            workspace_id=workspace_id,
            staged_document_id=chunks[0].staged_document_id,
            text=merged_text,
            chunk_index=min_index,
            source="manual",
            edit_history=[
                _make_history_entry(
                    user,
                    "merged_from",
                    details={"source_chunk_ids": [str(c.id) for c in chunks]},
                )
            ],
        )
        session.add(merged_chunk)
        await session.flush()

        for c in chunks:
            await session.delete(c)
        await session.flush()

        return {
            "action": "merge_chunks",
            "success": True,
            "message": f"Merged {len(chunks)} chunks",
            "result": {
                "merged_chunk_id": str(merged_chunk.id),
                "source_chunk_ids": [str(c) for c in chunk_ids],
            },
        }

    async def _execute_rewrite(
        self,
        session: AsyncSession,
        workspace_id: UUID,
        user: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Rewrite a chunk with new text."""
        from sqlalchemy import select

        from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

        chunk_id = arguments.get("chunk_id")
        new_text = arguments.get("new_text")
        if not chunk_id:
            raise ActionExecutionError("chunk_id is required for rewrite_chunk")
        if not new_text:
            raise ActionExecutionError("new_text is required for rewrite_chunk")

        result = await session.execute(
            select(StagedChunk).where(
                StagedChunk.id == uuid_mod.UUID(str(chunk_id)),
                StagedChunk.workspace_id == workspace_id,
            )
        )
        chunk = result.scalar_one_or_none()
        if chunk is None:
            raise ChunkNotInWorkspaceError(f"Chunk {chunk_id} not found in workspace")
        await _refuse_parent_copies(session, workspace_id, [chunk])

        old_text = chunk.text
        chunk.text = new_text

        history = list(chunk.edit_history or [])
        history.append(_make_history_entry(user, "rewrite", old_text=old_text))
        chunk.edit_history = history
        await session.flush()

        return {
            "action": "rewrite_chunk",
            "success": True,
            "message": "Chunk rewritten successfully",
            "result": {
                "chunk_id": str(chunk_id),
                "old_length": len(old_text),
                "new_length": len(new_text),
            },
        }

    async def _execute_search(
        self,
        arguments: dict[str, Any],
        corpus_id: UUID | None,
        query_orchestrator: QueryOrchestrator | None,
    ) -> dict[str, Any]:
        """Search the existing corpus for related content."""
        query = arguments.get("query")
        if not query:
            raise ActionExecutionError("query is required for search_corpus")
        top_k = arguments.get("top_k", 5)

        if query_orchestrator is None or corpus_id is None:
            return {
                "action": "search_corpus",
                "success": False,
                "message": "Corpus search not available (no orchestrator configured)",
                "result": None,
            }

        try:
            # QueryOrchestrator exposes no `search`; the retrieval-only entry
            # point is run_retrieval_pipeline. Its `assembled.cited_chunks` carry
            # chunk_id/text/score, whereas `top_results` only carries id/score.
            pipeline_result = await query_orchestrator.run_retrieval_pipeline(
                text=query,
                corpus_id=corpus_id,
                top_k=int(top_k),
            )
            results = pipeline_result.assembled.cited_chunks
            return {
                "action": "search_corpus",
                "success": True,
                "message": f"Found {len(results)} results",
                "result": {
                    "query": query,
                    "results": [
                        {
                            "chunk_id": str(r.chunk_id),
                            "text": r.llm_text[:MAX_CHUNK_PREVIEW_CHARS],
                            "score": r.score,
                        }
                        for r in results
                    ],
                },
            }
        except Exception as exc:
            raise ActionExecutionError(f"Corpus search failed: {exc}") from exc
