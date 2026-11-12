"""Admin action endpoints -- eval, optimize, config, gap detection.

All endpoints require admin API key authentication.
Eval, optimize and gap triggers create their row first (status "queued"), dispatch
a Celery task that fills it in, and return 202 with the row's id to poll.
"""

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from callobuzz_rag.api.deps import require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.admin import (
    EvalRunDetail,
    EvalRunListResponse,
    EvalRunSummary,
    GapReportResponse,
    OptimizationRunDetail,
    OptimizationRunListResponse,
    OptimizationRunSummary,
    RetrievalConfigResponse,
    TriggerEvalRequest,
    TriggerEvalResponse,
    TriggerGapDetectionResponse,
    TriggerOptimizeRequest,
    TriggerOptimizeResponse,
    UpdateRetrievalConfigRequest,
)
from callobuzz_rag.cob.system import refuse_system_corpus
from callobuzz_rag.evaluation.optional import ADDON_MESSAGE, ragas_available

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# --- Private helper functions (mockable in tests) ---


async def _queue_run(
    request: Request,
    row: Any,
    corpus_id: UUID | None,
    task_name: str,
    task_kwargs: dict[str, Any],
    id_kwarg: str,
) -> UUID:
    """Create the run's row (status "queued"), then hand its id to the worker.

    The id returned to the caller is the row's own, so it can be polled on the
    list/detail endpoints. A failed dispatch marks the row failed and answers
    503 rather than leaving a run that will never start.
    """
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.worker.celery_app import celery_app

    async with request.app.state.session_factory() as session:
        if corpus_id is not None:
            target = await session.get(Corpus, corpus_id)
            if target is None:
                raise HTTPException(status_code=404, detail="Corpus not found")
            refuse_system_corpus(target)
        row.status = "queued"
        session.add(row)
        await session.commit()
        await session.refresh(row)
        run_id: UUID = row.id

        kwargs = {
            **task_kwargs,
            id_kwarg: str(run_id),
            "corpus_id": str(corpus_id) if corpus_id is not None else None,
        }
        try:
            celery_app.send_task(task_name, kwargs=kwargs)
        except Exception as exc:
            logger.error("Could not queue %s: %s", task_name, exc)
            row.status = "failed"
            row.error_message = "Could not queue the task: the task broker is unavailable"
            await session.commit()
            raise HTTPException(
                status_code=503, detail="Task broker unavailable; the run was not queued"
            ) from None

    logger.info("Queued %s: %s=%s corpus_id=%s", task_name, id_kwarg, run_id, corpus_id)
    return run_id


async def _dispatch_eval_task(
    request: Request, sample_size: int | None, corpus_id: UUID | None
) -> UUID:
    """Create a queued EvalRun and dispatch the evaluation task for it."""
    from callobuzz_rag.db.models.eval_run import EvalRun

    size = sample_size or 50
    row = EvalRun(triggered_by="manual", sample_size=size, corpus_id=corpus_id)
    return await _queue_run(
        request,
        row,
        corpus_id,
        "callobuzz_rag.run_evaluation",
        {"sample_size": size},
        "run_id",
    )


async def _dispatch_optimize_task(
    request: Request, n_trials: int | None, corpus_id: UUID | None
) -> UUID:
    """Create a queued OptimizationRun and dispatch the optimization task for it."""
    from callobuzz_rag.db.models.optimization_run import OptimizationRun

    trials = n_trials or 50
    row = OptimizationRun(n_trials=trials, corpus_id=corpus_id)
    return await _queue_run(
        request,
        row,
        corpus_id,
        "callobuzz_rag.run_optimization",
        {"n_trials": trials},
        "run_id",
    )


async def _dispatch_gap_detection_task(request: Request, corpus_id: UUID | None = None) -> UUID:
    """Create a queued GapReport and dispatch gap detection for it."""
    from callobuzz_rag.db.models.gap_report import GapReport

    row = GapReport(corpus_id=corpus_id, query_count=0, cluster_count=0)
    return await _queue_run(
        request,
        row,
        corpus_id,
        # The name the worker registers. The trigger used to send
        # "callobuzz_rag.run_gap_detection", which no worker handles.
        "callobuzz_rag.detect_knowledge_gaps",
        {},
        "report_id",
    )


async def _list_eval_runs(
    request: Request, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Fetch paginated eval runs from DB."""
    from sqlalchemy import func, select

    from callobuzz_rag.db.models.eval_run import EvalRun

    async with request.app.state.session_factory() as session:
        # Golden runs (conversation C7) have their own corpus-scoped routes.
        count_stmt = select(func.count(EvalRun.id)).where(EvalRun.kind == "traffic")
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = (
            select(EvalRun)
            .where(EvalRun.kind == "traffic")
            .order_by(EvalRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        runs = result.scalars().all()

        items = [
            {
                "id": run.id,
                "status": run.status,
                "sample_size": run.sample_size,
                "corpus_id": str(run.corpus_id) if run.corpus_id else None,
                "created_at": run.started_at,
                "completed_at": run.completed_at,
            }
            for run in runs
        ]
        return items, total


async def _get_eval_run(request: Request, run_id: UUID) -> dict[str, Any] | None:
    """Fetch a single eval run by ID."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.eval_run import EvalRun

    async with request.app.state.session_factory() as session:
        stmt = select(EvalRun).where(EvalRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            return None
        return {
            "id": run.id,
            "status": run.status,
            "sample_size": run.sample_size,
            "corpus_id": str(run.corpus_id) if run.corpus_id else None,
            "created_at": run.started_at,
            "completed_at": run.completed_at,
            "error_message": run.error_message,
            "metrics": run.metrics or {},
            "config_snapshot": run.config_snapshot or {},
        }


async def _list_optimization_runs(
    request: Request, offset: int, limit: int
) -> tuple[list[dict[str, Any]], int]:
    """Fetch paginated optimization runs from DB."""
    from sqlalchemy import func, select

    from callobuzz_rag.db.models.optimization_run import OptimizationRun

    async with request.app.state.session_factory() as session:
        count_stmt = select(func.count(OptimizationRun.id))
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = (
            select(OptimizationRun)
            .order_by(OptimizationRun.started_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.execute(stmt)
        runs = result.scalars().all()

        items = [
            {
                "id": run.id,
                "status": run.status,
                "n_trials": run.n_trials,
                "corpus_id": str(run.corpus_id) if run.corpus_id else None,
                "created_at": run.started_at,
                "completed_at": run.completed_at,
            }
            for run in runs
        ]
        return items, total


async def _get_optimization_run(request: Request, run_id: UUID) -> dict[str, Any] | None:
    """Fetch a single optimization run by ID."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.optimization_run import OptimizationRun

    async with request.app.state.session_factory() as session:
        stmt = select(OptimizationRun).where(OptimizationRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            return None
        return {
            "id": run.id,
            "status": run.status,
            "n_trials": run.n_trials,
            "corpus_id": str(run.corpus_id) if run.corpus_id else None,
            "created_at": run.started_at,
            "completed_at": run.completed_at,
            "error_message": run.error_message,
            "pareto_configs": run.pareto_configs or [],
            "best_config": run.best_config or {},
        }


def _retrieval_config_response(
    settings: Any, stored: dict[str, Any] | None
) -> RetrievalConfigResponse:
    """Merge stored overrides onto the engine defaults.

    A key that is absent or ``None`` has no override, so its default applies. ``source`` is
    ``"overrides"`` while at least one override is in effect, else ``"defaults"``.
    """
    overrides = {k: v for k, v in (stored or {}).items() if v is not None}
    return RetrievalConfigResponse(
        top_k_dense=overrides.get("top_k_dense", settings.retrieval_top_k_dense),
        top_k_sparse=overrides.get("top_k_sparse", settings.retrieval_top_k_sparse),
        rrf_k=overrides.get("rrf_k", settings.rrf_k),
        mmr_lambda=overrides.get("mmr_lambda", settings.mmr_lambda),
        temporal_decay_half_life_days=overrides.get(
            "temporal_decay_half_life_days", settings.temporal_decay_half_life_days
        ),
        rerank_top_n_input=overrides.get("rerank_top_n_input", settings.rerank_top_n_input),
        rerank_top_k_output=overrides.get("rerank_top_k_output", settings.rerank_top_k_output),
        source="overrides" if overrides else "defaults",
    )


async def _get_retrieval_config(request: Request) -> RetrievalConfigResponse:
    """Build retrieval config response from settings + DB overrides."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.config import Config

    async with request.app.state.session_factory() as session:
        stmt = select(Config).where(Config.key == "retrieval_overrides")
        result = await session.execute(stmt)
        config_row = result.scalar_one_or_none()
        stored = dict(config_row.value) if config_row and config_row.value else None

    return _retrieval_config_response(request.app.state.settings, stored)


async def _update_retrieval_config(
    request: Request, body: UpdateRetrievalConfigRequest
) -> RetrievalConfigResponse:
    """Update retrieval config in DB and return new state.

    Only fields present in the body change. A field sent as ``null`` removes its override,
    so the engine default applies again.
    """
    from sqlalchemy import select

    from callobuzz_rag.db.models.config import Config

    async with request.app.state.session_factory() as session:
        stmt = select(Config).where(Config.key == "retrieval_overrides")
        result = await session.execute(stmt)
        config_row = result.scalar_one_or_none()

        overrides: dict[str, Any] = {}
        if config_row and config_row.value:
            # Drop any null a previous version stored: null never was a usable value.
            overrides = {k: v for k, v in config_row.value.items() if v is not None}

        # Apply only provided fields; null clears the override.
        update_data = body.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is None:
                overrides.pop(key, None)
            else:
                overrides[key] = value

        if config_row:
            config_row.value = overrides
        else:
            session.add(Config(key="retrieval_overrides", value=overrides))

        await session.commit()

    # Fire CONFIG_CHANGED webhook
    dispatcher = getattr(request.app.state, "webhook_dispatcher", None)
    if dispatcher and update_data:
        try:
            from callobuzz_rag.core.events import WebhookEvent

            await dispatcher.dispatch(
                WebhookEvent.CONFIG_CHANGED,
                {"changed_keys": list(update_data.keys()), "source": "admin_api"},
            )
        except Exception:
            logger.warning("Webhook dispatch failed for CONFIG_CHANGED", exc_info=True)

    return _retrieval_config_response(request.app.state.settings, overrides)


async def _get_latest_gap_report(request: Request) -> dict[str, Any] | None:
    """Fetch the most recent gap report from DB."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.gap_report import GapReport

    async with request.app.state.session_factory() as session:
        stmt = select(GapReport).order_by(GapReport.started_at.desc()).limit(1)
        result = await session.execute(stmt)
        report = result.scalar_one_or_none()
        if report is None:
            return None
        return {
            "id": report.id,
            "corpus_id": str(report.corpus_id) if report.corpus_id else None,
            "status": report.status,
            "created_at": report.started_at,
            "topics": report.clusters or [],
            "total_queries_analyzed": report.query_count or 0,
            "low_confidence_count": 0,
        }


# --- Route Endpoints ---


@router.post("/eval/trigger", response_model=TriggerEvalResponse, status_code=202)
async def trigger_eval(
    request: Request, body: TriggerEvalRequest, _: None = Depends(require_admin)
) -> TriggerEvalResponse:
    """Queue a RAGAS evaluation run; poll GET /eval/runs/{eval_run_id} for the result.

    501 `evaluation_addon_missing` when the optional evaluation add-on is not installed.
    """
    if not ragas_available():
        raise APIError(501, ADDON_MESSAGE, "evaluation_addon_missing")
    run_id = await _dispatch_eval_task(request, body.sample_size, body.corpus_id)
    return TriggerEvalResponse(eval_run_id=str(run_id))


@router.get("/eval/runs")
async def list_eval_runs(
    request: Request, offset: int = 0, limit: int = 20, _: None = Depends(require_admin)
) -> EvalRunListResponse:
    """List evaluation runs with pagination."""
    items, total = await _list_eval_runs(request, offset, limit)
    return EvalRunListResponse(
        items=[EvalRunSummary(**item) for item in items], total=total, offset=offset, limit=limit
    )


@router.get("/eval/runs/{run_id}")
async def get_eval_run(
    request: Request, run_id: UUID, _: None = Depends(require_admin)
) -> EvalRunDetail:
    """Get a single evaluation run by ID."""
    data = await _get_eval_run(request, run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return EvalRunDetail(**data)


@router.post(
    "/optimize/trigger",
    response_model=TriggerOptimizeResponse,
    status_code=202,
)
async def trigger_optimize(
    request: Request, body: TriggerOptimizeRequest, _: None = Depends(require_admin)
) -> TriggerOptimizeResponse:
    """Queue a Bayesian optimization run; poll GET /optimize/runs/{optimization_run_id}.

    Its objective is the RAGAS faithfulness and answer-relevancy scores, so it answers 501
    `evaluation_addon_missing` when the optional evaluation add-on is not installed.
    """
    if not ragas_available():
        raise APIError(501, ADDON_MESSAGE, "evaluation_addon_missing")
    run_id = await _dispatch_optimize_task(request, body.n_trials, body.corpus_id)
    return TriggerOptimizeResponse(optimization_run_id=str(run_id))


@router.get("/optimize/runs")
async def list_optimization_runs(
    request: Request, offset: int = 0, limit: int = 20, _: None = Depends(require_admin)
) -> OptimizationRunListResponse:
    """List optimization runs with pagination."""
    items, total = await _list_optimization_runs(request, offset, limit)
    return OptimizationRunListResponse(
        items=[OptimizationRunSummary(**item) for item in items],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/optimize/runs/{run_id}")
async def get_optimization_run(
    request: Request, run_id: UUID, _: None = Depends(require_admin)
) -> OptimizationRunDetail:
    """Get a single optimization run by ID."""
    data = await _get_optimization_run(request, run_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Optimization run not found")
    return OptimizationRunDetail(**data)


@router.get("/config/retrieval")
async def get_retrieval_config(
    request: Request, _: None = Depends(require_admin)
) -> RetrievalConfigResponse:
    """Get current retrieval configuration (defaults + overrides)."""
    return await _get_retrieval_config(request)


@router.put("/config/retrieval")
async def update_retrieval_config(
    request: Request, body: UpdateRetrievalConfigRequest, _: None = Depends(require_admin)
) -> RetrievalConfigResponse:
    """Update retrieval configuration overrides."""
    return await _update_retrieval_config(request, body)


@router.get("/gaps")
async def get_gaps(request: Request, _: None = Depends(require_admin)) -> GapReportResponse:
    """Get the latest knowledge gap report."""
    data = await _get_latest_gap_report(request)
    if data is None:
        raise HTTPException(status_code=404, detail="No gap reports available")
    return GapReportResponse(**data)


@router.post("/gaps/trigger", status_code=202)
async def trigger_gap_detection(
    request: Request,
    corpus_id: UUID | None = Query(None, description="Scope to one corpus; omit for all"),
    _: None = Depends(require_admin),
) -> TriggerGapDetectionResponse:
    """Queue a knowledge gap detection run; poll GET /api/v1/analytics/gaps/{gap_report_id}."""
    report_id = await _dispatch_gap_detection_task(request, corpus_id)
    return TriggerGapDetectionResponse(gap_report_id=str(report_id))
