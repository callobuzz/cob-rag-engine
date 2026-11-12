"""Tests for admin action API endpoints using TestClient."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from callobuzz_rag.api.routes.admin import router

MODULE = "callobuzz_rag.api.routes.admin"


def _make_settings() -> MagicMock:
    """Create a mock Settings object with retrieval defaults."""
    settings = MagicMock()
    settings.retrieval_top_k_dense = 20
    settings.retrieval_top_k_sparse = 20
    settings.rrf_k = 60
    settings.mmr_lambda = 0.7
    settings.temporal_decay_half_life_days = 30.0
    settings.rerank_top_n_input = 50
    settings.rerank_top_k_output = 10
    return settings


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the admin router."""
    app = FastAPI()
    app.include_router(router)

    # Bypass auth: override require_admin dependency to be a no-op
    from callobuzz_rag.api.deps import require_admin

    app.dependency_overrides[require_admin] = lambda: None

    mock_session = AsyncMock()
    mock_factory = MagicMock()

    @asynccontextmanager
    async def _session_ctx():  # type: ignore[no-untyped-def]
        yield mock_session

    mock_factory.side_effect = _session_ctx
    app.state.session_factory = mock_factory
    app.state.settings = _make_settings()
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_create_test_app())


# --- Eval Trigger ---


class TestTriggerEval:
    @patch(f"{MODULE}._dispatch_eval_task")
    def test_returns_202(self, mock_dispatch: MagicMock, client: TestClient) -> None:
        mock_dispatch.return_value = "eval-run-001"
        resp = client.post("/api/v1/admin/eval/trigger", json={})
        assert resp.status_code == 202
        data = resp.json()
        assert data["eval_run_id"] == "eval-run-001"
        assert data["status"] == "queued"

    @patch(f"{MODULE}._dispatch_eval_task")
    def test_with_params(self, mock_dispatch: MagicMock, client: TestClient) -> None:
        mock_dispatch.return_value = "eval-run-002"
        corpus_id = uuid.uuid4()
        resp = client.post(
            "/api/v1/admin/eval/trigger",
            json={"sample_size": 100, "corpus_id": str(corpus_id)},
        )
        assert resp.status_code == 202
        _request, sample_size, passed_corpus = mock_dispatch.call_args.args
        assert (sample_size, passed_corpus) == (100, corpus_id)

    def test_non_uuid_corpus_id_is_422(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/eval/trigger", json={"corpus_id": "corp-1"})
        assert resp.status_code == 422


# --- Eval Runs List ---


class TestListEvalRuns:
    @patch(f"{MODULE}._list_eval_runs")
    def test_paginated(self, mock_list: AsyncMock, client: TestClient) -> None:
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        mock_list.return_value = (
            [
                {
                    "id": run_id,
                    "status": "completed",
                    "sample_size": 50,
                    "corpus_id": None,
                    "created_at": now,
                    "completed_at": now,
                }
            ],
            1,
        )
        resp = client.get("/api/v1/admin/eval/runs?offset=0&limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["offset"] == 0
        assert data["limit"] == 10
        assert len(data["items"]) == 1


class TestRunListsAreTyped:
    """OpenAPI documents the items of both run lists, and the payload is unchanged."""

    @pytest.mark.parametrize(
        ("path", "item_schema", "size_field"),
        [
            ("/api/v1/admin/eval/runs", "EvalRunSummary", "sample_size"),
            ("/api/v1/admin/optimize/runs", "OptimizationRunSummary", "n_trials"),
        ],
    )
    def test_openapi_names_the_item_model(
        self, path: str, item_schema: str, size_field: str
    ) -> None:
        spec = _create_test_app().openapi()
        ref = spec["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        page = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
        assert page["properties"]["items"]["items"]["$ref"].endswith(f"/{item_schema}")
        assert set(page["required"]) == {"items", "total", "offset", "limit"}
        assert size_field in spec["components"]["schemas"][item_schema]["properties"]

    @patch(f"{MODULE}._list_eval_runs")
    def test_eval_items_serialize_as_before(self, mock_list: AsyncMock, client: TestClient) -> None:
        now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
        run_id = uuid.uuid4()
        corpus_id = str(uuid.uuid4())
        mock_list.return_value = (
            [
                {
                    "id": run_id,
                    "status": "queued",
                    "sample_size": 50,
                    "corpus_id": corpus_id,
                    "created_at": now,
                    "completed_at": None,
                }
            ],
            1,
        )
        item = client.get("/api/v1/admin/eval/runs").json()["items"][0]
        assert item == {
            "id": str(run_id),
            "status": "queued",
            "sample_size": 50,
            "corpus_id": corpus_id,
            "created_at": "2026-09-30T12:00:00Z",
            "completed_at": None,
        }


# --- Eval Run Detail ---


class TestGetEvalRun:
    @patch(f"{MODULE}._get_eval_run")
    def test_by_id(self, mock_get: AsyncMock, client: TestClient) -> None:
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        mock_get.return_value = {
            "id": run_id,
            "status": "completed",
            "sample_size": 50,
            "corpus_id": None,
            "created_at": now,
            "completed_at": now,
            "metrics": {"faithfulness": 0.92},
            "config_snapshot": {"top_k": 20},
        }
        resp = client.get(f"/api/v1/admin/eval/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["metrics"]["faithfulness"] == 0.92

    @patch(f"{MODULE}._get_eval_run")
    def test_not_found(self, mock_get: AsyncMock, client: TestClient) -> None:
        mock_get.return_value = None
        run_id = uuid.uuid4()
        resp = client.get(f"/api/v1/admin/eval/runs/{run_id}")
        assert resp.status_code == 404


# --- Optimize Trigger ---


class TestTriggerOptimize:
    @patch(f"{MODULE}._dispatch_optimize_task")
    def test_returns_202(self, mock_dispatch: MagicMock, client: TestClient) -> None:
        mock_dispatch.return_value = "opt-run-001"
        resp = client.post("/api/v1/admin/optimize/trigger", json={})
        assert resp.status_code == 202
        data = resp.json()
        assert data["optimization_run_id"] == "opt-run-001"
        assert data["status"] == "queued"


# --- Optimization Runs List ---


class TestListOptimizationRuns:
    @patch(f"{MODULE}._list_optimization_runs")
    def test_list(self, mock_list: AsyncMock, client: TestClient) -> None:
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        mock_list.return_value = (
            [
                {
                    "id": run_id,
                    "status": "completed",
                    "n_trials": 30,
                    "corpus_id": None,
                    "created_at": now,
                    "completed_at": now,
                }
            ],
            1,
        )
        resp = client.get("/api/v1/admin/optimize/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["items"]) == 1


# --- Optimization Run Detail ---


class TestGetOptimizationRun:
    @patch(f"{MODULE}._get_optimization_run")
    def test_by_id(self, mock_get: AsyncMock, client: TestClient) -> None:
        now = datetime.now(UTC)
        run_id = uuid.uuid4()
        mock_get.return_value = {
            "id": run_id,
            "status": "completed",
            "n_trials": 30,
            "corpus_id": None,
            "created_at": now,
            "completed_at": now,
            "pareto_configs": [{"top_k": 15}],
            "best_config": {"top_k": 15},
        }
        resp = client.get(f"/api/v1/admin/optimize/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert len(data["pareto_configs"]) == 1

    @patch(f"{MODULE}._get_optimization_run")
    def test_not_found(self, mock_get: AsyncMock, client: TestClient) -> None:
        mock_get.return_value = None
        run_id = uuid.uuid4()
        resp = client.get(f"/api/v1/admin/optimize/runs/{run_id}")
        assert resp.status_code == 404


# --- Retrieval Config ---


class TestRetrievalConfig:
    @patch(f"{MODULE}._get_retrieval_config")
    def test_get(self, mock_get: AsyncMock, client: TestClient) -> None:
        from callobuzz_rag.api.schemas.admin import (
            RetrievalConfigResponse,
        )

        mock_get.return_value = RetrievalConfigResponse(
            top_k_dense=20,
            top_k_sparse=20,
            rrf_k=60,
            mmr_lambda=0.7,
            temporal_decay_half_life_days=30.0,
            rerank_top_n_input=50,
            rerank_top_k_output=10,
            source="defaults",
        )
        resp = client.get("/api/v1/admin/config/retrieval")
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_k_dense"] == 20
        assert data["source"] == "defaults"

    @patch(f"{MODULE}._update_retrieval_config")
    def test_update(self, mock_update: AsyncMock, client: TestClient) -> None:
        from callobuzz_rag.api.schemas.admin import (
            RetrievalConfigResponse,
        )

        mock_update.return_value = RetrievalConfigResponse(
            top_k_dense=30,
            top_k_sparse=20,
            rrf_k=60,
            mmr_lambda=0.7,
            temporal_decay_half_life_days=30.0,
            rerank_top_n_input=50,
            rerank_top_k_output=10,
            source="overrides",
        )
        resp = client.put(
            "/api/v1/admin/config/retrieval",
            json={"top_k_dense": 30},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["top_k_dense"] == 30
        assert data["source"] == "overrides"

    def test_invalid_mmr_lambda(self, client: TestClient) -> None:
        resp = client.put(
            "/api/v1/admin/config/retrieval",
            json={"mmr_lambda": 1.5},
        )
        assert resp.status_code == 422


def _app_with_config_store() -> tuple[FastAPI, dict[str, object]]:
    """Admin app whose session keeps the ``retrieval_overrides`` row in memory."""
    app = _create_test_app()
    store: dict[str, object] = {}

    session = AsyncMock()

    async def _execute(_stmt: object) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none.return_value = store.get("row")
        return result

    def _add(row: object) -> None:
        store["row"] = row

    session.execute.side_effect = _execute
    session.add = MagicMock(side_effect=_add)

    @asynccontextmanager
    async def _session_ctx():  # type: ignore[no-untyped-def]
        yield session

    app.state.session_factory = MagicMock(side_effect=_session_ctx)
    return app, store


class TestRetrievalConfigReset:
    """``null`` removes an engine-wide override so the default applies again."""

    def test_null_removes_one_override_and_keeps_the_others(self) -> None:
        app, store = _app_with_config_store()
        client = TestClient(app)

        resp = client.put("/api/v1/admin/config/retrieval", json={"top_k_dense": 30, "rrf_k": 90})
        assert resp.status_code == 200

        resp = client.put("/api/v1/admin/config/retrieval", json={"top_k_dense": None})
        assert resp.status_code == 200
        assert resp.json()["top_k_dense"] == 20
        assert resp.json()["rrf_k"] == 90
        assert resp.json()["source"] == "overrides"

        row = store["row"]
        assert row.value == {"rrf_k": 90}  # type: ignore[attr-defined]

        resp = client.get("/api/v1/admin/config/retrieval")
        assert resp.status_code == 200
        assert resp.json()["top_k_dense"] == 20
        assert resp.json()["rrf_k"] == 90

    def test_null_on_the_last_override_reports_defaults(self) -> None:
        app, _store = _app_with_config_store()
        client = TestClient(app)

        client.put("/api/v1/admin/config/retrieval", json={"mmr_lambda": 0.3})
        resp = client.put("/api/v1/admin/config/retrieval", json={"mmr_lambda": None})
        assert resp.status_code == 200
        assert resp.json()["mmr_lambda"] == 0.7
        assert resp.json()["source"] == "defaults"

        resp = client.get("/api/v1/admin/config/retrieval")
        assert resp.status_code == 200
        assert resp.json()["mmr_lambda"] == 0.7
        assert resp.json()["source"] == "defaults"

    def test_a_null_already_stored_reads_as_the_default(self) -> None:
        from callobuzz_rag.db.models.config import Config

        app, store = _app_with_config_store()
        store["row"] = Config(key="retrieval_overrides", value={"top_k_dense": None})
        client = TestClient(app)

        resp = client.get("/api/v1/admin/config/retrieval")
        assert resp.status_code == 200
        assert resp.json()["top_k_dense"] == 20
        assert resp.json()["source"] == "defaults"


# --- Gap Detection ---


class TestGapDetection:
    @patch(f"{MODULE}._get_latest_gap_report")
    def test_get_gaps(self, mock_get: AsyncMock, client: TestClient) -> None:
        now = datetime.now(UTC)
        report_id = uuid.uuid4()
        mock_get.return_value = {
            "id": report_id,
            "status": "completed",
            "created_at": now,
            "topics": [{"topic": "billing", "count": 5}],
            "total_queries_analyzed": 100,
            "low_confidence_count": 12,
        }
        resp = client.get("/api/v1/admin/gaps")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries_analyzed"] == 100
        assert len(data["topics"]) == 1

    @patch(f"{MODULE}._get_latest_gap_report")
    def test_none_available(self, mock_get: AsyncMock, client: TestClient) -> None:
        mock_get.return_value = None
        resp = client.get("/api/v1/admin/gaps")
        assert resp.status_code == 404

    @patch(f"{MODULE}._dispatch_gap_detection_task")
    def test_trigger(self, mock_dispatch: MagicMock, client: TestClient) -> None:
        mock_dispatch.return_value = "gap-run-001"
        resp = client.post("/api/v1/admin/gaps/trigger")
        assert resp.status_code == 202
        data = resp.json()
        assert data["gap_report_id"] == "gap-run-001"
        assert data["status"] == "queued"
        assert mock_dispatch.call_args.args[1] is None

    @patch(f"{MODULE}._dispatch_gap_detection_task")
    def test_trigger_scoped_to_corpus(self, mock_dispatch: MagicMock, client: TestClient) -> None:
        mock_dispatch.return_value = "gap-run-002"
        corpus_id = uuid.uuid4()
        resp = client.post(f"/api/v1/admin/gaps/trigger?corpus_id={corpus_id}")
        assert resp.status_code == 202
        assert mock_dispatch.call_args.args[1] == corpus_id
