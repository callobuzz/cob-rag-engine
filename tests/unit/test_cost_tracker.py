"""Tests for CostTracker, CostItem, and CostTrendPoint."""

import uuid
from collections import namedtuple
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.analytics.cost_tracker import (
    DEFAULT_MODEL_PRICING,
    CostItem,
    CostTracker,
    CostTrendPoint,
)


class TestCostItem:
    def test_creation(self) -> None:
        item = CostItem(
            model="openai/gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.00075,
        )
        assert item.model == "openai/gpt-4o"
        assert item.prompt_tokens == 100
        assert item.completion_tokens == 50
        assert item.cost_usd == 0.00075
        assert item.label == "generation"

    def test_total_tokens(self) -> None:
        item = CostItem(
            model="openai/gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
        )
        assert item.total_tokens == 150


class TestCostTrendPoint:
    def test_creation(self) -> None:
        point = CostTrendPoint(
            period=date(2026, 3, 1),
            total_cost_usd=1.50,
            total_queries=10,
            avg_cost_per_query=0.15,
        )
        assert point.period == date(2026, 3, 1)
        assert point.total_cost_usd == 1.50
        assert point.total_queries == 10
        assert point.avg_cost_per_query == 0.15


class TestDefaultPricing:
    def test_has_common_models(self) -> None:
        assert "openai/gpt-4o" in DEFAULT_MODEL_PRICING
        assert "anthropic/claude-sonnet-4-20250514" in DEFAULT_MODEL_PRICING
        assert "openai/gpt-4o-mini" in DEFAULT_MODEL_PRICING

    def test_pricing_structure(self) -> None:
        for model, pricing in DEFAULT_MODEL_PRICING.items():
            assert "prompt_per_1k" in pricing, f"{model} missing prompt_per_1k"
            assert "completion_per_1k" in pricing, f"{model} missing completion_per_1k"
            assert pricing["prompt_per_1k"] > 0
            assert pricing["completion_per_1k"] > 0


class TestEstimateCost:
    def test_known_model(self) -> None:
        tracker = CostTracker()
        cost = tracker.estimate_cost("openai/gpt-4o", prompt_tokens=1000, completion_tokens=500)
        expected = (1000 / 1000) * 0.0025 + (500 / 1000) * 0.01
        assert cost == pytest.approx(expected)

    def test_unknown_model_returns_zero(self) -> None:
        tracker = CostTracker()
        cost = tracker.estimate_cost("unknown/model", prompt_tokens=1000, completion_tokens=500)
        assert cost == 0.0

    def test_custom_pricing_override(self) -> None:
        overrides = {
            "custom/model": {
                "prompt_per_1k": 0.005,
                "completion_per_1k": 0.02,
            }
        }
        tracker = CostTracker(pricing_overrides=overrides)
        cost = tracker.estimate_cost("custom/model", prompt_tokens=2000, completion_tokens=1000)
        expected = (2000 / 1000) * 0.005 + (1000 / 1000) * 0.02
        assert cost == pytest.approx(expected)

    def test_zero_tokens(self) -> None:
        tracker = CostTracker()
        cost = tracker.estimate_cost("openai/gpt-4o", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0

    def test_build_cost_item(self) -> None:
        tracker = CostTracker()
        item = tracker.build_cost_item(
            model="openai/gpt-4o",
            prompt_tokens=1000,
            completion_tokens=500,
            label="rerank",
        )
        assert isinstance(item, CostItem)
        assert item.model == "openai/gpt-4o"
        assert item.prompt_tokens == 1000
        assert item.completion_tokens == 500
        assert item.label == "rerank"
        expected_cost = (1000 / 1000) * 0.0025 + (500 / 1000) * 0.01
        assert item.cost_usd == pytest.approx(expected_cost)


class TestRecordQueryCost:
    @pytest.mark.asyncio
    async def test_record_updates_token_usage(self) -> None:
        tracker = CostTracker()
        query_id = uuid.uuid4()

        mock_log = MagicMock()
        mock_log.token_usage = {"existing": "data"}

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_log

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        costs = [
            CostItem(
                model="openai/gpt-4o",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.00075,
            ),
        ]

        await tracker.record_query_cost(mock_session, query_id, costs)

        assert mock_log.token_usage["total_cost_usd"] == 0.00075
        assert len(mock_log.token_usage["cost_breakdown"]) == 1
        assert mock_log.token_usage["cost_breakdown"][0]["model"] == "openai/gpt-4o"
        assert mock_log.token_usage["existing"] == "data"
        mock_session.flush.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missing_query_log_is_noop(self) -> None:
        tracker = CostTracker()
        query_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        costs = [
            CostItem(
                model="openai/gpt-4o",
                prompt_tokens=100,
                completion_tokens=50,
                cost_usd=0.00075,
            ),
        ]

        await tracker.record_query_cost(mock_session, query_id, costs)

        mock_session.flush.assert_not_awaited()


class TestGetCostTrends:
    @pytest.mark.asyncio
    async def test_returns_trend_points(self) -> None:
        tracker = CostTracker()

        Row = namedtuple("Row", ["period", "total_cost", "query_count"])
        mock_rows = [
            Row(period=date(2026, 3, 1), total_cost=1.50, query_count=10),
            Row(period=date(2026, 3, 2), total_cost=2.00, query_count=8),
        ]

        mock_result = MagicMock()
        mock_result.all.return_value = mock_rows

        mock_session = AsyncMock()
        mock_session.execute.return_value = mock_result

        trends = await tracker.get_cost_trends(
            session=mock_session,
            start=date(2026, 3, 1),
            end=date(2026, 3, 7),
        )

        assert len(trends) == 2
        assert isinstance(trends[0], CostTrendPoint)
        assert trends[0].period == date(2026, 3, 1)
        assert trends[0].total_cost_usd == 1.50
        assert trends[0].total_queries == 10
        assert trends[0].avg_cost_per_query == pytest.approx(0.15)
        assert trends[1].total_cost_usd == 2.00
