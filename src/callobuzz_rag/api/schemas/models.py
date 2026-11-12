"""Model catalog API schemas."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.answer_benchmarks import AnswerBenchmarks
from callobuzz_rag.core.benchmark_results import BenchmarkSummary, FastModelBenchmarks
from callobuzz_rag.core.local_benchmarks import LocalBenchmarks  # PIP2


class ModelEntry(BaseModel):
    """A single model in the catalog."""

    id: str = Field(..., description="LiteLLM model string.")
    name: str
    provider: str
    category: str = Field(..., description="free | budget | standard | premium")
    input_cost_per_1m: float
    output_cost_per_1m: float
    context_window: int
    supports_streaming: bool
    recommended: bool = False


class ModelCatalogResponse(BaseModel):
    """Response for model catalog listing."""

    models: list[ModelEntry]
    categories: list[str] = Field(
        default=["free", "budget", "standard", "premium"],
        description="Available pricing categories.",
    )
    count: int


class PresetListResponse(BaseModel):
    """Response for retrieval preset listing."""

    presets: dict[str, dict[str, Any]]
    count: int


class FastBenchmarksResponse(BaseModel):
    """The fast-model benchmark table (benchmarks/results/fast-models.json)."""

    model_config = ConfigDict(extra="forbid")

    available: bool = Field(description="False when this engine image has no results file.")
    benchmarks: FastModelBenchmarks | None


class AnswerBenchmarksResponse(BaseModel):
    """The answer-model benchmark (benchmarks/results/answer-models.json), normalised."""

    model_config = ConfigDict(extra="forbid")

    available: bool = Field(description="False when this engine image has no results file.")
    benchmarks: AnswerBenchmarks | None
    # PIP2 local
    local: LocalBenchmarks | None = Field(
        default=None,
        description=(
            "The small models that answer on a CPU (the free local tier), measured on the same "
            "English, Hindi and Hinglish items (benchmarks/results/local-llm.json). Every row is "
            "`local: true`; null when this engine image has no results file."
        ),
    )
    # /PIP2 local


class BenchmarkSummaryResponse(BaseModel):
    """The other benchmarks' headline tables (benchmarks/results/summary.json)."""

    model_config = ConfigDict(extra="forbid")

    available: bool = Field(description="False when this engine image has no results file.")
    summary: BenchmarkSummary | None
