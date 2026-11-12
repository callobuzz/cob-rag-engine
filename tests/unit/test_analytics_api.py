"""Tests for analytics API endpoints using TestClient."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from callobuzz_rag.api.routes.analytics import router


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the analytics router."""
    app = FastAPI()
    app.include_router(router)

    # Bypass auth: these routes resolve a Scope, so override that. Admin scope
    # keeps these tests focused on behaviour rather than authorization;
    # authorization itself is covered by test_tenant_scoping.py.
    from callobuzz_rag.api.deps import Scope, resolve_scope

    app.dependency_overrides[resolve_scope] = lambda: Scope(is_admin=True)

    # Mock session factory on app.state
    mock_session = AsyncMock()
    mock_factory = MagicMock()

    @asynccontextmanager
    async def _session_ctx():  # type: ignore[no-untyped-def]
        yield mock_session

    mock_factory.side_effect = _session_ctx
    app.state.session_factory = mock_factory
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_create_test_app())


class TestQueryCountEndpoint:
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_query_count")
    def test_returns_200(self, mock_count: AsyncMock, client: TestClient) -> None:
        mock_count.return_value = 42
        resp = client.get("/api/v1/analytics/queries/count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 42

    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_query_count")
    def test_with_corpus_filter(self, mock_count: AsyncMock, client: TestClient) -> None:
        mock_count.return_value = 5
        cid = str(uuid.uuid4())
        resp = client.get(f"/api/v1/analytics/queries/count?corpus_id={cid}")
        assert resp.status_code == 200
        assert resp.json()["count"] == 5


class TestTrendsEndpoint:
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_trends")
    def test_returns_200(self, mock_trends: AsyncMock, client: TestClient) -> None:
        mock_trends.return_value = [
            {
                "bucket": datetime(2026, 3, 1, tzinfo=UTC),
                "count": 10,
                "avg_confidence": 0.85,
                "avg_latency": 120.5,
            }
        ]
        resp = client.get("/api/v1/analytics/queries/trends")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert len(data["data"]) == 1
        assert data["data"][0]["count"] == 10

    @pytest.mark.parametrize("granularity", ["hour", "day", "week", "month"])
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_trends")
    def test_documented_granularities_pass_through(
        self, mock_trends: AsyncMock, granularity: str, client: TestClient
    ) -> None:
        mock_trends.return_value = []
        resp = client.get(f"/api/v1/analytics/queries/trends?granularity={granularity}")
        assert resp.status_code == 200, resp.text
        assert mock_trends.call_args.kwargs["granularity"] == granularity

    @pytest.mark.parametrize("granularity", ["year", "DAY", "day'); drop table x;--", ""])
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_trends")
    def test_anything_else_is_422(
        self, mock_trends: AsyncMock, granularity: str, client: TestClient
    ) -> None:
        resp = client.get("/api/v1/analytics/queries/trends", params={"granularity": granularity})
        assert resp.status_code == 422, resp.text
        assert "granularity" in resp.text
        mock_trends.assert_not_called()


class TestHistoryEndpoint:
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_query_history")
    def test_returns_200(self, mock_history: AsyncMock, client: TestClient) -> None:
        mock_item = MagicMock()
        mock_item.id = uuid.uuid4()
        mock_item.query_text = "hello"
        mock_item.response = "world"
        mock_item.confidence_score = 0.9
        mock_item.latency_ms = 100
        mock_item.corpus_id = None
        mock_item.session_id = None
        mock_item.created_at = datetime(2026, 3, 1, tzinfo=UTC)
        # Support model_validate(item) which calls from_attributes
        mock_item.__class__ = type(
            "MockQueryLog",
            (),
            {
                "__dict__": property(
                    lambda self: {
                        "id": self.id,
                        "query_text": self.query_text,
                        "response": self.response,
                        "confidence_score": self.confidence_score,
                        "latency_ms": self.latency_ms,
                        "corpus_id": self.corpus_id,
                        "session_id": self.session_id,
                        "created_at": self.created_at,
                    }
                )
            },
        )

        mock_history.return_value = ([mock_item], 1)
        resp = client.get("/api/v1/analytics/queries/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert data["total"] == 1
        assert data["offset"] == 0
        assert data["limit"] == 20


class TestConfidenceEndpoint:
    @patch("callobuzz_rag.analytics.stats_service.QueryStatsService.get_confidence_distribution")
    def test_returns_200(self, mock_dist: AsyncMock, client: TestClient) -> None:
        mock_dist.return_value = [
            {"range": "0.0-0.2", "count": 10},
            {"range": "0.2-0.4", "count": 20},
            {"range": "0.4-0.6", "count": 30},
            {"range": "0.6-0.8", "count": 25},
            {"range": "0.8-1.0", "count": 15},
        ]
        resp = client.get("/api/v1/analytics/queries/confidence")
        assert resp.status_code == 200
        data = resp.json()
        assert "buckets" in data
        assert len(data["buckets"]) == 5
