"""Langfuse score reporter — pushes RAGAS scores to Langfuse traces.

Reads completed EvalRun records and pushes each metric as a Langfuse
score on the corresponding trace, making quality visible in Langfuse.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LangfuseScoreReporter:
    """Pushes evaluation scores to Langfuse traces."""

    def __init__(self, langfuse_client: Any) -> None:
        self._langfuse = langfuse_client

    async def push_query_score(
        self,
        trace_id: str,
        metric: str,
        score: float,
        comment: str | None = None,
    ) -> None:
        """Push a single metric score to a Langfuse trace."""
        try:
            kwargs: dict[str, Any] = {
                "trace_id": trace_id,
                "name": metric,
                "value": score,
            }
            if comment:
                kwargs["comment"] = comment
            self._langfuse.score(**kwargs)
            logger.debug(
                "Pushed score to Langfuse: trace=%s metric=%s score=%.3f",
                trace_id,
                metric,
                score,
            )
        except Exception:
            logger.warning(
                "Failed to push score to Langfuse: trace=%s metric=%s",
                trace_id,
                metric,
                exc_info=True,
            )

    async def push_eval_scores(
        self,
        eval_run_id: str,
        trace_id: str,
        metrics: dict[str, float],
    ) -> int:
        """Push all metrics from an eval run to a Langfuse trace.

        Returns the number of successfully pushed scores.
        """
        pushed = 0
        for metric, score in metrics.items():
            try:
                self._langfuse.score(
                    trace_id=trace_id,
                    name=metric,
                    value=score,
                    comment=f"eval_run={eval_run_id}",
                )
                pushed += 1
            except Exception:
                logger.warning(
                    "Failed to push %s score for eval_run=%s trace=%s",
                    metric,
                    eval_run_id,
                    trace_id,
                    exc_info=True,
                )
        return pushed

    async def push_cost_score(
        self,
        trace_id: str,
        cost_usd: float,
    ) -> None:
        """Push cost as a Langfuse score on a trace."""
        try:
            self._langfuse.score(
                trace_id=trace_id,
                name="cost_usd",
                value=cost_usd,
            )
        except Exception:
            logger.warning(
                "Failed to push cost score to Langfuse: trace=%s",
                trace_id,
                exc_info=True,
            )
