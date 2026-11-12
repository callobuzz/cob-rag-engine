"""Feedback enrichment & session tracking.

Detects implicit dissatisfaction signals from query sessions,
records implicit feedback, and provides feedback analytics.

Epic 3-08.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.feedback import Feedback
from callobuzz_rag.db.models.query_log import QueryLog

logger = logging.getLogger(__name__)


class FeedbackStats:
    """Aggregated feedback statistics."""

    __slots__ = ("corpus_id", "corrections", "implicit", "negative", "positive", "total")

    def __init__(
        self,
        corpus_id: uuid.UUID,
        total: int,
        positive: int,
        negative: int,
        corrections: int,
        implicit: int,
    ) -> None:
        self.corpus_id = corpus_id
        self.total = total
        self.positive = positive
        self.negative = negative
        self.corrections = corrections
        self.implicit = implicit


class FeedbackEnrichmentService:
    """Service for feedback enrichment and session tracking."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _check_query_log_columns() -> bool:
        """Check if QueryLog has the required session_id and corpus_id columns."""
        return hasattr(QueryLog, "session_id") and hasattr(QueryLog, "corpus_id")

    async def detect_followup(
        self,
        session_id: str,
        corpus_id: uuid.UUID,
        window_seconds: int = 60,
    ) -> bool:
        """Check if a query is a follow-up within a session time window."""
        if not self._check_query_log_columns():
            return False

        cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)

        stmt = select(QueryLog).where(
            and_(
                QueryLog.session_id == session_id,
                QueryLog.corpus_id == corpus_id,
                QueryLog.created_at >= cutoff,
            )
        )
        result = await self._session.execute(stmt)
        prior_queries = result.scalars().all()

        if len(prior_queries) > 0:
            logger.info(
                "Follow-up detected: session=%s corpus=%s prior_count=%d",
                session_id,
                corpus_id,
                len(prior_queries),
            )
            return True

        return False

    async def record_implicit_feedback(
        self,
        session_id: str,
        query_log_id: uuid.UUID,
    ) -> Feedback:
        """Record implicit negative feedback for a follow-up query."""
        feedback = Feedback(
            query_log_id=query_log_id,
            rating=1,
            feedback_type="implicit",
            session_id=session_id,
        )
        self._session.add(feedback)
        logger.info(
            "Implicit feedback recorded: session=%s query_log=%s",
            session_id,
            query_log_id,
        )
        return feedback

    async def get_session_queries(
        self,
        session_id: str,
    ) -> list[QueryLog]:
        """Retrieve all queries belonging to a session, ordered by time."""
        if not self._check_query_log_columns():
            return []

        stmt = (
            select(QueryLog)
            .where(QueryLog.session_id == session_id)
            .order_by(QueryLog.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_feedback_stats(
        self,
        corpus_id: uuid.UUID,
        start: datetime,
        end: datetime,
    ) -> FeedbackStats:
        """Compute aggregated feedback statistics for a corpus."""
        base_where = and_(
            QueryLog.corpus_id == corpus_id,
            Feedback.created_at >= start,
            Feedback.created_at < end,
        )

        total_stmt = (
            select(func.count())
            .select_from(Feedback)
            .join(QueryLog, Feedback.query_log_id == QueryLog.id)
            .where(base_where)
        )
        total_result = await self._session.execute(total_stmt)
        total = total_result.scalar() or 0

        positive_stmt = (
            select(func.count())
            .select_from(Feedback)
            .join(QueryLog, Feedback.query_log_id == QueryLog.id)
            .where(and_(base_where, Feedback.rating >= 4, Feedback.feedback_type == "explicit"))
        )
        positive_result = await self._session.execute(positive_stmt)
        positive = positive_result.scalar() or 0

        negative_stmt = (
            select(func.count())
            .select_from(Feedback)
            .join(QueryLog, Feedback.query_log_id == QueryLog.id)
            .where(and_(base_where, Feedback.rating <= 2, Feedback.feedback_type == "explicit"))
        )
        negative_result = await self._session.execute(negative_stmt)
        negative = negative_result.scalar() or 0

        correction_stmt = (
            select(func.count())
            .select_from(Feedback)
            .join(QueryLog, Feedback.query_log_id == QueryLog.id)
            .where(and_(base_where, Feedback.correction.isnot(None)))
        )
        correction_result = await self._session.execute(correction_stmt)
        corrections = correction_result.scalar() or 0

        implicit_stmt = (
            select(func.count())
            .select_from(Feedback)
            .join(QueryLog, Feedback.query_log_id == QueryLog.id)
            .where(and_(base_where, Feedback.feedback_type == "implicit"))
        )
        implicit_result = await self._session.execute(implicit_stmt)
        implicit = implicit_result.scalar() or 0

        return FeedbackStats(
            corpus_id=corpus_id,
            total=total,
            positive=positive,
            negative=negative,
            corrections=corrections,
            implicit=implicit,
        )

    async def link_feedback_to_trace(
        self,
        feedback_id: uuid.UUID,
        trace_id: str,
    ) -> None:
        """Link a feedback record to a Langfuse trace."""
        feedback = await self._session.get(Feedback, feedback_id)
        if feedback is None:
            raise ValueError(f"Feedback {feedback_id} not found")

        feedback.trace_id = trace_id
        logger.info("Linked feedback %s to trace %s", feedback_id, trace_id)


async def check_and_record_implicit_feedback(
    session: AsyncSession,
    session_id: str | None,
    corpus_id: uuid.UUID,
    current_query_log_id: uuid.UUID,
) -> None:
    """Post-query hook: detect follow-up and record implicit feedback."""
    if session_id is None:
        return

    service = FeedbackEnrichmentService(session)

    is_followup = await service.detect_followup(
        session_id=session_id,
        corpus_id=corpus_id,
    )

    if not is_followup:
        return

    queries = await service.get_session_queries(session_id)
    prior_queries = [q for q in queries if q.id != current_query_log_id]
    if prior_queries:
        previous_query = prior_queries[-1]
        await service.record_implicit_feedback(
            session_id=session_id,
            query_log_id=previous_query.id,
        )
        logger.info(
            "Implicit feedback recorded for prior query %s in session %s",
            previous_query.id,
            session_id,
        )
