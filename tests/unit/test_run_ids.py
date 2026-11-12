"""Background runs (eval, optimize, gap detection) are pollable by the id a trigger returns.

The triggers used to return a random uuid4 unrelated to any row, lists reported
`corpus_id: None` for everything, the optimizer never saved its run, and
`POST /analytics/gaps/run` answered 202 after running synchronously. Now the API
creates the row (status "queued"), the worker picks that same row up by id, and
lists and details report the row's real corpus.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from callobuzz_rag.db.models.eval_run import EvalRun
from callobuzz_rag.db.models.gap_report import GapReport
from callobuzz_rag.db.models.optimization_run import OptimizationRun


def _factory(session: Any) -> Any:
    @asynccontextmanager
    async def factory():  # type: ignore[no-untyped-def]
        yield session

    return factory


def _session(get_returns: Any = None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(return_value=get_returns)
    return session


# --- Worker side: the queued row is picked up, not duplicated ---


class TestEvaluationRunnerPicksUpQueuedRun:
    async def test_existing_row_is_reused_and_completed(self) -> None:
        from callobuzz_rag.evaluation.runner import EvaluationRunner

        queued = EvalRun(triggered_by="manual", status="queued", sample_size=10)
        queued.id = uuid.uuid4()
        session = _session(get_returns=queued)
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=empty)

        runner = EvaluationRunner(session_factory=_factory(session))
        run = await runner.run(sample_size=10, run_id=queued.id)

        assert run is queued
        assert run.status == "completed"
        session.add.assert_not_called()
        session.get.assert_awaited_once_with(EvalRun, queued.id)

    async def test_without_run_id_creates_row_with_corpus(self) -> None:
        from callobuzz_rag.evaluation.runner import EvaluationRunner

        session = _session()
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=empty)
        corpus_id = uuid.uuid4()

        runner = EvaluationRunner(session_factory=_factory(session))
        run = await runner.run(corpus_id=corpus_id, triggered_by="schedule")

        session.add.assert_called_once_with(run)
        assert run.corpus_id == corpus_id
        session.get.assert_not_awaited()


class TestSchedulerReturnsJsonSafeSummary:
    async def test_run_eval_async_returns_dict_with_row_id(self) -> None:
        from callobuzz_rag.evaluation import scheduler

        run = EvalRun(status="completed", sample_size=3, metrics={"f": {"score": 1.0}})
        run.id = uuid.uuid4()
        runner = MagicMock()
        runner.run = AsyncMock(return_value=run)
        run_id = uuid.uuid4()
        corpus_id = uuid.uuid4()
        with patch.object(scheduler, "_create_eval_runner", return_value=runner):
            out = await scheduler._run_eval_async(
                corpus_id=str(corpus_id), sample_size=3, run_id=str(run_id)
            )

        assert out == {
            "eval_run_id": str(run.id),
            "status": "completed",
            "sample_size": 3,
            "metrics": {"f": {"score": 1.0}},
        }
        kwargs = runner.run.await_args.kwargs
        assert kwargs["run_id"] == run_id  # string from Celery -> UUID
        assert kwargs["corpus_id"] == corpus_id
        assert kwargs["sample_size"] == 3
        assert kwargs["triggered_by"] == "manual"


class TestOptimizerPersistsItsRun:
    def _optimizer(self, session: Any, pairs: list[dict[str, Any]]) -> Any:
        from callobuzz_rag.analytics.optimizer import RetrievalOptimizer

        golden = AsyncMock()
        golden.list_pairs.return_value = pairs
        golden.list_by_corpus.return_value = pairs
        eval_runner = AsyncMock()
        eval_runner.evaluate_with_params.return_value = {
            "faithfulness": 0.8,
            "answer_relevancy": 0.7,
            "latency_p95": 0.3,
        }
        return RetrievalOptimizer(
            eval_runner=eval_runner, golden_manager=golden, session_factory=_factory(session)
        )

    async def test_queued_row_is_completed_and_saved(self) -> None:
        queued = OptimizationRun(n_trials=2, status="queued")
        queued.id = uuid.uuid4()
        session = _session(get_returns=queued)
        optimizer = self._optimizer(session, [{"question": "q", "answer": "a", "id": "1"}])

        run = await optimizer.optimize(n_trials=2, run_id=queued.id)

        assert run is queued
        assert run.status == "completed"
        assert run.best_config is not None
        session.add.assert_not_called()
        session.merge.assert_awaited_with(queued)

    async def test_no_golden_pairs_saves_the_failure(self) -> None:
        queued = OptimizationRun(n_trials=2, status="queued")
        queued.id = uuid.uuid4()
        session = _session(get_returns=queued)
        optimizer = self._optimizer(session, [])

        with pytest.raises(ValueError, match="No golden pairs"):
            await optimizer.optimize(n_trials=2, corpus_id=uuid.uuid4(), run_id=queued.id)

        assert queued.status == "failed"
        assert "golden pairs" in (queued.error_message or "")
        session.merge.assert_awaited_with(queued)

    async def test_without_run_id_the_run_is_added(self) -> None:
        session = _session()
        optimizer = self._optimizer(session, [{"question": "q", "answer": "a", "id": "1"}])
        corpus_id = uuid.uuid4()

        run = await optimizer.optimize(n_trials=2, corpus_id=corpus_id)

        session.add.assert_called_once_with(run)
        assert run.corpus_id == corpus_id


class TestGapDetectorPicksUpQueuedReport:
    async def test_existing_report_is_reused(self) -> None:
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.gaps.detector import GapDetector

        queued = GapReport(status="queued", query_count=0, cluster_count=0)
        queued.id = uuid.uuid4()
        session = _session(get_returns=queued)
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=empty)

        detector = GapDetector(
            session_factory=_factory(session),
            embedding_provider=AsyncMock(),
            settings=Settings(_env_file=None),
        )
        report = await detector.run(report_id=queued.id)

        assert report is queued
        assert report.status == "completed"
        session.add.assert_not_called()


class TestBeatTasksForwardIds:
    def test_gap_task_forwards_report_id(self) -> None:
        from callobuzz_rag.worker.beat_tasks import detect_knowledge_gaps_task

        report_id = uuid.uuid4()
        result = SimpleNamespace(id=report_id, status="completed", cluster_count=0, query_count=0)
        with patch(
            "callobuzz_rag.worker.beat_tasks._run_gap_detection_async",
            new=AsyncMock(return_value=result),
        ) as inner:
            out = detect_knowledge_gaps_task.run(report_id=str(report_id))

        assert inner.await_args.kwargs == {"corpus_id": None, "report_id": report_id}
        assert out["gap_report_id"] == str(report_id)

    def test_optimize_task_forwards_run_id_and_trials(self) -> None:
        from callobuzz_rag.worker.beat_tasks import run_optimization_task

        run_id = uuid.uuid4()
        result = SimpleNamespace(id=run_id, status="completed", n_trials=3, pareto_configs=[])
        with patch(
            "callobuzz_rag.worker.beat_tasks._run_optimization_async",
            new=AsyncMock(return_value=result),
        ) as inner:
            out = run_optimization_task.run(run_id=str(run_id), n_trials=3)

        assert inner.await_args.kwargs == {"corpus_id": None, "n_trials": 3, "run_id": run_id}
        assert out["optimization_run_id"] == str(run_id)


# --- API side: real corpus ids in list and detail ---


class TestAdminReportsRealCorpusId:
    def _request(self, session: Any) -> Any:
        return SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(session_factory=_factory(session)))
        )

    async def test_eval_detail_and_list(self) -> None:
        from callobuzz_rag.api.routes.admin import _get_eval_run, _list_eval_runs
        from callobuzz_rag.api.schemas.admin import EvalRunDetail

        corpus_id = uuid.uuid4()
        run = EvalRun(
            status="completed",
            sample_size=5,
            corpus_id=corpus_id,
            metrics={"faithfulness": {"name": "faithfulness", "score": 0.9, "passed": True}},
        )
        run.id = uuid.uuid4()
        run.started_at = datetime.now(UTC)
        result = MagicMock()
        result.scalar_one_or_none.return_value = run
        result.scalar.return_value = 1
        result.scalars.return_value.all.return_value = [run]
        session = _session()
        session.execute = AsyncMock(return_value=result)

        detail = await _get_eval_run(self._request(session), run.id)
        assert detail is not None
        assert detail["corpus_id"] == str(corpus_id)
        # The runner stores metric dicts; the detail schema used to demand floats.
        assert EvalRunDetail(**detail).metrics["faithfulness"]["score"] == 0.9

        items, _total = await _list_eval_runs(self._request(session), 0, 10)
        assert items[0]["corpus_id"] == str(corpus_id)

    async def test_optimization_detail(self) -> None:
        from callobuzz_rag.api.routes.admin import _get_optimization_run

        corpus_id = uuid.uuid4()
        run = OptimizationRun(status="failed", n_trials=4, corpus_id=corpus_id, error_message="x")
        run.id = uuid.uuid4()
        run.started_at = datetime.now(UTC)
        result = MagicMock()
        result.scalar_one_or_none.return_value = run
        session = _session()
        session.execute = AsyncMock(return_value=result)

        detail = await _get_optimization_run(self._request(session), run.id)
        assert detail is not None
        assert detail["corpus_id"] == str(corpus_id)
        assert detail["error_message"] == "x"


# --- POST /analytics/gaps/run is queued, not synchronous ---


def _gaps_app(scope: Any, session: Any) -> FastAPI:
    from callobuzz_rag.api.deps import get_session, resolve_scope
    from callobuzz_rag.api.routes.gaps import router

    app = FastAPI()
    app.include_router(router)

    async def _get_session():  # type: ignore[no-untyped-def]
        yield session

    app.dependency_overrides[get_session] = _get_session
    app.dependency_overrides[resolve_scope] = lambda: scope
    return app


class TestGapsRunIsQueued:
    def test_returns_queued_report_with_pollable_id(self) -> None:
        from callobuzz_rag.api.deps import Scope

        report_id = uuid.uuid4()
        corpus_id = uuid.uuid4()
        queued = GapReport(status="queued", corpus_id=corpus_id, query_count=0, cluster_count=0)
        queued.id = report_id
        queued.started_at = datetime.now(UTC)
        session = _session(get_returns=queued)
        app = _gaps_app(Scope(is_admin=True), session)

        with patch(
            "callobuzz_rag.api.routes.admin._dispatch_gap_detection_task",
            new=AsyncMock(return_value=report_id),
        ) as dispatch:
            resp = TestClient(app).post(f"/api/v1/analytics/gaps/run?corpus_id={corpus_id}")

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["id"] == str(report_id)
        assert body["status"] == "queued"
        assert body["corpus_id"] == str(corpus_id)
        assert dispatch.await_args.args[1] == corpus_id

    def test_corpus_key_without_corpus_id_runs_on_its_own_corpus(self) -> None:
        from callobuzz_rag.api.deps import Scope

        own = uuid.uuid4()
        queued = GapReport(status="queued", corpus_id=own, query_count=0, cluster_count=0)
        queued.id = uuid.uuid4()
        queued.started_at = datetime.now(UTC)
        app = _gaps_app(Scope(is_admin=False, corpus_id=own), _session(get_returns=queued))
        with patch(
            "callobuzz_rag.api.routes.admin._dispatch_gap_detection_task",
            new=AsyncMock(return_value=queued.id),
        ) as dispatch:
            resp = TestClient(app).post("/api/v1/analytics/gaps/run")

        assert resp.status_code == 202, resp.text
        assert dispatch.await_args.args[1] == own

    def test_tenant_key_cannot_queue_a_global_run(self) -> None:
        from callobuzz_rag.api.deps import Scope

        scope = Scope(is_admin=False, tenant_id=uuid.uuid4())
        session = _session()
        owned = MagicMock()
        owned.scalars.return_value.all.return_value = [uuid.uuid4()]
        session.execute = AsyncMock(return_value=owned)
        app = _gaps_app(scope, session)
        with patch(
            "callobuzz_rag.api.routes.admin._dispatch_gap_detection_task", new=AsyncMock()
        ) as dispatch:
            resp = TestClient(app).post("/api/v1/analytics/gaps/run")

        assert resp.status_code == 400
        dispatch.assert_not_awaited()
