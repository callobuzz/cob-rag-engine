"""Streaming service — yields SSE events for conversation messages."""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from callobuzz_rag.api.stage_events import (
    STAGE_SEARCH,
    STAGE_UNDERSTAND,
    STAGE_WRITE,
    StageClock,
)
from callobuzz_rag.api.streaming import (
    shielded,
    sse_done,
    sse_error,
    sse_thinking,
    sse_token,
)
from callobuzz_rag.llm.stream_stats import collect_stream_stats

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.retrieval.search import HybridSearcher

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Answer the user's question based on the "
    "provided context. If the context does not contain enough information to "
    "answer, say so honestly. Be concise and accurate."
)


async def stream_message_response(
    conversation_id: UUID,
    corpus_id: UUID,
    content: str,
    session_factory: "Callable[[], AsyncSession]",
    llm_provider: "LLMProvider",
    make_searcher: "Callable[[AsyncSession], HybridSearcher | None] | None" = None,
    *,
    meter: "Callable[[list[LLMMessage], str], Awaitable[None]] | None" = None,
    meter_failed: "Callable[[], Awaitable[None]] | None" = None,
) -> AsyncIterator[str]:
    """Yield SSE events for a streaming conversation response.

    `meter_failed` is awaited when generation fails part-way: the model calls already made were
    paid for, so the caller records that spend (without counting an answered question).

    The stream runs in a session of its own, opened here and closed when the
    stream ends (or the client goes away): the request's session is released
    before the response starts, so no connection is held by, or torn down
    under, a finished request. `make_searcher` builds the retriever on it.

    `meter` is awaited with the prompt messages and the full reply once the
    answer has been delivered; the caller records usage from them.
    """
    session = session_factory()
    try:
        async for event in _stream(
            conversation_id,
            corpus_id,
            content,
            session,
            llm_provider,
            make_searcher(session) if make_searcher is not None else None,
            meter,
            meter_failed,
        ):
            yield event
    finally:
        # Also when the client left mid-answer: the rollback of the half-done
        # turn runs to completion instead of being cancelled with the stream.
        await shielded(session.close())


async def _stream(
    conversation_id: UUID,
    corpus_id: UUID,
    content: str,
    session: "AsyncSession",
    llm_provider: "LLMProvider",
    searcher: "HybridSearcher | None",
    meter: "Callable[[list[LLMMessage], str], Awaitable[None]] | None",
    meter_failed: "Callable[[], Awaitable[None]] | None" = None,
) -> AsyncIterator[str]:
    """The conversation stream itself, on `session`."""

    from callobuzz_rag.db.models.conversation import Conversation
    from callobuzz_rag.db.models.message import Message
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.memory.context_builder import ConversationContextBuilder

    # Verify conversation
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.corpus_id != corpus_id:
        yield sse_error("Conversation not found")
        return

    # Store user message
    user_msg = Message(conversation_id=conversation_id, role="user", content=content)
    session.add(user_msg)
    await session.flush()

    # `stage` events beside the older `thinking` ones: same shape as /query/stream, so a
    # client shows one live clock and stage label for every answering surface.
    clock = StageClock()
    stage_event = clock.enter(STAGE_UNDERSTAND)
    if stage_event is not None:
        yield stage_event
    yield sse_thinking("Analyzing query...")

    # Load conversation history
    context_builder = ConversationContextBuilder(session, max_messages=10, token_budget=2000)
    history_dicts = await context_builder.build_context(conversation_id)

    # Retrieve context
    context_text = ""
    search_ms = 0
    if searcher is not None:
        stage_event = clock.enter(STAGE_SEARCH)
        if stage_event is not None:
            yield stage_event
        yield sse_thinking("Searching knowledge base...")
        search_start = time.monotonic()
        try:
            from callobuzz_rag.retrieval.models import SearchFilters

            filters = SearchFilters(corpus_id=str(corpus_id))
            results = await searcher.search(content, filters, top_k_dense=5)
            if results:
                parts: list[str] = []
                for i, r in enumerate(results, 1):
                    parts.append(f"[{i}] {r.text}")
                context_text = "\n\n".join(parts)
        except Exception as e:
            logger.warning("Retrieval failed during streaming: %s", e)
        search_ms = round((time.monotonic() - search_start) * 1000)

    # Build LLM messages
    system_content = _SYSTEM_PROMPT
    if context_text:
        system_content += "\n\nRelevant context from the knowledge base:\n" + context_text

    llm_messages: list[LLMMessage] = [
        LLMMessage(role="system", content=system_content),
    ]

    # Add conversation history (skip last — it's the message we just added)
    for msg_dict in history_dicts[:-1]:
        llm_messages.append(LLMMessage(role=msg_dict["role"], content=msg_dict["content"]))

    llm_messages.append(LLMMessage(role="user", content=content))

    # Stream LLM response
    understand_ms = clock.elapsed_ms() - search_ms
    stage_event = clock.enter(STAGE_WRITE)
    if stage_event is not None:
        yield stage_event
    yield sse_thinking("Generating response...")
    full_content = ""
    write_start = time.monotonic()

    try:
        with collect_stream_stats() as stream_stats:
            async for delta in llm_provider.complete_stream(llm_messages):
                full_content += delta
                yield sse_token(delta)
    except Exception as e:
        await session.rollback()
        if meter_failed is not None:
            await shielded(meter_failed())
        yield sse_error(str(e))
        return

    # Store assistant message
    estimated_tokens = len(full_content) // 4
    assistant_msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=full_content,
        token_count=estimated_tokens,
    )
    session.add(assistant_msg)
    conv.message_count = (conv.message_count or 0) + 2
    conv.total_tokens = (conv.total_tokens or 0) + estimated_tokens
    # Saved and metered before `done`, shielded: a client that hangs up on
    # `done` must not cut a write off and leave a broken connection behind.
    await shielded(session.commit())
    await shielded(session.refresh(assistant_msg))
    if meter is not None:
        await shielded(meter(llm_messages, full_content))

    timings: dict[str, int] = {STAGE_UNDERSTAND: max(0, understand_ms)}
    if searcher is not None:
        timings[STAGE_SEARCH] = search_ms
    timings[STAGE_WRITE] = round((time.monotonic() - write_start) * 1000)
    extra: dict[str, Any] = {"timings": timings, "latency_ms": clock.elapsed_ms()}
    if stream_stats.thinking_ms is not None:
        extra["thinking_ms"] = stream_stats.thinking_ms
    if stream_stats.reasoning_tokens > 0:
        extra["reasoning_tokens"] = stream_stats.reasoning_tokens
    yield sse_done(
        message_id=str(assistant_msg.id),
        total_tokens=estimated_tokens,
        **extra,
    )
