"""Admin action API request/response schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from callobuzz_rag.core.schemas import StrictRequest


class TriggerEvalRequest(StrictRequest):
    """Request body for triggering an evaluation run."""

    sample_size: int | None = Field(None, gt=0)
    corpus_id: UUID | None = Field(None, description="Scope to one corpus; omit for all")


class TriggerEvalResponse(BaseModel):
    eval_run_id: str
    status: str = "queued"


class TriggerOptimizeRequest(StrictRequest):
    """Request body for triggering an optimization run."""

    n_trials: int | None = Field(None, ge=1)
    corpus_id: UUID | None = Field(None, description="Scope to one corpus; omit for all")


class TriggerOptimizeResponse(BaseModel):
    optimization_run_id: str
    status: str = "queued"


class UpdateRetrievalConfigRequest(StrictRequest):
    """All fields optional -- only provided fields are updated.

    A field sent as ``null`` removes its engine-wide override, so the default applies again.
    """

    top_k_dense: int | None = Field(None, ge=1, le=200)
    top_k_sparse: int | None = Field(None, ge=1, le=200)
    rrf_k: int | None = Field(None, ge=1, le=1000)
    mmr_lambda: float | None = Field(None, ge=0.0, le=1.0)
    temporal_decay_half_life_days: float | None = Field(None, gt=0.0)
    rerank_top_n_input: int | None = Field(None, ge=1, le=500)
    rerank_top_k_output: int | None = Field(None, ge=1, le=200)


class RetrievalConfigResponse(BaseModel):
    top_k_dense: int
    top_k_sparse: int
    rrf_k: int
    mmr_lambda: float
    temporal_decay_half_life_days: float
    rerank_top_n_input: int
    rerank_top_k_output: int
    source: str = Field("defaults")


class EvalRunSummary(BaseModel):
    id: UUID
    status: str
    sample_size: int | None = None
    corpus_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class EvalRunDetail(EvalRunSummary):
    error_message: str | None = None
    # {metric: {name, score, threshold, passed}} as the runner stores it.
    metrics: dict[str, Any] = Field(default_factory=dict)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)


class OptimizationRunSummary(BaseModel):
    id: UUID
    status: str
    n_trials: int | None = None
    corpus_id: str | None = None
    created_at: datetime
    completed_at: datetime | None = None


class OptimizationRunDetail(OptimizationRunSummary):
    error_message: str | None = None
    pareto_configs: list[dict[str, Any]] = Field(default_factory=list)
    best_config: dict[str, Any] = Field(default_factory=dict)


class GapReportResponse(BaseModel):
    id: UUID
    corpus_id: str | None = None
    status: str
    created_at: datetime
    topics: list[dict[str, Any]] = Field(default_factory=list)
    total_queries_analyzed: int = 0
    low_confidence_count: int = 0


class TriggerGapDetectionResponse(BaseModel):
    gap_report_id: str
    status: str = "queued"


class PaginatedResponse(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int


class EvalRunListResponse(PaginatedResponse):
    """A page of evaluation runs, newest first."""

    items: list[EvalRunSummary]


class OptimizationRunListResponse(PaginatedResponse):
    """A page of optimization runs, newest first."""

    items: list[OptimizationRunSummary]
