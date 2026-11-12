"""Unit tests for ConversationService — message send flow."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.api.services.conversation_service import (
    ConversationNotFoundError,
    ConversationService,
)
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.llm.models import LLMResponse, LLMUsage


def _make_conversation(corpus_id, conv_id=None):
    """Create a mock Conversation object."""
    conv = MagicMock(spec=Conversation)
    conv.id = conv_id or uuid.uuid4()
    conv.corpus_id = corpus_id
    conv.title = "Test Chat"
    conv.message_count = 0
    conv.total_tokens = 0
    return conv


def _make_llm_response(content="Hello! How can I help?", total_tokens=42):
    """Create an LLMResponse."""
    return LLMResponse(
        content=content,
        model="test-model",
        usage=LLMUsage(
            prompt_tokens=20,
            completion_tokens=22,
            total_tokens=total_tokens,
        ),
    )


@pytest.fixture
def corpus_id():
    return uuid.uuid4()


@pytest.fixture
def conversation_id():
    return uuid.uuid4()


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


@pytest.fixture
def mock_llm():
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=_make_llm_response())
    return llm


# ---------------------------------------------------------------------------
# Successful send — messages stored, counters updated
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_success(mock_session, mock_llm, corpus_id, conversation_id):
    """send_message should store user+assistant messages and update counters."""
    conversation = _make_conversation(corpus_id, conversation_id)

    # Mock: _get_conversation returns the conversation
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conversation

    # Mock: context_builder.build_context returns empty list
    # ConversationService makes 1 execute call for _get_conversation;
    # ConversationContextBuilder makes 2 execute calls (conv + messages)
    cb_conv_result = MagicMock()
    cb_conv_result.scalar_one_or_none.return_value = conversation

    cb_msg_scalars = MagicMock()
    cb_msg_scalars.all.return_value = []
    cb_msg_result = MagicMock()
    cb_msg_result.scalars.return_value = cb_msg_scalars

    # Auto-title: generate_title makes 1 execute call (conv lookup)
    title_conv = MagicMock()
    title_conv.id = conversation_id
    title_conv.title = "Test Chat"  # already has title, will skip
    title_result = MagicMock()
    title_result.scalar_one_or_none.return_value = title_conv

    mock_session.execute = AsyncMock(
        side_effect=[conv_result, cb_conv_result, cb_msg_result, title_result]
    )

    service = ConversationService(
        session=mock_session,
        llm_provider=mock_llm,
        searcher=None,
    )

    _user_msg, _assistant_msg, retrieval_ctx = await service.send_message(
        conversation_id=conversation_id,
        corpus_id=corpus_id,
        content="What is RAG?",
    )

    # Both messages should be added to the session
    assert mock_session.add.call_count == 2

    # User message
    first_add_arg = mock_session.add.call_args_list[0][0][0]
    assert isinstance(first_add_arg, Message)
    assert first_add_arg.role == "user"
    assert first_add_arg.content == "What is RAG?"

    # Assistant message
    second_add_arg = mock_session.add.call_args_list[1][0][0]
    assert isinstance(second_add_arg, Message)
    assert second_add_arg.role == "assistant"
    assert second_add_arg.content == "Hello! How can I help?"
    assert second_add_arg.token_count == 42

    # LLM was called with correct messages
    mock_llm.complete.assert_awaited()
    # First call is for message generation (auto-title may also call)
    llm_messages = mock_llm.complete.call_args_list[0][0][0]
    assert llm_messages[0].role == "system"
    assert llm_messages[-1].role == "user"
    assert llm_messages[-1].content == "What is RAG?"

    # Conversation counters updated
    assert conversation.message_count == 2
    assert conversation.total_tokens == 42

    # Session committed
    mock_session.commit.assert_awaited_once()

    # No retrieval context when no searcher
    assert retrieval_ctx is None


# ---------------------------------------------------------------------------
# Send with conversation history
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_history(mock_session, mock_llm, corpus_id, conversation_id):
    """send_message should include recent history in LLM messages."""
    conversation = _make_conversation(corpus_id, conversation_id)
    conversation.message_count = 2
    conversation.total_tokens = 30

    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conversation

    # Context builder: conv lookup + message fetch
    cb_conv_result = MagicMock()
    cb_conv_result.scalar_one_or_none.return_value = conversation

    now = datetime.now(UTC)
    prev_user = MagicMock(spec=Message)
    prev_user.role = "user"
    prev_user.content = "Hi"
    prev_user.created_at = now
    prev_user.token_count = 0

    prev_assistant = MagicMock(spec=Message)
    prev_assistant.role = "assistant"
    prev_assistant.content = "Hello!"
    prev_assistant.created_at = now
    prev_assistant.token_count = 0

    new_user = MagicMock(spec=Message)
    new_user.role = "user"
    new_user.content = "Tell me about RAG"
    new_user.created_at = now
    new_user.token_count = 0

    # DB returns desc order (newest first); builder reverses to oldest-first
    cb_msg_scalars = MagicMock()
    cb_msg_scalars.all.return_value = [new_user, prev_assistant, prev_user]
    cb_msg_result = MagicMock()
    cb_msg_result.scalars.return_value = cb_msg_scalars

    mock_session.execute = AsyncMock(side_effect=[conv_result, cb_conv_result, cb_msg_result])

    service = ConversationService(
        session=mock_session,
        llm_provider=mock_llm,
        searcher=None,
        auto_title_enabled=False,
    )

    await service.send_message(
        conversation_id=conversation_id,
        corpus_id=corpus_id,
        content="Tell me about RAG",
    )

    # LLM messages: system + prev_user + prev_assistant + current user
    llm_messages = mock_llm.complete.call_args_list[0][0][0]
    assert len(llm_messages) == 4
    assert llm_messages[0].role == "system"
    assert llm_messages[1].role == "user"
    assert llm_messages[1].content == "Hi"
    assert llm_messages[2].role == "assistant"
    assert llm_messages[2].content == "Hello!"
    assert llm_messages[3].role == "user"
    assert llm_messages[3].content == "Tell me about RAG"

    # Counters should be incremented from existing values
    assert conversation.message_count == 4
    assert conversation.total_tokens == 30 + 42


# ---------------------------------------------------------------------------
# Conversation not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_conversation_not_found(mock_session, mock_llm, corpus_id):
    """send_message should raise ConversationNotFoundError for missing conversation."""
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=conv_result)

    service = ConversationService(
        session=mock_session,
        llm_provider=mock_llm,
        searcher=None,
    )

    with pytest.raises(ConversationNotFoundError):
        await service.send_message(
            conversation_id=uuid.uuid4(),
            corpus_id=corpus_id,
            content="Hello",
        )

    # LLM should never be called
    mock_llm.complete.assert_not_awaited()
    mock_session.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Send with retrieval context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_with_retrieval(mock_session, mock_llm, corpus_id, conversation_id):
    """send_message should include retrieval context when searcher is provided."""
    conversation = _make_conversation(corpus_id, conversation_id)

    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conversation

    # Context builder mocks
    cb_conv_result = MagicMock()
    cb_conv_result.scalar_one_or_none.return_value = conversation

    cb_msg_scalars = MagicMock()
    cb_msg_scalars.all.return_value = []
    cb_msg_result = MagicMock()
    cb_msg_result.scalars.return_value = cb_msg_scalars

    # Auto-title conv lookup
    title_conv = MagicMock()
    title_conv.id = conversation_id
    title_conv.title = "Test Chat"
    title_result = MagicMock()
    title_result.scalar_one_or_none.return_value = title_conv

    mock_session.execute = AsyncMock(
        side_effect=[conv_result, cb_conv_result, cb_msg_result, title_result]
    )

    # Mock searcher
    mock_searcher = AsyncMock()
    mock_result = MagicMock()
    mock_result.chunk_id = "chunk-1"
    mock_result.text = "RAG is Retrieval-Augmented Generation."
    mock_result.score = 0.95
    mock_result.document_id = "doc-1"
    mock_searcher.search = AsyncMock(return_value=[mock_result])

    service = ConversationService(
        session=mock_session,
        llm_provider=mock_llm,
        searcher=mock_searcher,
    )

    _user_msg, _assistant_msg, retrieval_ctx = await service.send_message(
        conversation_id=conversation_id,
        corpus_id=corpus_id,
        content="What is RAG?",
    )

    # Retrieval context should be present
    assert retrieval_ctx is not None
    assert retrieval_ctx["total_chunks"] == 1
    assert retrieval_ctx["chunks"][0]["chunk_id"] == "chunk-1"
    assert retrieval_ctx["chunks"][0]["score"] == 0.95

    # LLM system prompt should include context
    llm_messages = mock_llm.complete.call_args_list[0][0][0]
    assert "RAG is Retrieval-Augmented Generation." in llm_messages[0].content

    # Assistant message should have retrieval_context stored
    assistant_add = mock_session.add.call_args_list[1][0][0]
    assert assistant_add.retrieval_context == retrieval_ctx


# ---------------------------------------------------------------------------
# Retrieval failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_retrieval_failure_non_fatal(
    mock_session, mock_llm, corpus_id, conversation_id
):
    """Retrieval failure should not block message generation."""
    conversation = _make_conversation(corpus_id, conversation_id)

    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conversation

    # Context builder mocks
    cb_conv_result = MagicMock()
    cb_conv_result.scalar_one_or_none.return_value = conversation

    cb_msg_scalars = MagicMock()
    cb_msg_scalars.all.return_value = []
    cb_msg_result = MagicMock()
    cb_msg_result.scalars.return_value = cb_msg_scalars

    # Auto-title conv lookup
    title_conv = MagicMock()
    title_conv.id = conversation_id
    title_conv.title = "Test Chat"
    title_result = MagicMock()
    title_result.scalar_one_or_none.return_value = title_conv

    mock_session.execute = AsyncMock(
        side_effect=[conv_result, cb_conv_result, cb_msg_result, title_result]
    )

    # Mock searcher that raises an exception
    mock_searcher = AsyncMock()
    mock_searcher.search = AsyncMock(side_effect=RuntimeError("Qdrant down"))

    service = ConversationService(
        session=mock_session,
        llm_provider=mock_llm,
        searcher=mock_searcher,
    )

    _user_msg, _assistant_msg, retrieval_ctx = await service.send_message(
        conversation_id=conversation_id,
        corpus_id=corpus_id,
        content="What is RAG?",
    )

    # Should still succeed without retrieval context
    assert retrieval_ctx is None
    mock_llm.complete.assert_awaited()
    assert conversation.message_count == 2


# ---------------------------------------------------------------------------
# Metering hand-off and title persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_message_collects_answer_and_title_replies(
    mock_session, corpus_id, conversation_id
):
    """Every model reply is exposed for metering, and a new title is committed."""
    conversation = _make_conversation(corpus_id, conversation_id)
    conversation.message_count = 0
    conv_result = MagicMock()
    conv_result.scalar_one_or_none.return_value = conversation
    cb_conv_result = MagicMock()
    cb_conv_result.scalar_one_or_none.return_value = conversation
    cb_msg_scalars = MagicMock()
    cb_msg_scalars.all.return_value = []
    cb_msg_result = MagicMock()
    cb_msg_result.scalars.return_value = cb_msg_scalars
    untitled = MagicMock()
    untitled.title = None
    title_result = MagicMock()
    title_result.scalar_one_or_none.return_value = untitled
    mock_session.execute = AsyncMock(
        side_effect=[conv_result, cb_conv_result, cb_msg_result, title_result]
    )

    title_reply = _make_llm_response(content="About RAG", total_tokens=7)
    llm = AsyncMock()
    llm.fast_call_options = MagicMock(return_value={"model": "fast-model"})
    llm.complete = AsyncMock(side_effect=[_make_llm_response(), title_reply])

    service = ConversationService(session=mock_session, llm_provider=llm, searcher=None)
    await service.send_message(
        conversation_id=conversation_id, corpus_id=corpus_id, content="What is RAG?"
    )

    assert [r.content for r in service.llm_responses] == ["Hello! How can I help?", "About RAG"]
    assert untitled.title == "About RAG"
    # Once for the messages, once for the title (it used to be flushed and lost).
    assert mock_session.commit.await_count == 2
    assert llm.complete.await_args_list[1].kwargs["model"] == "fast-model"
