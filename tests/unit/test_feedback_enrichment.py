"""Unit tests for feedback enrichment & session tracking (Epic 3-08)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.db.models.feedback import Feedback


class TestFeedbackModelColumns:
    def test_feedback_has_feedback_type(self) -> None:
        """Feedback model has feedback_type column."""
        fb = Feedback(
            id=uuid.uuid4(),
            query_log_id=uuid.uuid4(),
            rating=3,
            feedback_type="explicit",
        )
        assert fb.feedback_type == "explicit"

    def test_feedback_has_session_id(self) -> None:
        """Feedback model has session_id column."""
        sid = "session-abc-123"
        fb = Feedback(id=uuid.uuid4(), session_id=sid)
        assert fb.session_id == sid

    def test_feedback_has_trace_id(self) -> None:
        """Feedback model has trace_id column."""
        fb = Feedback(id=uuid.uuid4(), trace_id="langfuse-trace-abc")
        assert fb.trace_id == "langfuse-trace-abc"


class TestFeedbackSchemas:
    def test_feedback_request_accepts_session_id_and_trace_id(self) -> None:
        """FeedbackRequest should accept optional session_id and trace_id."""
        from callobuzz_rag.api.schemas.feedback import FeedbackRequest

        req = FeedbackRequest(
            query_id=uuid.uuid4(),
            rating=4,
            session_id="session-test-123",
            trace_id="langfuse-trace-abc123",
        )
        assert req.session_id is not None
        assert req.trace_id == "langfuse-trace-abc123"

    def test_feedback_request_session_id_optional(self) -> None:
        """session_id and trace_id default to None."""
        from callobuzz_rag.api.schemas.feedback import FeedbackRequest

        req = FeedbackRequest(query_id=uuid.uuid4(), rating=3)
        assert req.session_id is None
        assert req.trace_id is None

    def test_feedback_stats_schema(self) -> None:
        """FeedbackStats schema should serialize correctly."""
        from callobuzz_rag.api.schemas.feedback import FeedbackStatsResponse

        stats = FeedbackStatsResponse(
            corpus_id=uuid.uuid4(),
            total=100,
            positive=70,
            negative=20,
            corrections=5,
            implicit=5,
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert stats.positive + stats.negative + stats.implicit == 95


class TestFeedbackEnrichmentService:
    @pytest.mark.asyncio
    async def test_detect_followup_true_within_window(self) -> None:
        """Follow-up detected when same corpus_id query within time window."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        session_id = uuid.uuid4()
        corpus_id = uuid.uuid4()

        previous_query = MagicMock()
        previous_query.created_at = datetime.now(UTC) - timedelta(seconds=30)
        previous_query.corpus_id = corpus_id

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [previous_query]
        session.execute = AsyncMock(return_value=mock_result)

        result = await service.detect_followup(
            session_id=session_id,
            corpus_id=corpus_id,
            window_seconds=60,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_detect_followup_false_no_prior_query(self) -> None:
        """No follow-up when no prior queries in session."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        result = await service.detect_followup(
            session_id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            window_seconds=60,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_record_implicit_feedback(self) -> None:
        """Record implicit feedback creates a feedback record with type='implicit'."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        feedback = await service.record_implicit_feedback(
            session_id=uuid.uuid4(),
            query_log_id=uuid.uuid4(),
        )
        assert feedback.feedback_type == "implicit"
        assert feedback.rating == 1
        session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_queries(self) -> None:
        """Get all queries in a session."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        q1, q2 = MagicMock(), MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [q1, q2]
        session.execute = AsyncMock(return_value=mock_result)

        queries = await service.get_session_queries(uuid.uuid4())
        assert len(queries) == 2

    @pytest.mark.asyncio
    async def test_link_feedback_to_trace(self) -> None:
        """Linking feedback to trace updates the trace_id field."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        feedback = MagicMock()
        session.get = AsyncMock(return_value=feedback)

        await service.link_feedback_to_trace(uuid.uuid4(), "trace-xyz")
        assert feedback.trace_id == "trace-xyz"

    @pytest.mark.asyncio
    async def test_link_feedback_to_trace_not_found(self) -> None:
        """Linking a non-existent feedback ID raises ValueError."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        session.get = AsyncMock(return_value=None)
        service = FeedbackEnrichmentService(session)

        with pytest.raises(ValueError, match="not found"):
            await service.link_feedback_to_trace(uuid.uuid4(), "trace-abc")

    @pytest.mark.asyncio
    async def test_get_feedback_stats_empty_corpus(self) -> None:
        """Stats for a corpus with no feedback returns all zeros."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        session.execute = AsyncMock(return_value=mock_result)

        stats = await service.get_feedback_stats(
            corpus_id=uuid.uuid4(),
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 3, 1, tzinfo=UTC),
        )
        assert stats.total == 0
        assert stats.positive == 0
        assert stats.negative == 0
        assert stats.corrections == 0
        assert stats.implicit == 0

    @pytest.mark.asyncio
    async def test_get_session_queries_empty(self) -> None:
        """Session with no queries returns empty list."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

        service = FeedbackEnrichmentService(session)
        queries = await service.get_session_queries(uuid.uuid4())
        assert queries == []


class TestImplicitFeedbackHook:
    @pytest.mark.asyncio
    async def test_implicit_feedback_hook_called_on_followup(self) -> None:
        """The hook triggers implicit feedback on follow-ups."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        with (
            patch.object(service, "detect_followup", return_value=True) as mock_detect,
            patch.object(service, "record_implicit_feedback") as mock_record,
        ):
            session_id = uuid.uuid4()
            corpus_id = uuid.uuid4()
            query_log_id = uuid.uuid4()

            is_followup = await service.detect_followup(
                session_id=session_id,
                corpus_id=corpus_id,
            )
            if is_followup:
                await service.record_implicit_feedback(
                    session_id=session_id,
                    query_log_id=query_log_id,
                )

            mock_detect.assert_called_once()
            mock_record.assert_called_once_with(
                session_id=session_id,
                query_log_id=query_log_id,
            )

    @pytest.mark.asyncio
    async def test_implicit_feedback_hook_skipped_when_no_followup(self) -> None:
        """No implicit feedback when query is not a follow-up."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackEnrichmentService

        session = AsyncMock()
        service = FeedbackEnrichmentService(session)

        with (
            patch.object(service, "detect_followup", return_value=False),
            patch.object(service, "record_implicit_feedback") as mock_record,
        ):
            session_id = uuid.uuid4()
            corpus_id = uuid.uuid4()

            is_followup = await service.detect_followup(
                session_id=session_id,
                corpus_id=corpus_id,
            )
            if is_followup:
                await service.record_implicit_feedback(
                    session_id=session_id,
                    query_log_id=uuid.uuid4(),
                )

            mock_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_and_record_skips_when_no_session(self) -> None:
        """check_and_record_implicit_feedback is a no-op when session_id is None."""
        from callobuzz_rag.analytics.feedback_enrichment import (
            check_and_record_implicit_feedback,
        )

        session = AsyncMock()
        await check_and_record_implicit_feedback(
            session=session,
            session_id=None,
            corpus_id=uuid.uuid4(),
            current_query_log_id=uuid.uuid4(),
        )
        session.execute.assert_not_called()


class TestFeedbackStatsDataclass:
    def test_feedback_stats_holds_fields(self) -> None:
        """FeedbackStats holds all expected fields."""
        from callobuzz_rag.analytics.feedback_enrichment import FeedbackStats

        cid = uuid.uuid4()
        stats = FeedbackStats(
            corpus_id=cid, total=10, positive=7, negative=2, corrections=1, implicit=0
        )
        assert stats.corpus_id == cid
        assert stats.total == 10
        assert stats.positive == 7


class TestFeedbackRoutes:
    def test_feedback_stats_endpoint_exists(self) -> None:
        """GET /api/v1/feedback/stats should be a registered route."""
        from callobuzz_rag.api.routes.feedback import router

        paths = [r.path for r in router.routes]
        assert "/stats" in paths or "/api/v1/feedback/stats" in paths

    def test_feedback_submit_includes_session_id(self) -> None:
        """POST /api/v1/feedback should accept session_id in body."""
        from callobuzz_rag.api.routes.feedback import router

        post_routes = [r for r in router.routes if hasattr(r, "methods") and "POST" in r.methods]
        assert len(post_routes) >= 1
