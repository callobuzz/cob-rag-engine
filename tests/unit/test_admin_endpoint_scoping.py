"""Tests for Epic 4-09 (Admin Endpoint Scoping), updated 2026-08-06.

Two contracts live here now:

- Kernel-wide management (admin.py, webhooks.py) stays `require_admin`.
- Corpus-scoped surfaces (golden, analytics, gaps, feedback/stats) moved to
  `resolve_scope`, which accepts admin, tenant OR corpus keys and authorizes
  the corpus each request names. Those endpoints were unreachable for any
  consumer app before this; see the tenant-scoping completion plan.

These assert the DEPENDENCY only. That a non-owning tenant is actually refused
is proved in test_tenant_scoping.py, which exercises the resolution logic.
"""

import inspect
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.deps import require_admin, resolve_scope


def _has_dependency(endpoint_func, expected) -> bool:
    """True if the endpoint declares Depends(expected) in its signature."""
    sig = inspect.signature(endpoint_func)
    for param in sig.parameters.values():
        if param.default is not inspect.Parameter.empty:
            dep = param.default
            if hasattr(dep, "dependency") and dep.dependency is expected:
                return True
    return False


def _has_require_admin_dependency(endpoint_func) -> bool:
    """True if the endpoint is gated on the admin key alone."""
    return _has_dependency(endpoint_func, require_admin)


def _has_scope_dependency(endpoint_func) -> bool:
    """True if the endpoint resolves a Scope (admin, tenant or corpus key)."""
    return _has_dependency(endpoint_func, resolve_scope)


class TestAdminEndpointsProtected:
    """All admin/analytics/golden/webhook/gaps endpoints require require_admin."""

    def test_admin_trigger_eval_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import trigger_eval

        assert _has_require_admin_dependency(trigger_eval)

    def test_admin_list_eval_runs_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import list_eval_runs

        assert _has_require_admin_dependency(list_eval_runs)

    def test_admin_get_eval_run_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import get_eval_run

        assert _has_require_admin_dependency(get_eval_run)

    def test_admin_trigger_optimize_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import trigger_optimize

        assert _has_require_admin_dependency(trigger_optimize)

    def test_admin_list_optimization_runs_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import list_optimization_runs

        assert _has_require_admin_dependency(list_optimization_runs)

    def test_admin_get_optimization_run_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import get_optimization_run

        assert _has_require_admin_dependency(get_optimization_run)

    def test_admin_get_retrieval_config_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import get_retrieval_config

        assert _has_require_admin_dependency(get_retrieval_config)

    def test_admin_update_retrieval_config_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import update_retrieval_config

        assert _has_require_admin_dependency(update_retrieval_config)

    def test_admin_get_gaps_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import get_gaps

        assert _has_require_admin_dependency(get_gaps)

    def test_admin_trigger_gap_detection_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.admin import trigger_gap_detection

        assert _has_require_admin_dependency(trigger_gap_detection)


class TestAnalyticsEndpointsProtected:
    """All analytics endpoints resolve a scope (admin, tenant or corpus)."""

    def test_analytics_query_count_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.analytics import query_count

        assert _has_scope_dependency(query_count)
        assert not _has_require_admin_dependency(query_count)

    def test_analytics_query_trends_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.analytics import query_trends

        assert _has_scope_dependency(query_trends)
        assert not _has_require_admin_dependency(query_trends)

    def test_analytics_query_history_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.analytics import query_history

        assert _has_scope_dependency(query_history)
        assert not _has_require_admin_dependency(query_history)

    def test_analytics_confidence_distribution_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.analytics import confidence_distribution

        assert _has_scope_dependency(confidence_distribution)
        assert not _has_require_admin_dependency(confidence_distribution)


class TestGoldenEndpointsProtected:
    """All golden dataset endpoints resolve a scope."""

    def test_golden_create_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import create_golden_pair

        assert _has_scope_dependency(create_golden_pair)
        assert not _has_require_admin_dependency(create_golden_pair)

    def test_golden_list_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import list_golden_pairs

        assert _has_scope_dependency(list_golden_pairs)
        assert not _has_require_admin_dependency(list_golden_pairs)

    def test_golden_delete_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import delete_golden_pair

        assert _has_scope_dependency(delete_golden_pair)
        assert not _has_require_admin_dependency(delete_golden_pair)

    def test_golden_import_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import import_golden_pairs

        assert _has_scope_dependency(import_golden_pairs)
        assert not _has_require_admin_dependency(import_golden_pairs)

    def test_golden_export_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import export_golden_pairs

        assert _has_scope_dependency(export_golden_pairs)
        assert not _has_require_admin_dependency(export_golden_pairs)

    def test_golden_from_feedback_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.golden import create_from_feedback

        assert _has_scope_dependency(create_from_feedback)
        assert not _has_require_admin_dependency(create_from_feedback)


class TestWebhookEndpointsProtected:
    """All webhook endpoints require require_admin."""

    def test_webhook_create_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.webhooks import create_webhook

        assert _has_require_admin_dependency(create_webhook)

    def test_webhook_list_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.webhooks import list_webhooks

        assert _has_require_admin_dependency(list_webhooks)

    def test_webhook_delete_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.webhooks import delete_webhook

        assert _has_require_admin_dependency(delete_webhook)

    def test_webhook_test_requires_admin(self) -> None:
        from callobuzz_rag.api.routes.webhooks import test_webhook

        assert _has_require_admin_dependency(test_webhook)


class TestGapEndpointsProtected:
    """All gap detection endpoints resolve a scope."""

    def test_gaps_list_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.gaps import list_gap_reports

        assert _has_scope_dependency(list_gap_reports)
        assert not _has_require_admin_dependency(list_gap_reports)

    def test_gaps_get_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.gaps import get_gap_report

        assert _has_scope_dependency(get_gap_report)
        assert not _has_require_admin_dependency(get_gap_report)

    def test_gaps_run_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.gaps import run_gap_detection

        assert _has_scope_dependency(run_gap_detection)
        assert not _has_require_admin_dependency(run_gap_detection)


class TestFeedbackEndpointScoping:
    """Feedback POST remains open; /stats resolves a scope."""

    def test_feedback_submit_does_not_require_admin(self) -> None:
        from callobuzz_rag.api.routes.feedback import submit_feedback

        assert not _has_require_admin_dependency(submit_feedback)

    def test_feedback_stats_resolves_scope(self) -> None:
        from callobuzz_rag.api.routes.feedback import get_feedback_stats

        assert _has_scope_dependency(get_feedback_stats)
        assert not _has_require_admin_dependency(get_feedback_stats)


class TestCorpusIdPassedToTasks:
    """Admin triggers create the row first and hand its id and corpus to Celery.

    The id returned to the caller is the row's own id (it used to be a random
    uuid4 unrelated to anything), so the run can be polled.
    """

    RUN_ID = uuid.UUID("0b8e3e7e-2f1d-4a8c-9a55-3f3b4f7d9c11")

    def _request(self, *, corpus_exists: bool = True):
        session = AsyncMock()
        session.add = MagicMock()
        session.get = AsyncMock(return_value=MagicMock() if corpus_exists else None)

        async def refresh(row):
            row.id = self.RUN_ID

        session.refresh = AsyncMock(side_effect=refresh)

        @asynccontextmanager
        async def factory():
            yield session

        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(session_factory=factory))
        )
        return request, session

    async def test_eval_task_receives_run_id_and_corpus_id(self) -> None:
        from callobuzz_rag.api.routes.admin import _dispatch_eval_task

        corpus_id = uuid.uuid4()
        request, session = self._request()
        with patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery:
            run_id = await _dispatch_eval_task(request, 10, corpus_id)

        assert run_id == self.RUN_ID
        name = mock_celery.send_task.call_args.args[0]
        kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
        assert name == "callobuzz_rag.run_evaluation"
        assert kwargs == {
            "sample_size": 10,
            "run_id": str(self.RUN_ID),
            "corpus_id": str(corpus_id),
        }
        row = session.add.call_args.args[0]
        assert (row.status, row.corpus_id, row.sample_size) == ("queued", corpus_id, 10)

    async def test_optimize_task_receives_run_id_and_corpus_id(self) -> None:
        from callobuzz_rag.api.routes.admin import _dispatch_optimize_task

        corpus_id = uuid.uuid4()
        request, session = self._request()
        with patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery:
            run_id = await _dispatch_optimize_task(request, 7, corpus_id)

        assert run_id == self.RUN_ID
        assert mock_celery.send_task.call_args.args[0] == "callobuzz_rag.run_optimization"
        kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
        assert kwargs == {"n_trials": 7, "run_id": str(self.RUN_ID), "corpus_id": str(corpus_id)}
        row = session.add.call_args.args[0]
        assert (row.status, row.corpus_id, row.n_trials) == ("queued", corpus_id, 7)

    async def test_gap_detection_goes_to_the_registered_task(self) -> None:
        """It used to send "run_gap_detection", a name no worker registers."""
        from callobuzz_rag.api.routes.admin import _dispatch_gap_detection_task
        from callobuzz_rag.worker.beat_tasks import detect_knowledge_gaps_task

        corpus_id = uuid.uuid4()
        request, _session = self._request()
        with patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery:
            report_id = await _dispatch_gap_detection_task(request, corpus_id)

        assert report_id == self.RUN_ID
        assert mock_celery.send_task.call_args.args[0] == detect_knowledge_gaps_task.name
        kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
        assert kwargs == {"report_id": str(self.RUN_ID), "corpus_id": str(corpus_id)}

    async def test_eval_task_none_corpus_id(self) -> None:
        from callobuzz_rag.api.routes.admin import _dispatch_eval_task

        request, session = self._request()
        with patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery:
            await _dispatch_eval_task(request, None, None)

        kwargs = mock_celery.send_task.call_args.kwargs["kwargs"]
        assert kwargs["corpus_id"] is None
        assert kwargs["sample_size"] == 50
        session.get.assert_not_awaited()  # no corpus to look up

    async def test_unknown_corpus_is_404_and_nothing_queued(self) -> None:
        from fastapi import HTTPException

        from callobuzz_rag.api.routes.admin import _dispatch_eval_task

        request, session = self._request(corpus_exists=False)
        with (
            patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery,
            pytest.raises(HTTPException) as exc,
        ):
            await _dispatch_eval_task(request, 10, uuid.uuid4())

        assert exc.value.status_code == 404
        mock_celery.send_task.assert_not_called()
        session.add.assert_not_called()

    async def test_broker_down_marks_the_row_failed_and_503s(self) -> None:
        from fastapi import HTTPException

        from callobuzz_rag.api.routes.admin import _dispatch_optimize_task

        request, session = self._request()
        with (
            patch("callobuzz_rag.worker.celery_app.celery_app") as mock_celery,
            pytest.raises(HTTPException) as exc,
        ):
            mock_celery.send_task.side_effect = ConnectionError("redis down")
            await _dispatch_optimize_task(request, 5, None)

        assert exc.value.status_code == 503
        row = session.add.call_args.args[0]
        assert row.status == "failed"
        assert row.error_message
