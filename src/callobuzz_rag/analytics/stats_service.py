"""QueryStatsService — aggregation queries over query_log."""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, get_args

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.db.models.query_log import QueryLog

# The `date_trunc` fields the trends endpoint documents and accepts.
TrendGranularity = Literal["hour", "day", "week", "month"]
TREND_GRANULARITIES: tuple[str, ...] = get_args(TrendGranularity)


class QueryStatsService:
    """Analytics aggregation service for the query_log table."""

    async def get_query_count(
        self,
        session: AsyncSession,
        corpus_id: uuid.UUID | None = None,
        corpus_ids: Sequence[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> int:
        """Return total query count with optional corpus/date filters."""
        stmt = select(func.count()).select_from(QueryLog)
        stmt = self._apply_filters(
            stmt, corpus_id=corpus_id, corpus_ids=corpus_ids, start=start, end=end
        )
        result = await session.execute(stmt)
        return int(result.scalar_one())

    async def get_trends(
        self,
        session: AsyncSession,
        corpus_id: uuid.UUID | None = None,
        corpus_ids: Sequence[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        granularity: TrendGranularity = "day",
    ) -> list[dict[str, Any]]:
        """Return time-series trend data grouped by granularity.

        Each item has keys: bucket, count, avg_confidence, avg_latency. `granularity`
        reaches SQL as a bound parameter, and only from the whitelist.

        Raises:
            ValueError: `granularity` is not one of `TREND_GRANULARITIES`.
        """
        if granularity not in TREND_GRANULARITIES:
            raise ValueError(
                f"granularity must be one of {', '.join(TREND_GRANULARITIES)}, not {granularity!r}"
            )
        bucket = func.date_trunc(granularity, QueryLog.created_at).label("bucket")
        stmt = (
            select(
                bucket,
                func.count().label("count"),
                func.avg(QueryLog.confidence_score).label("avg_confidence"),
                func.avg(QueryLog.latency_ms).label("avg_latency"),
            )
            .select_from(QueryLog)
            .group_by(bucket)
            .order_by(bucket)
        )
        stmt = self._apply_filters(
            stmt, corpus_id=corpus_id, corpus_ids=corpus_ids, start=start, end=end
        )
        result = await session.execute(stmt)
        rows = result.all()
        return [
            {
                "bucket": row.bucket,
                "count": row.count,
                "avg_confidence": float(row.avg_confidence) if row.avg_confidence else None,
                "avg_latency": float(row.avg_latency) if row.avg_latency else None,
            }
            for row in rows
        ]

    async def get_query_history(
        self,
        session: AsyncSession,
        corpus_id: uuid.UUID | None = None,
        corpus_ids: Sequence[uuid.UUID] | None = None,
        offset: int = 0,
        limit: int = 20,
        session_id: str | None = None,
    ) -> tuple[list[QueryLog], int]:
        """Return paginated query history and total count."""
        base = select(QueryLog)
        count_stmt = select(func.count()).select_from(QueryLog)

        # This method builds its own filters rather than using _apply_filters, so
        # the authorization restriction has to be applied here too — and to BOTH
        # statements, or the total would count rows the caller cannot see.
        if corpus_ids is not None:
            base = base.where(QueryLog.corpus_id.in_(corpus_ids))
            count_stmt = count_stmt.where(QueryLog.corpus_id.in_(corpus_ids))
        if corpus_id is not None:
            base = base.where(QueryLog.corpus_id == corpus_id)
            count_stmt = count_stmt.where(QueryLog.corpus_id == corpus_id)
        if session_id is not None:
            base = base.where(QueryLog.session_id == session_id)
            count_stmt = count_stmt.where(QueryLog.session_id == session_id)

        total_result = await session.execute(count_stmt)
        total = total_result.scalar_one()

        items_stmt = base.order_by(QueryLog.created_at.desc()).offset(offset).limit(limit)
        items_result = await session.execute(items_stmt)
        items = list(items_result.scalars().all())

        return items, total

    async def get_confidence_distribution(
        self,
        session: AsyncSession,
        corpus_id: uuid.UUID | None = None,
        corpus_ids: Sequence[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Return confidence score histogram in 5 buckets."""
        buckets = [
            ("0.0-0.2", 0.0, 0.2),
            ("0.2-0.4", 0.2, 0.4),
            ("0.4-0.6", 0.4, 0.6),
            ("0.6-0.8", 0.6, 0.8),
            ("0.8-1.0", 0.8, 1.0),
        ]

        cases = []
        for label, low, high in buckets:
            if high == 1.0:
                cond = (QueryLog.confidence_score >= low) & (QueryLog.confidence_score <= high)
            else:
                cond = (QueryLog.confidence_score >= low) & (QueryLog.confidence_score < high)
            cases.append(func.count(case((cond, 1))).label(label.replace(".", "_")))

        stmt = select(*cases).select_from(QueryLog)
        stmt = self._apply_filters(
            stmt, corpus_id=corpus_id, corpus_ids=corpus_ids, start=start, end=end
        )
        result = await session.execute(stmt)
        row = result.one()

        return [{"range": label, "count": row[i]} for i, (label, _, _) in enumerate(buckets)]

    @staticmethod
    def _apply_filters(
        stmt: Any,
        *,
        corpus_id: uuid.UUID | None = None,
        corpus_ids: Sequence[uuid.UUID] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Any:
        """Apply common corpus / date range filters to a statement.

        `corpus_ids` is the authorization filter (the set of corpora the caller
        may see); `corpus_id` is the caller's own narrowing. They are ANDed, so a
        caller can never widen their scope by also passing corpus_id.
        """
        if corpus_ids is not None:
            stmt = stmt.where(QueryLog.corpus_id.in_(corpus_ids))
        if corpus_id is not None:
            stmt = stmt.where(QueryLog.corpus_id == corpus_id)
        if start is not None:
            stmt = stmt.where(QueryLog.created_at >= start)
        if end is not None:
            stmt = stmt.where(QueryLog.created_at <= end)
        return stmt
