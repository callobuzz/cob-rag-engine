"""Unit tests for ConversationContextBuilder and auto-title generation."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.llm.models import LLMResponse, LLMUsage
from callobuzz_rag.memory.auto_title import generate_title
from callobuzz_rag.memory.context_builder import ConversationContextBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(role: str, content: str, token_count: int = 0):
    """Create a mock Message object."""
    msg = MagicMock()
    msg.role = role
    msg.content = content
    msg.token_count = token_count
    msg.created_at = datetime.now(UTC)
    return msg


def _make_conversation(conv_id=None, message_count=0, summary=None, title=None):
    """Create a mock Conversation object."""
    conv = MagicMock()
    conv.id = conv_id or uuid.uuid4()
    conv.message_count = message_count
    conv.summary = summary
    conv.title = title
    return conv


# ---------------------------------------------------------------------------
# ConversationContextBuilder: empty conversation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_empty_conversation():
    """Empty conversation should return empty context list."""
    session = AsyncMock()

    # Conversation exists but no summary
    conv = _make_conversation(message_count=0)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    # No messages
    msg_scalars = MagicMock()
    msg_scalars.all.return_value = []
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    session.execute = AsyncMock(side_effect=[conv_result, msg_result])

    builder = ConversationContextBuilder(session, max_messages=10, token_budget=2000)
    context = await builder.build_context(uuid.uuid4())

    assert context == []


# ---------------------------------------------------------------------------
# ConversationContextBuilder: context with messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_with_messages():
    """Should return recent messages as context dicts."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(conv_id, message_count=4)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    messages = [
        _make_message("user", "Hello"),
        _make_message("assistant", "Hi there!"),
        _make_message("user", "What is RAG?"),
        _make_message("assistant", "RAG stands for..."),
    ]
    # DB returns desc order, builder reverses
    msg_scalars = MagicMock()
    msg_scalars.all.return_value = list(reversed(messages))
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    session.execute = AsyncMock(side_effect=[conv_result, msg_result])

    builder = ConversationContextBuilder(session, max_messages=10, token_budget=2000)
    context = await builder.build_context(conv_id)

    assert len(context) == 4
    assert context[0] == {"role": "user", "content": "Hello"}
    assert context[1] == {"role": "assistant", "content": "Hi there!"}
    assert context[2] == {"role": "user", "content": "What is RAG?"}
    assert context[3] == {"role": "assistant", "content": "RAG stands for..."}


# ---------------------------------------------------------------------------
# ConversationContextBuilder: context with summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_with_summary():
    """Should prepend summary when message_count > max_messages."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(
        conv_id,
        message_count=20,  # exceeds max_messages=10
        summary="User discussed RAG architecture and embeddings.",
    )
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    messages = [
        _make_message("user", "Tell me more about chunking"),
        _make_message("assistant", "Chunking is the process of..."),
    ]
    msg_scalars = MagicMock()
    msg_scalars.all.return_value = list(reversed(messages))
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    session.execute = AsyncMock(side_effect=[conv_result, msg_result])

    builder = ConversationContextBuilder(session, max_messages=10, token_budget=2000)
    context = await builder.build_context(conv_id)

    # First item is the summary
    assert len(context) == 3
    assert context[0]["role"] == "system"
    assert "Previous conversation summary" in context[0]["content"]
    assert "RAG architecture" in context[0]["content"]

    # Then the messages
    assert context[1] == {"role": "user", "content": "Tell me more about chunking"}
    assert context[2] == {"role": "assistant", "content": "Chunking is the process of..."}


# ---------------------------------------------------------------------------
# ConversationContextBuilder: summary not included when below threshold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_summary_skipped_when_below_threshold():
    """Summary should NOT be included when message_count <= max_messages."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(
        conv_id,
        message_count=5,  # below max_messages=10
        summary="Some summary text",
    )
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    messages = [_make_message("user", "Hello")]
    msg_scalars = MagicMock()
    msg_scalars.all.return_value = list(reversed(messages))
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    session.execute = AsyncMock(side_effect=[conv_result, msg_result])

    builder = ConversationContextBuilder(session, max_messages=10, token_budget=2000)
    context = await builder.build_context(conv_id)

    # Only the message, no summary
    assert len(context) == 1
    assert context[0]["role"] == "user"


# ---------------------------------------------------------------------------
# ConversationContextBuilder: token budget enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_context_respects_token_budget():
    """Should stop adding messages when token budget is exhausted."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(conv_id, message_count=4)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    # Each message ~100 chars => ~25 tokens. Budget=30 tokens => only 1 fits.
    messages = [
        _make_message("user", "A" * 100),
        _make_message("assistant", "B" * 100),
    ]
    msg_scalars = MagicMock()
    msg_scalars.all.return_value = list(reversed(messages))
    msg_result = MagicMock()
    msg_result.scalars.return_value = msg_scalars

    session.execute = AsyncMock(side_effect=[conv_result, msg_result])

    builder = ConversationContextBuilder(session, max_messages=10, token_budget=30)
    context = await builder.build_context(conv_id)

    # Only first message fits (25 tokens), second (25 tokens) exceeds remaining 5
    assert len(context) == 1
    assert context[0]["content"] == "A" * 100


# ---------------------------------------------------------------------------
# Auto-title: generate_title success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_success():
    """generate_title should save a generated title to the conversation."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(conv_id, title=None)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    session.execute = AsyncMock(return_value=conv_result)
    session.flush = AsyncMock()

    llm = AsyncMock()
    llm.complete = AsyncMock(
        return_value=LLMResponse(
            content="Understanding RAG Architecture",
            model="test",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )
    )

    llm.fast_call_options = MagicMock(
        return_value={"model": "fast-model", "temperature": 0.0, "max_tokens": 512}
    )
    seen: list[LLMResponse] = []

    title = await generate_title(
        session,
        llm,
        conv_id,
        "Can you explain the RAG architecture?",
        on_response=seen.append,
    )

    assert title == "Understanding RAG Architecture"
    assert conv.title == "Understanding RAG Architecture"
    session.flush.assert_awaited_once()
    llm.complete.assert_awaited_once()
    # Runs on the fast model, with the title's own short cap.
    kwargs = llm.complete.await_args.kwargs
    assert kwargs == {"model": "fast-model", "temperature": 0.3, "max_tokens": 60}
    # The reply is handed back so the caller can meter it.
    assert [r.usage.total_tokens for r in seen] == [15]


@pytest.mark.asyncio
async def test_generate_title_leaves_reasoning_fast_model_uncapped():
    """No cap when fast_call_options gave none: a reasoning model would be truncated."""
    session = AsyncMock()
    conv_id = uuid.uuid4()
    conv = _make_conversation(conv_id, title=None)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv
    session.execute = AsyncMock(return_value=conv_result)

    llm = AsyncMock()
    llm.fast_call_options = MagicMock(return_value={"model": "gpt-oss", "temperature": 0.0})
    llm.complete = AsyncMock(return_value=LLMResponse(content="T", model="gpt-oss"))

    await generate_title(session, llm, conv_id, "Hi")

    assert "max_tokens" not in llm.complete.await_args.kwargs


# ---------------------------------------------------------------------------
# Auto-title: skips if title already set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_skips_existing():
    """generate_title should not overwrite an existing title."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(conv_id, title="Existing Title")
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    session.execute = AsyncMock(return_value=conv_result)

    llm = AsyncMock()

    title = await generate_title(session, llm, conv_id, "Hello")

    assert title == "Existing Title"
    llm.complete.assert_not_awaited()


# ---------------------------------------------------------------------------
# Auto-title: non-fatal on LLM error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_non_fatal_on_error():
    """generate_title should return None and not raise on failure."""
    session = AsyncMock()
    conv_id = uuid.uuid4()

    conv = _make_conversation(conv_id, title=None)
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conv

    session.execute = AsyncMock(return_value=conv_result)

    llm = AsyncMock()
    llm.fast_call_options = MagicMock(return_value={"model": "fast"})
    llm.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    title = await generate_title(session, llm, conv_id, "Hello")

    assert title is None


# ---------------------------------------------------------------------------
# Auto-title: conversation not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_title_conversation_not_found():
    """generate_title should return None for missing conversation."""
    session = AsyncMock()

    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=conv_result)

    llm = AsyncMock()

    title = await generate_title(session, llm, uuid.uuid4(), "Hello")

    assert title is None
    llm.complete.assert_not_awaited()
