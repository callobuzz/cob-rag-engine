"""Unit tests for EvaluationRunner — mocks RAGAS and DB."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.evaluation.metrics import (
    ANSWER_RELEVANCY,
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    DEFAULT_THRESHOLDS,
    FAITHFULNESS,
)
from callobuzz_rag.evaluation.runner import EvaluationRunner


def _make_query_log(
    query_text: str | None = "What is RAG?",
    response: str | None = "RAG is retrieval-augmented generation.",
    chunk_ids: list[uuid.UUID] | None = None,
) -> MagicMock:
    """Create a mock QueryLog row."""
    log = MagicMock()
    log.id = uuid.uuid4()
    log.query_text = query_text
    log.response = response
    log.retrieved_chunk_ids = chunk_ids if chunk_ids is not None else [uuid.uuid4(), uuid.uuid4()]
    log.created_at = datetime.now(UTC)
    return log


def _make_chunk(chunk_id: uuid.UUID, text: str = "Some chunk text.") -> MagicMock:
    """Create a mock Chunk row."""
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.text = text
    return chunk


class TestEvaluationRunnerBuildSamples:
    """Test dataset construction from query logs."""

    @pytest.mark.asyncio
    async def test_build_samples_from_query_logs(self) -> None:
        """Verify SingleTurnSample construction from query log + chunks."""
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        chunk_id_1 = uuid.uuid4()
        chunk_id_2 = uuid.uuid4()
        log = _make_query_log(
            query_text="What is X?",
            response="X is Y.",
            chunk_ids=[chunk_id_1, chunk_id_2],
        )
        chunks_by_id = {
            chunk_id_1: _make_chunk(chunk_id_1, "Chunk about X."),
            chunk_id_2: _make_chunk(chunk_id_2, "More about X."),
        }

        samples = runner._build_samples([log], chunks_by_id)

        assert len(samples) == 1
        sample = samples[0]
        assert sample.user_input == "What is X?"
        assert sample.response == "X is Y."
        assert len(sample.retrieved_contexts) == 2
        assert "Chunk about X." in sample.retrieved_contexts

    @pytest.mark.asyncio
    async def test_skips_logs_without_response(self) -> None:
        """Query logs missing a response are skipped."""
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        log = _make_query_log(response=None)

        samples = runner._build_samples([log], {})
        assert len(samples) == 0

    @pytest.mark.asyncio
    async def test_skips_logs_without_query_text(self) -> None:
        """Query logs missing query_text are skipped."""
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        log = _make_query_log(query_text=None)

        samples = runner._build_samples([log], {})
        assert len(samples) == 0

    @pytest.mark.asyncio
    async def test_skips_logs_without_chunks(self) -> None:
        """Query logs with no retrieved chunks are skipped."""
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        log = _make_query_log(chunk_ids=[])

        samples = runner._build_samples([log], {})
        assert len(samples) == 0


class TestEvaluationRunnerParseResults:
    """Test RAGAS result parsing into MetricResult list."""

    def test_parse_ragas_results(self) -> None:
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        # Simulate RAGAS evaluate() return — dict of metric_name -> score
        raw_scores = {
            FAITHFULNESS: 0.95,
            ANSWER_RELEVANCY: 0.88,
            CONTEXT_PRECISION: 0.82,
            CONTEXT_RECALL: 0.70,
        }

        results = runner._parse_results(raw_scores)

        assert len(results) == 4
        faith = next(r for r in results if r.name == FAITHFULNESS)
        assert faith.score == 0.95
        assert faith.passed is True

        recall = next(r for r in results if r.name == CONTEXT_RECALL)
        assert recall.score == 0.70
        assert recall.passed is False  # Below 0.75 threshold

    def test_parse_results_missing_metric(self) -> None:
        """Missing metrics in RAGAS output are skipped gracefully."""
        runner = EvaluationRunner.__new__(EvaluationRunner)
        runner._thresholds = DEFAULT_THRESHOLDS

        raw_scores = {FAITHFULNESS: 0.92}
        results = runner._parse_results(raw_scores)

        assert len(results) == 1
        assert results[0].name == FAITHFULNESS


def _mock_session_factory(
    mock_session: AsyncMock,
) -> MagicMock:
    """Create a mock that mimics async_sessionmaker behavior.

    async_sessionmaker() returns an async context manager directly (not a
    coroutine), so we use MagicMock with __aenter__/__aexit__.
    """
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory


class TestEvaluationRunnerRun:
    """Test the full run() orchestration with mocked DB and RAGAS."""

    @pytest.mark.asyncio
    async def test_run_creates_eval_run_record(self) -> None:
        """Full run stores metrics in eval_run table."""
        mock_session = AsyncMock()

        chunk_id = uuid.uuid4()
        log = _make_query_log(chunk_ids=[chunk_id])
        chunk = _make_chunk(chunk_id, "Context text.")

        # Mock DB queries: fetch query logs, then fetch chunks
        mock_result_logs = MagicMock()
        mock_result_logs.scalars.return_value.all.return_value = [log]
        mock_result_chunks = MagicMock()
        mock_result_chunks.scalars.return_value.all.return_value = [chunk]
        mock_result_refs = MagicMock()
        mock_result_refs.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[mock_result_logs, mock_result_chunks, mock_result_refs]
        )
        mock_session.commit = AsyncMock()

        # Mock RAGAS evaluate — return a plain dict (matches _run_ragas_evaluate signature)
        mock_ragas_result: dict[str, float] = {
            FAITHFULNESS: 0.95,
            ANSWER_RELEVANCY: 0.88,
            CONTEXT_PRECISION: 0.82,
            CONTEXT_RECALL: 0.78,
        }

        factory = _mock_session_factory(mock_session)
        runner = EvaluationRunner(session_factory=factory)

        with patch(
            "callobuzz_rag.evaluation.runner._run_ragas_evaluate",
            return_value=mock_ragas_result,
        ):
            eval_run = await runner.run(sample_size=50, triggered_by="manual")

        assert eval_run.status == "completed"
        assert eval_run.triggered_by == "manual"
        assert eval_run.metrics is not None
        assert FAITHFULNESS in eval_run.metrics
        mock_session.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_run_stores_one_sample_per_evaluated_query_log(self) -> None:
        """A traffic run is browsable question by question (conversation C7)."""
        from callobuzz_rag.db.models.eval_run_sample import EvalRunSample

        mock_session = AsyncMock()
        chunk_id = uuid.uuid4()
        log = _make_query_log(chunk_ids=[chunk_id])
        chunk = _make_chunk(chunk_id, "Context text.")
        logs = MagicMock()
        logs.scalars.return_value.all.return_value = [log]
        chunks = MagicMock()
        chunks.scalars.return_value.all.return_value = [chunk]
        refs = MagicMock()
        refs.all.return_value = []
        mock_session.execute = AsyncMock(side_effect=[logs, chunks, refs])
        mock_session.commit = AsyncMock()
        mock_session.add = MagicMock()

        def fake_ragas(samples_data, llm_config, settings, per_sample):
            per_sample.append({FAITHFULNESS: 0.95, ANSWER_RELEVANCY: 0.5, CONTEXT_PRECISION: None})
            return {FAITHFULNESS: 0.95, ANSWER_RELEVANCY: 0.5}

        runner = EvaluationRunner(session_factory=_mock_session_factory(mock_session))
        with patch("callobuzz_rag.evaluation.runner._run_ragas_evaluate", side_effect=fake_ragas):
            await runner.run(sample_size=50, triggered_by="manual")

        rows = [
            c.args[0]
            for c in mock_session.add.call_args_list
            if isinstance(c.args[0], EvalRunSample)
        ]
        assert len(rows) == 1
        assert rows[0].question == log.query_text and rows[0].query_log_id == log.id
        assert rows[0].scores[FAITHFULNESS] == 0.95 and rows[0].scores[CONTEXT_PRECISION] is None
        assert rows[0].passed is False  # answer relevancy 0.5 is under its 0.85 threshold
        assert rows[0].contexts == [{"text": "Context text."}]

    @pytest.mark.asyncio
    async def test_the_score_drop_baseline_is_a_traffic_run_of_the_same_corpus(self) -> None:
        mock_session = AsyncMock()
        previous = MagicMock()
        previous.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=previous)
        runner = EvaluationRunner(session_factory=_mock_session_factory(mock_session))
        corpus = uuid.uuid4()
        await runner._fetch_previous_scores(mock_session, uuid.uuid4(), corpus)
        sql = str(
            mock_session.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": False})
        )
        assert "eval_run.kind" in sql and "eval_run.corpus_id" in sql

    @pytest.mark.asyncio
    async def test_run_handles_no_query_logs(self) -> None:
        """Run with no query logs completes with zero sample_size."""
        mock_session = AsyncMock()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        factory = _mock_session_factory(mock_session)
        runner = EvaluationRunner(session_factory=factory)
        eval_run = await runner.run(sample_size=50, triggered_by="manual")

        assert eval_run.status == "completed"
        assert eval_run.sample_size == 0
        assert eval_run.metrics == {}

    @pytest.mark.asyncio
    async def test_run_handles_ragas_error(self) -> None:
        """RAGAS failure sets status=failed with error_message."""
        mock_session = AsyncMock()

        chunk_id = uuid.uuid4()
        log = _make_query_log(chunk_ids=[chunk_id])
        chunk = _make_chunk(chunk_id, "Context text.")

        mock_result_logs = MagicMock()
        mock_result_logs.scalars.return_value.all.return_value = [log]
        mock_result_chunks = MagicMock()
        mock_result_chunks.scalars.return_value.all.return_value = [chunk]
        mock_result_refs = MagicMock()
        mock_result_refs.all.return_value = []
        mock_session.execute = AsyncMock(
            side_effect=[mock_result_logs, mock_result_chunks, mock_result_refs]
        )
        mock_session.commit = AsyncMock()

        factory = _mock_session_factory(mock_session)
        runner = EvaluationRunner(session_factory=factory)

        with patch(
            "callobuzz_rag.evaluation.runner._run_ragas_evaluate",
            side_effect=RuntimeError("RAGAS crashed"),
        ):
            eval_run = await runner.run(sample_size=50, triggered_by="manual")

        assert eval_run.status == "failed"
        assert "RAGAS crashed" in eval_run.error_message
