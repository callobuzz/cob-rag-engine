"""Tests for Phase 3 multi-corpus compatibility (Epic 4-10)."""

import inspect
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _make_query_log(
    query_text: str = "test query",
    response: str = "test response",
    corpus_id: uuid.UUID | None = None,
    confidence_score: float | None = None,
    chunk_ids: list[uuid.UUID] | None = None,
) -> MagicMock:
    log = MagicMock()
    log.id = uuid.uuid4()
    log.query_text = query_text
    log.response = response
    log.corpus_id = corpus_id
    log.confidence_score = confidence_score
    log.retrieved_chunk_ids = chunk_ids or [uuid.uuid4()]
    log.created_at = datetime.now(UTC)
    return log


class TestEvalRunnerCorpusScoping:
    """EvaluationRunner filters query logs by corpus_id."""

    def test_run_accepts_corpus_id(self) -> None:
        """run() signature includes corpus_id parameter."""
        from callobuzz_rag.evaluation.runner import EvaluationRunner

        sig = inspect.signature(EvaluationRunner.run)
        assert "corpus_id" in sig.parameters

    def test_fetch_query_logs_accepts_corpus_id(self) -> None:
        """_fetch_query_logs signature includes corpus_id parameter."""
        from callobuzz_rag.evaluation.runner import EvaluationRunner

        sig = inspect.signature(EvaluationRunner._fetch_query_logs)
        assert "corpus_id" in sig.parameters


class TestGapDetectorCorpusScoping:
    """GapDetector filters low-confidence queries by corpus_id."""

    def test_run_accepts_corpus_id(self) -> None:
        from callobuzz_rag.gaps.detector import GapDetector

        sig = inspect.signature(GapDetector.run)
        assert "corpus_id" in sig.parameters

    def test_execute_accepts_corpus_id(self) -> None:
        from callobuzz_rag.gaps.detector import GapDetector

        sig = inspect.signature(GapDetector._execute)
        assert "corpus_id" in sig.parameters


class TestOptimizerCorpusScoping:
    """RetrievalOptimizer scopes by corpus_id."""

    def test_optimize_accepts_corpus_id(self) -> None:
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        sig = inspect.signature(RetrievalOptimizer.optimize)
        assert "corpus_id" in sig.parameters


class TestBeatTasksCorpusScoping:
    """Celery beat tasks extract and forward corpus_id from kwargs."""

    def test_run_evaluation_task_accepts_kwargs(self) -> None:
        from callobuzz_rag.worker.beat_tasks import run_evaluation_task

        sig = inspect.signature(run_evaluation_task)
        params = list(sig.parameters.values())
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    def test_detect_gaps_task_accepts_kwargs(self) -> None:
        from callobuzz_rag.worker.beat_tasks import detect_knowledge_gaps_task

        sig = inspect.signature(detect_knowledge_gaps_task)
        params = list(sig.parameters.values())
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    def test_run_optimization_task_accepts_kwargs(self) -> None:
        from callobuzz_rag.worker.beat_tasks import run_optimization_task

        sig = inspect.signature(run_optimization_task)
        params = list(sig.parameters.values())
        assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

    def test_eval_task_passes_corpus_id(self) -> None:
        from callobuzz_rag.worker.beat_tasks import run_evaluation_task

        with patch("callobuzz_rag.evaluation.scheduler.run_scheduled_eval") as mock_eval:
            mock_eval.return_value = {"status": "completed"}
            inner = (
                run_evaluation_task.__wrapped__
                if hasattr(run_evaluation_task, "__wrapped__")
                else run_evaluation_task
            )
            inner(corpus_id="abc-123", sample_size=20, run_id="run-1")
            mock_eval.assert_called_once_with(
                corpus_id="abc-123", sample_size=20, run_id="run-1", kind=None
            )

    def test_gap_detection_task_passes_corpus_id(self) -> None:
        from callobuzz_rag.worker.beat_tasks import detect_knowledge_gaps_task

        test_uuid = uuid.uuid4()
        with patch("callobuzz_rag.worker.beat_tasks._run_gap_detection_async") as mock_fn:
            mock_result = MagicMock()
            mock_result.status = "completed"
            mock_result.cluster_count = 0
            mock_result.query_count = 0
            mock_fn.return_value = mock_result
            with patch("callobuzz_rag.worker.beat_tasks.asyncio") as mock_asyncio:
                mock_asyncio.run.return_value = mock_result
                inner = (
                    detect_knowledge_gaps_task.__wrapped__
                    if hasattr(detect_knowledge_gaps_task, "__wrapped__")
                    else detect_knowledge_gaps_task
                )
                inner(corpus_id=str(test_uuid))
                mock_asyncio.run.assert_called_once()


class TestSchedulerCorpusScoping:
    """Eval scheduler passes corpus_id through to runner."""

    def test_run_scheduled_eval_accepts_corpus_id(self) -> None:
        from callobuzz_rag.evaluation.scheduler import run_scheduled_eval

        sig = inspect.signature(run_scheduled_eval)
        assert "corpus_id" in sig.parameters


class TestFeedbackCorpusScoping:
    """Feedback submit_feedback checks cross-corpus ownership."""

    @pytest.mark.asyncio
    async def test_feedback_rejects_cross_corpus(self) -> None:
        from callobuzz_rag.api.routes.feedback import submit_feedback

        mock_request = MagicMock()
        corpus = MagicMock()
        corpus.id = uuid.uuid4()
        mock_request.state.corpus = corpus
        mock_request.state.is_admin = False  # a corpus key, not admin
        mock_request.state.tenant = None

        mock_query_log = MagicMock()
        mock_query_log.corpus_id = uuid.uuid4()  # Different corpus

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_query_log)

        body = MagicMock()
        body.query_id = uuid.uuid4()
        body.rating = 5
        body.correction = None
        body.session_id = None
        body.trace_id = None

        with pytest.raises(HTTPException) as exc_info:
            await submit_feedback(body=body, request=mock_request, session=mock_session)
        assert exc_info.value.status_code == 403
        assert "does not belong" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_feedback_allows_same_corpus(self) -> None:
        from callobuzz_rag.api.routes.feedback import submit_feedback

        corpus_id = uuid.uuid4()
        mock_request = MagicMock()
        corpus = MagicMock()
        corpus.id = corpus_id
        mock_request.state.corpus = corpus
        mock_request.state.is_admin = False  # a corpus key, not admin
        mock_request.state.tenant = None

        mock_query_log = MagicMock()
        mock_query_log.corpus_id = corpus_id  # Same corpus

        mock_feedback = MagicMock()
        mock_feedback.id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_query_log)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock(side_effect=lambda f: setattr(f, "id", mock_feedback.id))

        body = MagicMock()
        body.query_id = uuid.uuid4()
        body.rating = 5
        body.correction = None
        body.session_id = None
        body.trace_id = None

        result = await submit_feedback(body=body, request=mock_request, session=mock_session)
        assert result.feedback_id is not None


class TestGapsRouteCorpusScoping:
    """Gaps run endpoint accepts corpus_id query param."""

    def test_run_gap_detection_accepts_corpus_id(self) -> None:
        from callobuzz_rag.api.routes.gaps import run_gap_detection

        sig = inspect.signature(run_gap_detection)
        assert "corpus_id" in sig.parameters


class TestGoldenDatasetManagerListPairs:
    """GoldenDatasetManager has a list_pairs fallback for no-corpus case."""

    def test_list_pairs_exists(self) -> None:
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        assert hasattr(GoldenDatasetManager, "list_pairs")
        sig = inspect.signature(GoldenDatasetManager.list_pairs)
        assert "limit" in sig.parameters
