"""Benchmark results the engine serves: the fast-model table and a summary of the others.

**Fast models.** One file, read by everything that shows it.

`benchmarks/results/fast-models.json` holds one row per measured model (and OpenRouter host
pin) from the turn-label benchmark: accuracy, latency of the label call, real cost. The engine
serves it (`GET /api/v1/models/fast-benchmarks`), the dashboard renders it beside the fast-model
pickers, `benchmarks/tables.py` writes the tables in the README files and docs/benchmarks.html,
and the setup wizard suggests its recommended model.

The per-bar results, the tags and the recommendations are derived, never typed: `normalise`
computes them from the measurements and the bars, and a unit test keeps the file equal to its
own normalised form. The rules (design: fast and good):

- a model *passes* when it clears every accuracy bar and its fallback rate is within the bar;
- the default is, among passing models, the one with the highest business-question recall (the
  messages a shop cannot afford to lose), then the highest overall accuracy, then the lowest p90
  label latency;
- the budget pick is the cheapest passing model (per 1,000 labels);
- also named: the fastest and the most accurate passing model, and `cheapest_passing` (the budget
  pick under its older name).

**Summary.** `benchmarks/results/summary.json`: the headline tables of the other benchmarks
(retrieval on BEIR, real documents, time to first token), each with its date and a link to its
method, for the dashboard's Benchmarks page. Copied from the reports, which stay the record.

In Docker both files are copied to /app/benchmarks/results (see Dockerfile); in an image without
them the API answers with an empty table.
"""

import functools
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "results"
FAST_MODELS_PATH = RESULTS_DIR / "fast-models.json"
SUMMARY_PATH = RESULTS_DIR / "summary.json"
BAR_NAMES = ("overall", "per_language", "business_recall", "reply_language", "fallbacks")
Tag = Literal["recommended", "fastest", "cheapest", "most_accurate"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FastModelBars(_Strict):
    overall: float = Field(description="Minimum overall label accuracy.")
    per_language: float = Field(description="Minimum accuracy in each language.")
    business_recall: float = Field(description="Minimum share of business questions kept.")
    reply_language: float = Field(description="Minimum share of turns in the right language.")
    max_fallback_rate: float = Field(description="Maximum share of label calls that fell back.")


class FastModelLatency(_Strict):
    p50: float | None
    p90: float | None
    p99: float | None


class FastModelBarResults(_Strict):
    overall: bool
    per_language: bool
    business_recall: bool
    reply_language: bool
    fallbacks: bool


class FastModelResult(_Strict):
    key: str = Field(description="model, or model@host,host when an OpenRouter host is pinned.")
    model: str = Field(description="The model id as the engine's provider takes it.")
    provider: str = Field(description="LiteLLM provider id the run used, e.g. 'openrouter'.")
    provider_order: list[str] = Field(
        description="OpenRouter hosts pinned for the run (RAG_LLM_FAST_PROVIDER_ORDER); [] = none."
    )
    reasoning_effort: Literal["none", "low", "medium", "high"] | None = Field(
        description="Thinking effort the engine sent; null = none sent (a non-thinking model)."
    )
    thinking_control_verified: bool = Field(
        description="Whether the run confirmed thinking was off or at its minimum."
    )
    date: str
    report: str = Field(description="The run's report under benchmarks/results/.")
    turns: int
    model_turns: int = Field(description="Turns the rules did not decide, labelled by the model.")
    overall: float
    per_language: dict[str, float]
    business_recall: float
    reply_language: float
    fallback_rate: float
    latency_s: FastModelLatency = Field(description="The label call alone, timed in-process.")
    usd_per_1000_labels: float | None = Field(description="From the provider's billed cost.")
    cached_prompt_share: float | None = Field(
        description="Share of prompt tokens the provider served from its cache; null = unknown."
    )
    bars: FastModelBarResults
    passes: bool
    tags: list[Tag]


class FastModelRecommendations(_Strict):
    default: str | None
    fastest_passing: str | None
    budget: str | None = Field(description="The cheapest passing model: the budget option.")
    cheapest_passing: str | None
    most_accurate: str | None


class BenchmarkMethod(_Strict):
    summary: str
    link: str


class FastModelBenchmarks(_Strict):
    schema_version: Literal[1]
    updated: str
    method: BenchmarkMethod
    bars: FastModelBars
    models: list[FastModelResult]
    recommendations: FastModelRecommendations


def model_key(model: str, provider_order: list[str]) -> str:
    return f"{model}@{','.join(provider_order)}" if provider_order else model


def _bars(row: dict[str, Any], bars: dict[str, float]) -> dict[str, bool]:
    return {
        "overall": row["overall"] >= bars["overall"],
        "per_language": min(row["per_language"].values()) >= bars["per_language"],
        "business_recall": row["business_recall"] >= bars["business_recall"],
        "reply_language": row["reply_language"] >= bars["reply_language"],
        "fallbacks": row["fallback_rate"] <= bars["max_fallback_rate"],
    }


def _p90(row: dict[str, Any]) -> float:
    value = row["latency_s"].get("p90")
    return float(value) if value is not None else float("inf")


def normalise(doc: dict[str, Any]) -> dict[str, Any]:
    """`doc` with keys, bar results, tags and recommendations recomputed from the
    measurements, the rows ordered by p90 latency. Pure; the input is not changed."""
    bars = doc["bars"]
    rows = []
    for raw in doc["models"]:
        row = {k: v for k, v in raw.items() if k not in ("key", "bars", "passes", "tags")}
        result = _bars(row, bars)
        rows.append(
            {
                "key": model_key(row["model"], row["provider_order"]),
                **row,
                "bars": result,
                "passes": all(result.values()),
                "tags": [],
            }
        )
    rows.sort(key=lambda r: (_p90(r), r["key"]))
    passing = [r for r in rows if r["passes"]]
    fastest = min(passing, key=_p90, default=None)
    default = max(
        passing,
        key=lambda r: (r["business_recall"], r["overall"], -_p90(r)),
        default=None,
    )
    priced = [r for r in passing if r["usd_per_1000_labels"] is not None]
    cheapest = min(priced, key=lambda r: r["usd_per_1000_labels"], default=None)
    accurate = max(passing, key=lambda r: r["overall"], default=None)
    picks: tuple[tuple[str, dict[str, Any] | None], ...] = (
        ("recommended", default),
        ("fastest", fastest),
        ("cheapest", cheapest),
        ("most_accurate", accurate),
    )
    for tag, pick in picks:
        if pick is not None:
            pick["tags"].append(tag)
    return {
        **{k: v for k, v in doc.items() if k not in ("models", "recommendations")},
        "models": rows,
        "recommendations": {
            "default": default["key"] if default else None,
            "fastest_passing": fastest["key"] if fastest else None,
            "budget": cheapest["key"] if cheapest else None,
            "cheapest_passing": cheapest["key"] if cheapest else None,
            "most_accurate": accurate["key"] if accurate else None,
        },
    }


@functools.lru_cache(maxsize=1)
def load_fast_models(path: Path = FAST_MODELS_PATH) -> FastModelBenchmarks | None:
    """The table, validated; None when the file is missing or unreadable (logged)."""
    try:
        raw = json.loads(path.read_text("utf-8"))
        return FastModelBenchmarks.model_validate(raw)
    except FileNotFoundError:
        logger.info("No fast-model benchmark file at %s", path)
    except (OSError, ValueError):
        logger.warning("Unreadable fast-model benchmark file %s", path, exc_info=True)
    return None


class BenchmarkSummaryRow(_Strict):
    name: str
    values: list[str]
    highlight: bool = Field(False, description="The engine's default configuration.")


class BenchmarkSummarySection(_Strict):
    id: str
    title: str
    date: str
    method: str
    link: str
    columns: list[str]
    rows: list[BenchmarkSummaryRow]


class BenchmarkSummary(_Strict):
    schema_version: Literal[1]
    sections: list[BenchmarkSummarySection]


@functools.lru_cache(maxsize=1)
def load_summary(path: Path = SUMMARY_PATH) -> BenchmarkSummary | None:
    """The other benchmarks' headline tables; None when missing or unreadable (logged)."""
    try:
        return BenchmarkSummary.model_validate(json.loads(path.read_text("utf-8")))
    except FileNotFoundError:
        logger.info("No benchmark summary file at %s", path)
    except (OSError, ValueError):
        logger.warning("Unreadable benchmark summary file %s", path, exc_info=True)
    return None
