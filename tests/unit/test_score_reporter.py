"""Tests for LangfuseScoreReporter."""

from unittest.mock import MagicMock

import pytest

from callobuzz_rag.evaluation.score_reporter import LangfuseScoreReporter


@pytest.fixture
def mock_langfuse() -> MagicMock:
    return MagicMock()


@pytest.fixture
def reporter(mock_langfuse: MagicMock) -> LangfuseScoreReporter:
    return LangfuseScoreReporter(langfuse_client=mock_langfuse)


class TestPushQueryScore:
    @pytest.mark.asyncio
    async def test_pushes_score_to_langfuse(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        await reporter.push_query_score(
            trace_id="trace-123",
            metric="faithfulness",
            score=0.95,
        )
        mock_langfuse.score.assert_called_once_with(
            trace_id="trace-123",
            name="faithfulness",
            value=0.95,
        )

    @pytest.mark.asyncio
    async def test_pushes_score_with_comment(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        await reporter.push_query_score(
            trace_id="trace-123",
            metric="relevancy",
            score=0.88,
            comment="good result",
        )
        mock_langfuse.score.assert_called_once_with(
            trace_id="trace-123",
            name="relevancy",
            value=0.88,
            comment="good result",
        )

    @pytest.mark.asyncio
    async def test_handles_langfuse_failure(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        mock_langfuse.score.side_effect = RuntimeError("connection failed")
        # Should not raise
        await reporter.push_query_score(
            trace_id="trace-123",
            metric="faithfulness",
            score=0.9,
        )


class TestPushEvalScores:
    @pytest.mark.asyncio
    async def test_pushes_all_metrics(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        metrics = {"faithfulness": 0.9, "relevancy": 0.85, "correctness": 0.92}
        pushed = await reporter.push_eval_scores(
            eval_run_id="eval-001",
            trace_id="trace-456",
            metrics=metrics,
        )
        assert pushed == 3
        assert mock_langfuse.score.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_count_of_pushed(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        call_count = 0

        def score_side_effect(**kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("fail on second")

        mock_langfuse.score.side_effect = score_side_effect
        metrics = {"faithfulness": 0.9, "relevancy": 0.85, "correctness": 0.92}
        pushed = await reporter.push_eval_scores(
            eval_run_id="eval-001",
            trace_id="trace-456",
            metrics=metrics,
        )
        assert pushed == 2  # 1st succeeds, 2nd fails, 3rd succeeds


class TestPushCostScore:
    @pytest.mark.asyncio
    async def test_pushes_cost(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        await reporter.push_cost_score(trace_id="trace-789", cost_usd=0.0042)
        mock_langfuse.score.assert_called_once_with(
            trace_id="trace-789",
            name="cost_usd",
            value=0.0042,
        )

    @pytest.mark.asyncio
    async def test_handles_failure(
        self, reporter: LangfuseScoreReporter, mock_langfuse: MagicMock
    ) -> None:
        mock_langfuse.score.side_effect = RuntimeError("connection failed")
        # Should not raise
        await reporter.push_cost_score(trace_id="trace-789", cost_usd=0.0042)
