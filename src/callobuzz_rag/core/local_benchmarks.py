"""The local CPU model benchmark the engine serves: which small model is good enough (PIP2).

`benchmarks/results/local-llm.json` is the file `python -m benchmarks.local_llm publish`
writes: one row per model, measured on this install's own frozen English, Hindi and Hinglish
answer items, on a CPU, with the free fact gate (nothing paid is ever called). The engine serves
it inside `GET /api/v1/models/answer-benchmarks` (field `local`), the dashboard shows it beside
the model pickers, and the setup wizard reads `best` for its default.

Nothing derived is trusted from the file: `normalise` computes `passed`, `fails`, `best` and
`default` from the measurements, so a rule change needs no new run. The passing rule (a small
model on a slow CPU is judged on being grounded first, then on being there at all):

- fact score (the answer kept every required fact and stated no wrong one) at least
  `fact_overall_min` overall and `fact_language_min` in each of English, Hindi and Hinglish;
- at most `refusal_answerable_max` of the answerable questions refused;
- median total time at most `total_p50_max_ms` and median first words at most `ttft_p50_max_ms`
  on the reference machine (recorded in `hardware`).

**best** = the passing model with the highest overall fact score (ties: the faster one). When
none passes, **default** is the fastest of the ones with the best fact score and `any_passed` is
false, so every screen says plainly "slow and less accurate: use it for a free trial tier only".
"""

import functools
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "results"
LOCAL_LLM_PATH = RESULTS_DIR / "local-llm.json"
LANGUAGES = ("en", "hi", "hi-Latn")
RULES: dict[str, float] = {
    "fact_overall_min": 0.80,
    "fact_language_min": 0.70,
    "refusal_answerable_max": 0.10,
    "total_p50_max_ms": 45_000,
    "ttft_p50_max_ms": 8_000,
}
Fail = Literal[
    "fact_overall", "fact_language", "refusals", "too_slow", "slow_first_words", "errors"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LocalHardware(_Strict):
    cpu: str | None
    cores: int | None
    ram_gb: float | None


class LocalFactScores(_Strict):
    overall: float | None
    en: float | None
    hi: float | None
    hi_latn: float | None = Field(alias="hi-Latn")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LocalModelRow(_Strict):
    local: Literal[True] = True
    model: str
    size_mb: int | None = Field(description="Download size of the model.")
    parameter_size: str | None
    quantization: str | None
    items: int
    answered: int
    errors: int
    fact: LocalFactScores = Field(description="Share of answers that kept every required fact.")
    refusal_on_answerable: float | None = Field(
        description="Share of answerable questions the model refused."
    )
    ttft_p50_ms: float | None
    ttft_p90_ms: float | None
    total_p50_ms: float | None
    total_p90_ms: float | None
    tokens_per_s: float | None
    passed: bool
    fails: list[Fail]


class LocalRules(_Strict):
    fact_overall_min: float
    fact_language_min: float
    refusal_answerable_max: float
    total_p50_max_ms: int
    ttft_p50_max_ms: int


class LocalBenchmarks(_Strict):
    schema_version: Literal[1]
    updated: str
    run: str
    method: str
    hardware: LocalHardware
    rules: LocalRules
    models: list[LocalModelRow]
    best: str | None = Field(description="The best passing model; None when none passes.")
    default: str | None = Field(description="What the setup wizard offers.")
    any_passed: bool


def _fails(row: dict[str, Any]) -> list[str]:
    fact = row["fact"]
    out: list[str] = []
    if row["answered"] < row["items"] or row["errors"] > 0:
        out.append("errors")
    if (fact.get("overall") or 0.0) < RULES["fact_overall_min"]:
        out.append("fact_overall")
    if any((fact.get(lang) or 0.0) < RULES["fact_language_min"] for lang in LANGUAGES):
        out.append("fact_language")
    if (row["refusal_on_answerable"] or 0.0) > RULES["refusal_answerable_max"]:
        out.append("refusals")
    total = row["total_p50_ms"]
    if total is None or total > RULES["total_p50_max_ms"]:
        out.append("too_slow")
    ttft = row["ttft_p50_ms"]
    if ttft is None or ttft > RULES["ttft_p50_max_ms"]:
        out.append("slow_first_words")
    return out


def _speed(row: dict[str, Any]) -> float:
    total = row["total_p50_ms"]
    return float(total) if total is not None else float("inf")


def _order(row: dict[str, Any]) -> tuple[bool, float, float, str]:
    return (not row["passed"], -(row["fact"].get("overall") or 0.0), _speed(row), row["model"])


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """The API document for a published file. Pure; the input is not changed."""
    rows: list[dict[str, Any]] = []
    for r in raw["models"]:
        row = {k: v for k, v in r.items() if k not in ("passed", "fails", "local")}
        row["fails"] = _fails(row)
        row["passed"] = not row["fails"]
        row["local"] = True
        rows.append(row)
    rows.sort(key=_order)
    passing = [r for r in rows if r["passed"]]
    best = passing[0]["model"] if passing else None
    default = best
    if default is None and rows:
        # Nothing passes: offer the fastest of the most accurate, and say so everywhere.
        top = max((r["fact"].get("overall") or 0.0) for r in rows)
        default = min(
            (r for r in rows if (r["fact"].get("overall") or 0.0) == top),
            key=lambda r: (_speed(r), r["model"]),
        )["model"]
    return {
        "schema_version": raw["schema_version"],
        "updated": raw["updated"],
        "run": raw["run"],
        "method": raw["method"],
        "hardware": raw["hardware"],
        "rules": {
            **RULES,
            "total_p50_max_ms": int(RULES["total_p50_max_ms"]),
            "ttft_p50_max_ms": int(RULES["ttft_p50_max_ms"]),
        },
        "models": rows,
        "best": best,
        "default": default,
        "any_passed": bool(passing),
    }


@functools.lru_cache(maxsize=1)
def load_local_benchmarks(path: Path = LOCAL_LLM_PATH) -> LocalBenchmarks | None:
    """The table, normalised and validated; None when the file is missing or unreadable."""
    try:
        return LocalBenchmarks.model_validate(normalise(json.loads(path.read_text("utf-8"))))
    except FileNotFoundError:
        logger.info("No local model benchmark file at %s", path)
    except (OSError, ValueError, KeyError):
        logger.warning("Unreadable local model benchmark file %s", path, exc_info=True)
    return None
