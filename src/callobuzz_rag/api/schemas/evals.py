"""Golden sets, evaluation runs, samples and comparisons for one knowledge base (C7)."""

from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput
from callobuzz_rag.conversation.schema import TemplateKey
from callobuzz_rag.conversation.types import LangKey
from callobuzz_rag.core.schemas import StrictRequest

Behaviour = Literal["answer", "decline", "handoff", "lead", "smalltalk", "no_answer"]
LabelName = Literal[
    "smalltalk",
    "business_question",
    "domain_explainer",
    "off_topic",
    "buying_intent",
    "human_request",
    "abuse",
    "other",
]
Origin = Literal["human", "ai_generated", "from_feedback"]
Difficulty = Literal["easy", "medium", "hard"]


class HistoryTurn(StrictRequest):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1, max_length=2000)


class GoldenPairFields(StrictRequest):
    """One question with its expectations and tags. At least one of `expected_answer` and
    `expected_behaviour`."""

    query: str = Field(..., min_length=1, max_length=5000)
    expected_answer: str | None = Field(None, min_length=1, max_length=10000)
    expected_behaviour: Behaviour | None = None
    expected_label: LabelName | None = None
    must_include: list[str] = Field(default_factory=list, max_length=10)
    must_cite: str | None = Field(None, max_length=500)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=4)
    language: LangKey | None = None
    page_url: str | None = Field(None, max_length=2048)
    office_hours: Literal["open", "closed"] | None = None
    origin: Origin = "human"
    difficulty: Difficulty | None = None
    topic: str | None = Field(None, max_length=60)

    @model_validator(mode="after")
    def _needs_an_expectation(self) -> "GoldenPairFields":
        if self.expected_answer is None and self.expected_behaviour is None:
            raise ValueError("give an expected answer, an expected behaviour, or both")
        return self


class GoldenSetCreate(StrictRequest):
    name: str = Field(..., min_length=1, max_length=120)
    pairs: list[GoldenPairFields] = Field(default_factory=list)


class GoldenSetUpdate(StrictRequest):
    name: str = Field(..., min_length=1, max_length=120)


class FromTemplateRequest(StrictRequest):
    template: TemplateKey | None = None


class AddPairsRequest(StrictRequest):
    pairs: list[GoldenPairFields] = Field(..., min_length=1, max_length=200)


class GoldenPairOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    set_id: UUID
    query: str
    expected_answer: str | None
    expected_behaviour: Behaviour | None
    expected_label: str | None
    must_include: list[str]
    must_cite: str | None
    history: list[dict[str, Any]]
    language: str | None
    page_url: str | None
    office_hours: str | None
    origin: Origin
    difficulty: Difficulty | None
    topic: str | None
    position: int


class LastRun(BaseModel):
    id: UUID
    status: str
    mode: str | None
    passed: int
    total: int
    finished_at: datetime | None


class GoldenSetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    corpus_id: UUID
    name: str
    source: Literal["owner", "template", "generated", "setup", "import"]
    is_default: bool
    pair_count: int = 0
    behaviour_count: int = 0
    last_run: LastRun | None = None
    created_at: datetime
    updated_at: datetime


class GoldenSetDetail(GoldenSetOut):
    pairs: list[GoldenPairOut]


class GoldenSetList(BaseModel):
    items: list[GoldenSetOut]
    count: int


class CriterionIn(StrictRequest):
    name: str = Field(..., min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9-]*$")
    definition: str = Field(..., min_length=1, max_length=500)
    threshold: float = Field(0.7, ge=0.0, le=1.0)
    enabled: bool = True


class CriterionUpdate(StrictRequest):
    definition: str | None = Field(None, min_length=1, max_length=500)
    threshold: float | None = Field(None, ge=0.0, le=1.0)
    enabled: bool | None = None


class CriterionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    definition: str
    threshold: float
    enabled: bool


class CriterionList(BaseModel):
    items: list[CriterionOut]


class EvalSettingsIn(StrictRequest):
    """Judge model (separate from the answer model) and per-metric pass thresholds."""

    judge_provider: str | None = Field(None, max_length=64)
    judge_model: str | None = Field(None, max_length=200)
    judge_api_key: str | None = Field(
        None,
        max_length=500,
        description='Plaintext; encrypted at rest. null keeps the stored key; "" removes it.',
    )
    judge_base_url: str | None = Field(None, max_length=500)
    thresholds: dict[str, float] = Field(default_factory=dict)

    @field_validator("judge_base_url")
    @classmethod
    def _http_url(cls, v: str | None) -> str | None:
        if not v:
            return None
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("judge_base_url must be an http(s) URL with a host")
        return v


class EvalSettingsOut(BaseModel):
    judge_provider: str | None
    judge_model: str | None
    judge_has_key: bool
    judge_base_url: str | None
    thresholds: dict[str, float]
    effective_thresholds: dict[str, float]


class AnswerModel(StrictRequest):
    provider: str = Field(..., min_length=1, max_length=64)
    model: str = Field(..., min_length=1, max_length=200)


class ExperimentSettings(StrictRequest):
    """A named settings snapshot to run a golden set against. Credentials are always the
    knowledge base's own; nothing here is saved to the knowledge base."""

    # "published", "draft", or a version number (a bool is not a version).
    assistant: Literal["published", "draft"] | StrictInt = "published"
    retrieval: RetrievalConfigInput | None = None
    answer_model: AnswerModel | None = None


class EstimateRequest(StrictRequest):
    set_id: UUID
    mode: Literal["fast", "full"] = "full"
    settings: ExperimentSettings = Field(default_factory=ExperimentSettings)


class EstimateOut(BaseModel):
    questions: int
    answer_usd: float
    judge_usd: float
    total_usd: float
    unpriced_models: list[str]
    assumptions: dict[str, int]
    answer_model: str
    judge_model: str


class RunCreate(EstimateRequest):
    name: str | None = Field(None, min_length=1, max_length=120)
    confirm_cost_usd: float | None = Field(None, ge=0)


class MetricSummary(BaseModel):
    metric: str
    n: int
    mean: float | None
    ci_low: float | None
    ci_high: float | None
    threshold: float | None
    pass_rate: float | None
    small_sample: bool


class TagBreakdown(BaseModel):
    tag: Literal["origin", "difficulty", "topic"]
    value: str
    n: int
    means: dict[str, float | None]
    pass_rate: float | None


class RunSummary(BaseModel):
    id: UUID
    kind: Literal["traffic", "golden"]
    name: str | None
    set_id: UUID | None
    mode: str | None
    target: str | None
    config_version: int | None
    status: str
    triggered_by: str
    sample_size: int
    totals: dict[str, Any] | None
    judge_model: str | None
    estimated_cost_usd: float | None
    actual_cost_usd: float | None
    # False when some model call had no known price: actual_cost_usd is then a floor.
    actual_cost_known: bool | None = None
    created_by: str | None
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None


class RunDetail(RunSummary):
    settings: dict[str, Any] | None
    metrics: list[MetricSummary]
    tags: list[TagBreakdown]
    warnings: list[str]


class RunList(BaseModel):
    items: list[RunSummary]
    count: int


class SampleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    golden_pair_id: UUID | None
    question: str
    expected_answer: str | None
    answer: str | None
    contexts: list[dict[str, Any]]
    scores: dict[str, float | None]
    reasons: dict[str, str]
    behaviour: dict[str, Any] | None
    tags: dict[str, Any]
    passed: bool | None
    latency_ms: int | None
    cost_usd: float | None
    rating: Literal["good", "acceptable", "poor"] | None
    rated_by: str | None
    error: str | None


class SampleList(BaseModel):
    items: list[SampleOut]
    count: int
    total: int


class RateSampleRequest(StrictRequest):
    rating: Literal["good", "acceptable", "poor"] | None


class CompareRequest(StrictRequest):
    run_ids: list[UUID] = Field(..., min_length=2, max_length=4)


class DeltaOut(BaseModel):
    n: int
    mean: float | None
    ci_low: float | None
    ci_high: float | None
    significant: bool


class MetricComparison(BaseModel):
    metric: str
    per_run: list[MetricSummary]
    deltas: list[DeltaOut | None]  # vs the baseline (index 0 is None)


class QuestionDelta(BaseModel):
    golden_pair_id: UUID
    question: str
    per_run_passed: list[bool | None]
    per_run_mean: list[float | None]
    change: list[Literal["baseline", "improved", "regressed", "unchanged", "missing"]]


class ComparisonOut(BaseModel):
    runs: list[RunSummary]
    metrics: list[MetricComparison]
    questions: list[QuestionDelta]
    warnings: list[str]
