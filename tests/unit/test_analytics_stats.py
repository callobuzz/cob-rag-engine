"""Tests for QueryStatsService with mocked AsyncSession."""

from __future__ import annotations

import uuid
from collections import namedtuple
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.analytics.stats_service import QueryStatsService


@pytest.fixture
def svc() -> QueryStatsService:
    return QueryStatsService()


def _mock_session(scalar_one_value: object = None) -> AsyncMock:
    """Create a mock async session with execute returning scalar_one."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one.return_value = scalar_one_value
    session.execute.return_value = result
    return session


class TestGetQueryCount:
    @pytest.mark.asyncio
    async def test_returns_integer(self, svc: QueryStatsService) -> None:
        session = _mock_session(scalar_one_value=42)
        count = await svc.get_query_count(session)
        assert count == 42
        assert isinstance(count, int)

    @pytest.mark.asyncio
    async def test_with_filters(self, svc: QueryStatsService) -> None:
        session = _mock_session(scalar_one_value=5)
        corpus = uuid.uuid4()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 12, 31, tzinfo=UTC)
        count = await svc.get_query_count(session, corpus_id=corpus, start=start, end=end)
        assert count == 5


class TestGetTrends:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self, svc: QueryStatsService) -> None:
        TrendRow = namedtuple("TrendRow", ["bucket", "count", "avg_confidence", "avg_latency"])
        rows = [
            TrendRow(
                bucket=datetime(2026, 3, 1, tzinfo=UTC),
                count=10,
                avg_confidence=0.85,
                avg_latency=120.5,
            ),
        ]
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        session.execute.return_value = result

        trends = await svc.get_trends(session)
        assert isinstance(trends, list)
        assert len(trends) == 1
        assert trends[0]["bucket"] == datetime(2026, 3, 1, tzinfo=UTC)
        assert trends[0]["count"] == 10
        assert trends[0]["avg_confidence"] == 0.85
        assert trends[0]["avg_latency"] == 120.5

    @pytest.mark.asyncio
    async def test_null_averages(self, svc: QueryStatsService) -> None:
        TrendRow = namedtuple("TrendRow", ["bucket", "count", "avg_confidence", "avg_latency"])
        rows = [
            TrendRow(
                bucket=datetime(2026, 3, 1, tzinfo=UTC),
                count=3,
                avg_confidence=None,
                avg_latency=None,
            ),
        ]
        session = AsyncMock()
        result = MagicMock()
        result.all.return_value = rows
        session.execute.return_value = result

        trends = await svc.get_trends(session)
        assert trends[0]["avg_confidence"] is None
        assert trends[0]["avg_latency"] is None

    @pytest.mark.asyncio
    async def test_an_unsupported_granularity_never_reaches_sql(
        self, svc: QueryStatsService
    ) -> None:
        session = AsyncMock()
        with pytest.raises(ValueError, match="granularity"):
            await svc.get_trends(session, granularity="year")
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_granularity_is_a_bound_parameter(self, svc: QueryStatsService) -> None:
        from sqlalchemy.dialects import postgresql

        session = AsyncMock()
        session.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        await svc.get_trends(session, granularity="week")

        compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
        assert "week" not in str(compiled)
        assert "week" in compiled.params.values()


class TestGetQueryHistory:
    @pytest.mark.asyncio
    async def test_returns_tuple(self, svc: QueryStatsService) -> None:
        session = AsyncMock()

        # First execute: count query
        count_result = MagicMock()
        count_result.scalar_one.return_value = 1

        # Second execute: items query
        mock_item = MagicMock()
        mock_item.id = uuid.uuid4()
        mock_item.query_text = "test query"
        items_result = MagicMock()
        items_result.scalars.return_value.all.return_value = [mock_item]

        session.execute.side_effect = [count_result, items_result]

        items, total = await svc.get_query_history(session)
        assert isinstance(items, list)
        assert isinstance(total, int)
        assert total == 1
        assert len(items) == 1


class TestGetConfidenceDistribution:
    @pytest.mark.asyncio
    async def test_returns_bucket_list(self, svc: QueryStatsService) -> None:
        session = AsyncMock()
        result = MagicMock()
        # Row with 5 bucket counts, accessed by index
        row = (10, 20, 30, 25, 15)
        result.one.return_value = row
        session.execute.return_value = result

        dist = await svc.get_confidence_distribution(session)
        assert isinstance(dist, list)
        assert len(dist) == 5
        assert dist[0]["range"] == "0.0-0.2"
        assert dist[0]["count"] == 10
        assert dist[4]["range"] == "0.8-1.0"
        assert dist[4]["count"] == 15
