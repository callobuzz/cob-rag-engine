"""Tests for Memory model and ConversationSummaryService."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.db.models.memory import Memory
from callobuzz_rag.memory.summary import SUMMARY_PROMPT, ConversationSummaryService

# ---------------------------------------------------------------------------
# Memory model
# ---------------------------------------------------------------------------


class TestMemoryModel:
    def test_tablename(self) -> None:
        assert Memory.__tablename__ == "memories"

    def test_columns_exist(self) -> None:
        cols = {c.name for c in Memory.__table__.columns}
        expected = {
            "id",
            "corpus_id",
            "conversation_id",
            "memory_type",
            "content",
            "embedding",
            "metadata",
            "created_at",
            "expires_at",
        }
        assert expected.issubset(cols)

    def test_nullable_columns(self) -> None:
        table = Memory.__table__
        assert table.c.conversation_id.nullable is True
        assert table.c.embedding.nullable is True
        assert table.c.metadata.nullable is True
        assert table.c.expires_at.nullable is True

    def test_non_nullable_columns(self) -> None:
        table = Memory.__table__
        assert table.c.corpus_id.nullable is False
        assert table.c.memory_type.nullable is False
        assert table.c.content.nullable is False


# ---------------------------------------------------------------------------
# ConversationSummaryService — below threshold
# ---------------------------------------------------------------------------


class TestSummaryBelowThreshold:
    @pytest.mark.asyncio
    async def test_no_summary_below_threshold(self) -> None:
        """Should return None when message_count < threshold."""
        conv = MagicMock()
        conv.message_count = 5
        conv.summary = None

        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = conv
        session.execute = AsyncMock(return_value=result_mock)

        llm = AsyncMock()

        svc = ConversationSummaryService(session, llm, summary_threshold=10)
        result = await svc.maybe_update_summary(str(uuid.uuid4()))

        assert result is None
        llm.complete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_summary_conversation_not_found(self) -> None:
        """Should return None when conversation doesn't exist."""
        session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        llm = AsyncMock()

        svc = ConversationSummaryService(session, llm)
        result = await svc.maybe_update_summary(str(uuid.uuid4()))

        assert result is None


# ---------------------------------------------------------------------------
# ConversationSummaryService — context with summary
# ---------------------------------------------------------------------------


class TestContextWithSummary:
    @pytest.mark.asyncio
    async def test_context_includes_summary(self) -> None:
        """Summary should be prepended as system message."""
        conv = MagicMock()
        conv.summary = "This is a summary of previous discussion."

        msg1 = MagicMock()
        msg1.role = "user"
        msg1.content = "Hello"
        msg1.token_count = 2
        msg1.created_at = datetime(2026, 1, 1, tzinfo=UTC)

        msg2 = MagicMock()
        msg2.role = "assistant"
        msg2.content = "Hi there"
        msg2.token_count = 3
        msg2.created_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

        session = AsyncMock()
        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                # conversation query
                mock_result.scalar_one_or_none.return_value = conv
            else:
                # messages query
                # Query orders by created_at DESC, so newest first
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = [msg2, msg1]
                mock_result.scalars.return_value = scalars_mock
            return mock_result

        session.execute = AsyncMock(side_effect=fake_execute)
        llm = AsyncMock()

        svc = ConversationSummaryService(session, llm)
        context = await svc.get_context_messages(
            str(uuid.uuid4()), max_messages=20, max_tokens=4096
        )

        assert len(context) == 3
        assert context[0].role == "system"
        assert "summary" in context[0].content.lower()
        assert context[1].role == "user"
        assert context[1].content == "Hello"
        assert context[2].role == "assistant"
        assert context[2].content == "Hi there"

    @pytest.mark.asyncio
    async def test_context_without_summary(self) -> None:
        """When no summary, only messages returned."""
        conv = MagicMock()
        conv.summary = None

        msg1 = MagicMock()
        msg1.role = "user"
        msg1.content = "Hello"
        msg1.token_count = 2
        msg1.created_at = datetime(2026, 1, 1, tzinfo=UTC)

        session = AsyncMock()
        call_count = 0

        async def fake_execute(stmt):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            if call_count == 1:
                mock_result.scalar_one_or_none.return_value = conv
            else:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = [msg1]
                mock_result.scalars.return_value = scalars_mock
            return mock_result

        session.execute = AsyncMock(side_effect=fake_execute)
        llm = AsyncMock()

        svc = ConversationSummaryService(session, llm)
        context = await svc.get_context_messages(
            str(uuid.uuid4()), max_messages=20, max_tokens=4096
        )

        assert len(context) == 1
        assert context[0].role == "user"


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestMemoryConfig:
    def test_memory_defaults(self) -> None:
        """Memory settings should have correct defaults."""
        with patch.dict(
            "os.environ",
            {
                "RAG_DB_PASSWORD": "test",
                "RAG_S3_ACCESS_KEY": "test",
                "RAG_S3_SECRET_KEY": "test",
                "RAG_JINA_API_KEY": "test",
                "RAG_LLM_API_KEY": "test",
                "RAG_ADMIN_API_KEY": "test",
                "RAG_LANGFUSE_SECRET_KEY": "test",
                "RAG_WHISPER_API_KEY": "test",
                "RAG_WEB_SEARCH_API_KEY": "test",
            },
            clear=False,
        ):
            from callobuzz_rag.core.config import Settings

            s = Settings()
            assert s.memory_mode == "summary"
            assert s.memory_summary_threshold == 10
            assert s.memory_max_entries == 10000
            assert s.memory_ttl_days == 90
            assert s.memory_extraction_model == ""
            assert s.memory_auto_inject is True
            assert s.memory_top_k == 5


# ---------------------------------------------------------------------------
# Summary prompt template
# ---------------------------------------------------------------------------


class TestSummaryPrompt:
    def test_prompt_has_transcript_placeholder(self) -> None:
        assert "{transcript}" in SUMMARY_PROMPT
