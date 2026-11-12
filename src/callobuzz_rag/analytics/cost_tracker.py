"""Per-query cost tracking with model pricing estimation.

Estimates LLM cost from token counts using a configurable pricing table.
Stores cost breakdowns in query_log.token_usage JSONB column.
Provides aggregated cost trends for analytics dashboards.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


# Default pricing per 1K tokens (USD).
#
# A model absent from this table costs 0.0 and says so only at DEBUG level
# (`estimate_cost`), so a missing entry does not fail — it silently reports
# every query as free. The engine default therefore has to be here.
DEFAULT_MODEL_PRICING: dict[str, dict[str, float]] = {
    # The product default (`RAG_LLM_MODEL`). Rates read from OpenRouter's live
    # pricing API on 2026-08-11: $0.03 / $0.13 per 1M tokens.
    "openai/gpt-oss-20b": {
        "prompt_per_1k": 0.00003,
        "completion_per_1k": 0.00013,
    },
    "anthropic/claude-sonnet-4-20250514": {
        "prompt_per_1k": 0.003,
        "completion_per_1k": 0.015,
    },
    "anthropic/claude-haiku-3-20240307": {
        "prompt_per_1k": 0.00025,
        "completion_per_1k": 0.00125,
    },
    "openai/gpt-4o": {
        "prompt_per_1k": 0.0025,
        "completion_per_1k": 0.01,
    },
    "openai/gpt-4o-mini": {
        "prompt_per_1k": 0.00015,
        "completion_per_1k": 0.0006,
    },
    "openai/gpt-4-turbo": {
        "prompt_per_1k": 0.01,
        "completion_per_1k": 0.03,
    },
}


@dataclass(frozen=True, slots=True)
class CostItem:
    """Cost breakdown for a single LLM call within a query."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    label: str = "generation"

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class CostTrendPoint:
    """Aggregated cost data for a single time period."""

    period: date
    total_cost_usd: float
    total_queries: int
    avg_cost_per_query: float


class CostTracker:
    """Estimates and records per-query LLM costs."""

    def __init__(
        self,
        pricing_overrides: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._pricing = {**DEFAULT_MODEL_PRICING}
        if pricing_overrides:
            self._pricing.update(pricing_overrides)

    def has_price(self, model: str) -> bool:
        """Whether the table prices `model`. `estimate_cost` answers 0.0 for a model it does
        not know; callers that must tell "free" from "unknown" ask this first."""
        return model in self._pricing

    def estimate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """Estimate cost in USD for a single LLM call."""
        pricing = self._pricing.get(model)
        if pricing is None:
            logger.debug("No pricing data for model %s, cost=0.0", model)
            return 0.0

        prompt_cost = (prompt_tokens / 1000) * pricing["prompt_per_1k"]
        completion_cost = (completion_tokens / 1000) * pricing["completion_per_1k"]
        return prompt_cost + completion_cost

    def build_cost_item(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        label: str = "generation",
    ) -> CostItem:
        """Build a CostItem with estimated cost."""
        cost = self.estimate_cost(model, prompt_tokens, completion_tokens)
        return CostItem(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            label=label,
        )

    async def record_query_cost(
        self,
        session: Any,
        query_id: uuid.UUID,
        costs: list[CostItem],
    ) -> None:
        """Store cost breakdown in query_log.token_usage JSONB."""
        from sqlalchemy import select

        from callobuzz_rag.db.models.query_log import QueryLog

        try:
            stmt = select(QueryLog).where(QueryLog.id == query_id)
            result = await session.execute(stmt)
            log = result.scalar_one_or_none()

            if log is None:
                logger.warning("QueryLog %s not found, skipping cost record", query_id)
                return

            token_usage = dict(log.token_usage or {})
            total_cost = sum(c.cost_usd for c in costs)

            token_usage["cost_breakdown"] = [
                {
                    "model": c.model,
                    "prompt_tokens": c.prompt_tokens,
                    "completion_tokens": c.completion_tokens,
                    "cost_usd": c.cost_usd,
                    "label": c.label,
                }
                for c in costs
            ]
            token_usage["total_cost_usd"] = total_cost

            log.token_usage = token_usage
            await session.flush()

        except Exception as e:
            logger.error("Failed to record cost for query %s: %s", query_id, e)

    async def get_cost_trends(
        self,
        session: Any,
        start: date,
        end: date,
        granularity: str = "day",
    ) -> list[CostTrendPoint]:
        """Aggregate cost per time period from query_log."""
        from sqlalchemy import cast, func, select
        from sqlalchemy.types import Date, Float

        from callobuzz_rag.db.models.query_log import QueryLog

        try:
            cost_expr = cast(
                QueryLog.token_usage["total_cost_usd"].as_string(),
                Float,
            )

            if granularity == "week":
                period_expr = func.date_trunc("week", QueryLog.created_at)
            else:
                period_expr = func.date_trunc("day", QueryLog.created_at)

            period_col = cast(period_expr, Date).label("period")

            stmt = (
                select(
                    period_col,
                    func.coalesce(func.sum(cost_expr), 0.0).label("total_cost"),
                    func.count(QueryLog.id).label("query_count"),
                )
                .where(
                    QueryLog.created_at >= start,
                    QueryLog.created_at <= end,
                    QueryLog.token_usage.isnot(None),
                )
                .group_by(period_col)
                .order_by(period_col)
            )

            result = await session.execute(stmt)
            rows = result.all()

            return [
                CostTrendPoint(
                    period=row.period,
                    total_cost_usd=float(row.total_cost),
                    total_queries=int(row.query_count),
                    avg_cost_per_query=(
                        float(row.total_cost) / int(row.query_count) if row.query_count > 0 else 0.0
                    ),
                )
                for row in rows
            ]

        except Exception as e:
            logger.error("Failed to get cost trends: %s", e)
            return []
