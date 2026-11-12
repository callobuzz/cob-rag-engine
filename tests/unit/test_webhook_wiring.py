"""Tests for Epic 3-14: Webhook Event Wiring.

Verifies that EvaluationRunner, GapDetector, and RetrievalOptimizer
correctly accept webhook_dispatcher and fire events via the dispatcher.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.analytics.optimizer import RetrievalOptimizer
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.evaluation.runner import EvaluationRunner
from callobuzz_rag.gaps.detector import GapDetector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    return Settings(_env_file=None, jina_api_key="test", llm_api_key="test")


def _make_dispatcher() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# TestEvalScoreDropWiring
# ---------------------------------------------------------------------------


class TestEvalScoreDropWiring:
    """Verify EvaluationRunner fires EVAL_SCORE_DROP on significant metric drops."""

    @pytest.mark.asyncio
    async def test_fires_on_metric_drop(self) -> None:
        dispatcher = _make_dispatcher()
        runner = EvaluationRunner(
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
            score_drop_threshold=0.10,
        )

        await runner._check_and_fire_score_drop(
            eval_run_id="run-001",
            current_scores={"faithfulness": 0.70},
            previous_scores={"faithfulness": 0.85},
        )

        dispatcher.dispatch.assert_awaited_once()
        call_args = dispatcher.dispatch.call_args
        assert call_args[0][0] == WebhookEvent.EVAL_SCORE_DROP
        payload = call_args[0][1]
        assert payload["metric"] == "faithfulness"
        assert payload["old_score"] == 0.85
        assert payload["new_score"] == 0.70
        assert payload["eval_run_id"] == "run-001"
        assert payload["drop"] == 0.15

    @pytest.mark.asyncio
    async def test_no_fire_when_scores_stable(self) -> None:
        dispatcher = _make_dispatcher()
        runner = EvaluationRunner(
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
            score_drop_threshold=0.10,
        )

        await runner._check_and_fire_score_drop(
            eval_run_id="run-002",
            current_scores={"faithfulness": 0.84},
            previous_scores={"faithfulness": 0.85},
        )

        dispatcher.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_fire_when_dispatcher_is_none(self) -> None:
        runner = EvaluationRunner(
            session_factory=MagicMock(),
            webhook_dispatcher=None,
        )

        # Should not raise
        await runner._check_and_fire_score_drop(
            eval_run_id="run-003",
            current_scores={"faithfulness": 0.50},
            previous_scores={"faithfulness": 0.90},
        )


# ---------------------------------------------------------------------------
# TestKnowledgeGapWiring
# ---------------------------------------------------------------------------


class TestKnowledgeGapWiring:
    """Verify GapDetector fires KNOWLEDGE_GAP_DETECTED on new topics."""

    @pytest.mark.asyncio
    async def test_fires_on_new_topics(self) -> None:
        dispatcher = _make_dispatcher()
        settings = _make_settings()
        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
            webhook_dispatcher=dispatcher,
        )

        await detector._fire_gap_detected(
            gap_report_id="report-001",
            new_topics=["billing questions", "refund policy"],
        )

        dispatcher.dispatch.assert_awaited_once()
        call_args = dispatcher.dispatch.call_args
        assert call_args[0][0] == WebhookEvent.KNOWLEDGE_GAP_DETECTED
        payload = call_args[0][1]
        assert payload["new_topics"] == ["billing questions", "refund policy"]
        assert payload["gap_report_id"] == "report-001"
        assert payload["topic_count"] == 2

    @pytest.mark.asyncio
    async def test_no_fire_when_no_new_topics(self) -> None:
        dispatcher = _make_dispatcher()
        settings = _make_settings()
        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
            webhook_dispatcher=dispatcher,
        )

        await detector._fire_gap_detected(
            gap_report_id="report-002",
            new_topics=[],
        )

        dispatcher.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_fire_when_dispatcher_is_none(self) -> None:
        settings = _make_settings()
        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
            webhook_dispatcher=None,
        )

        # Should not raise
        await detector._fire_gap_detected(
            gap_report_id="report-003",
            new_topics=["some topic"],
        )


# ---------------------------------------------------------------------------
# TestOptimizationCompleteWiring
# ---------------------------------------------------------------------------


class TestOptimizationCompleteWiring:
    """Verify RetrievalOptimizer fires OPTIMIZATION_COMPLETE."""

    @pytest.mark.asyncio
    async def test_fires_on_optimization_complete(self) -> None:
        dispatcher = _make_dispatcher()
        optimizer = RetrievalOptimizer(
            eval_runner=MagicMock(),
            golden_manager=MagicMock(),
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
        )

        await optimizer._fire_optimization_complete(
            run_id="opt-001",
            best_config={"top_k": 10, "alpha": 0.7},
            pareto_size=5,
            n_trials=50,
        )

        dispatcher.dispatch.assert_awaited_once()
        call_args = dispatcher.dispatch.call_args
        assert call_args[0][0] == WebhookEvent.OPTIMIZATION_COMPLETE
        payload = call_args[0][1]
        assert payload["run_id"] == "opt-001"
        assert payload["best_config"] == {"top_k": 10, "alpha": 0.7}
        assert payload["pareto_size"] == 5
        assert payload["n_trials"] == 50

    @pytest.mark.asyncio
    async def test_no_fire_when_dispatcher_is_none(self) -> None:
        optimizer = RetrievalOptimizer(
            eval_runner=MagicMock(),
            golden_manager=MagicMock(),
            session_factory=MagicMock(),
            webhook_dispatcher=None,
        )

        # Should not raise
        await optimizer._fire_optimization_complete(
            run_id="opt-002",
            best_config={"top_k": 5},
            pareto_size=3,
            n_trials=20,
        )


# ---------------------------------------------------------------------------
# TestWorkerDispatcherWiring
# ---------------------------------------------------------------------------


class TestWorkerDispatcherWiring:
    """Verify services accept and store webhook_dispatcher."""

    def test_eval_runner_receives_dispatcher(self) -> None:
        dispatcher = _make_dispatcher()
        runner = EvaluationRunner(
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
        )
        assert runner._webhook_dispatcher is dispatcher

    def test_gap_detector_receives_dispatcher(self) -> None:
        dispatcher = _make_dispatcher()
        settings = _make_settings()
        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
            webhook_dispatcher=dispatcher,
        )
        assert detector._webhook_dispatcher is dispatcher

    def test_optimizer_receives_dispatcher(self) -> None:
        dispatcher = _make_dispatcher()
        optimizer = RetrievalOptimizer(
            eval_runner=MagicMock(),
            golden_manager=MagicMock(),
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
        )
        assert optimizer._webhook_dispatcher is dispatcher

    def test_services_work_without_dispatcher(self) -> None:
        settings = _make_settings()

        runner = EvaluationRunner(session_factory=MagicMock())
        assert runner._webhook_dispatcher is None

        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
        )
        assert detector._webhook_dispatcher is None

        optimizer = RetrievalOptimizer(
            eval_runner=MagicMock(),
            golden_manager=MagicMock(),
            session_factory=MagicMock(),
        )
        assert optimizer._webhook_dispatcher is None


# ---------------------------------------------------------------------------
# TestDispatcherErrorIsolation
# ---------------------------------------------------------------------------


class TestDispatcherErrorIsolation:
    """Verify dispatch errors are swallowed — never crash the caller."""

    @pytest.mark.asyncio
    async def test_dispatch_error_does_not_crash_eval_runner(self) -> None:
        dispatcher = _make_dispatcher()
        dispatcher.dispatch.side_effect = Exception("network error")
        runner = EvaluationRunner(
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
            score_drop_threshold=0.10,
        )

        # Should complete without raising
        await runner._check_and_fire_score_drop(
            eval_run_id="run-err",
            current_scores={"faithfulness": 0.50},
            previous_scores={"faithfulness": 0.90},
        )

    @pytest.mark.asyncio
    async def test_dispatch_error_does_not_crash_gap_detector(self) -> None:
        dispatcher = _make_dispatcher()
        dispatcher.dispatch.side_effect = Exception("network error")
        settings = _make_settings()
        detector = GapDetector(
            session_factory=MagicMock(),
            embedding_provider=AsyncMock(),
            settings=settings,
            webhook_dispatcher=dispatcher,
        )

        # Should complete without raising
        await detector._fire_gap_detected(
            gap_report_id="report-err",
            new_topics=["some topic"],
        )

    @pytest.mark.asyncio
    async def test_dispatch_error_does_not_crash_optimizer(self) -> None:
        dispatcher = _make_dispatcher()
        dispatcher.dispatch.side_effect = Exception("network error")
        optimizer = RetrievalOptimizer(
            eval_runner=MagicMock(),
            golden_manager=MagicMock(),
            session_factory=MagicMock(),
            webhook_dispatcher=dispatcher,
        )

        # Should complete without raising
        await optimizer._fire_optimization_complete(
            run_id="opt-err",
            best_config={"top_k": 10},
            pareto_size=3,
            n_trials=20,
        )
