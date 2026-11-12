"""The answer-model benchmark the engine serves: which model writes the best grounded reply.

`benchmarks/results/answer-models.json` is the raw file the answer harness publishes
(`python -m benchmarks.answers publish`): one row per model and thinking level, measured on the
same 94 frozen items in English, Hindi and Hinglish, with a per-language slice for each row.
The engine serves it as `GET /api/v1/models/answer-benchmarks`, the dashboard renders it on the
Benchmarks page and beside the model pickers, and `benchmarks/tables.py` writes the tables in
the README files and docs/benchmarks.html.

Nothing derived is stored. `normalise` computes the bar results, the tags and the
recommendations from the measurements, so the file stays the measurements and a rule change
needs no re-publish. The rules:

- a model *meets the bar* when every item has an answer, thinking behaved as stated, its
  overall pass rate is at least `bars.pass_min` and it correctly declines at least
  `bars.no_answer_min` of the questions its sources cannot answer (a model that invents an
  answer to those is worse than a slower or dearer one);
- **recommended** (the default answer model): the highest pass rate among models that meet the
  bar, then the lowest cost;
- **Hindi and Hinglish**: a model is Hindi-ready when it meets the bar and, on the Hindi and
  Hinglish items together, passes at least `bars.hindi_pass_min`, declines at least
  `bars.hindi_no_answer_min` of the unanswerable ones and keeps the register right in at least
  `bars.hindi_register_min` of the judged replies. **Hindi default**: the Hindi-ready model with
  the highest Hindi pass rate, then the fastest first token. **Hindi budget**: the cheapest
  model that meets the bar, declines on Hindi and matches the Hindi default's pass rate with
  register at least `bars.hindi_budget_register_min`;
- also named: the fastest first token, the cheapest and the most accurate model that meets the
  bar, and the model with the best Hindi tone (highest judged naturalness on the Hindi and
  Hinglish items) whether or not it meets the bar, so a warm but unreliable model is visible.

Self-hostable means published open weights: a property of the model id, kept here as a short
prefix list (`OPEN_WEIGHT_PREFIXES`) and applied to the models of this table and of the
fast-model table.

In Docker the file is copied to /app/benchmarks/results (see Dockerfile); in an image without it
the API answers with an empty table.
"""

import functools
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.core.product import PUBLIC_DOCS_URL

logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).resolve().parents[3] / "benchmarks" / "results"
ANSWER_MODELS_PATH = RESULTS_DIR / "answer-models.json"
HINDI_ALL = "hindi_all"
LANGUAGES = ("en", "hi", "hi-Latn", HINDI_ALL)
BARS: dict[str, float] = {
    "pass_min": 0.80,
    "no_answer_min": 0.95,
    "hindi_pass_min": 0.85,
    "hindi_no_answer_min": 0.95,
    "hindi_register_min": 0.95,
    "hindi_budget_register_min": 0.90,
}
OPEN_WEIGHT_PREFIXES = (
    "google/gemma",
    "meta-llama/",
    "mistralai/mistral-small",
    "mistralai/mixtral",
    "qwen/",
    "openai/gpt-oss",
    "deepseek/",
)
METHOD_LINK = f"{PUBLIC_DOCS_URL}/benchmarks.html#answer-models"
Tag = Literal[
    "recommended",
    "hindi_recommended",
    "hindi_budget",
    "fastest",
    "cheapest",
    "most_accurate",
    "best_hindi_tone",
]
Fail = Literal["incomplete", "quality", "no_answer", "thinking"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerBars(_Strict):
    pass_min: float = Field(description="Minimum overall pass rate.")
    no_answer_min: float = Field(
        description="Minimum share of unanswerable questions correctly declined."
    )
    hindi_pass_min: float = Field(description="Minimum pass rate on Hindi and Hinglish together.")
    hindi_no_answer_min: float = Field(
        description="Minimum correct declines on the Hindi and Hinglish items."
    )
    hindi_register_min: float = Field(
        description="Minimum share of judged Hindi and Hinglish replies in the right register."
    )
    hindi_budget_register_min: float = Field(
        description="The same minimum for the Hindi budget pick."
    )


class AnswerLanguageSlice(_Strict):
    items: int
    passed: float | None
    refusal_correct_unanswerable: float | None
    unanswerable_items: int
    table_rate: float | None
    table_items: int
    ttft_p50_ms: float | None
    usd_per_1000_answers: float | None
    inr_per_1000_answers: float | None
    judged: int = Field(description="Replies the LLM judge graded; 0 for English.")
    fluency: float | None = Field(description="Judge score 1-5.")
    naturalness: float | None = Field(description="Judge score 1-5.")
    script_ok: float | None
    register_ok: float | None


class AnswerBarResults(_Strict):
    complete: bool
    quality: bool
    no_answer: bool
    thinking: bool


class AnswerModelResult(_Strict):
    key: str = Field(description="model[@hosts]|thinking, unique per row.")
    model: str = Field(description="The model id as the engine's provider takes it.")
    provider: str
    provider_order: list[str] = Field(description="OpenRouter hosts pinned for the run.")
    hosts: list[str] = Field(description="Hosts that actually served the answers.")
    thinking: str = Field(description="Thinking level sent: off, or the lowest the model allows.")
    thinking_verified: bool = Field(description="No reasoning tokens were seen when it was off.")
    items: int
    answered: int
    passed: float | None
    claim_check_pass: float | None
    hallucination_rate: float | None
    gold_recall: float | None
    citations_valid: float | None
    refusal_correct_answerable: float | None
    refusal_correct_unanswerable: float | None
    table_rate: float | None
    ttft_p50_ms: float | None
    ttft_p90_ms: float | None
    total_p50_ms: float | None
    usd_per_1000_answers: float | None
    inr_per_1000_answers: float | None
    judge_model: str | None
    by_language: dict[str, AnswerLanguageSlice]
    bars: AnswerBarResults
    fails: list[Fail] = Field(description="The overall bars this row misses; [] when it meets all.")
    passes: bool = Field(description="Meets the bar.")
    hindi_passes: bool = Field(description="Meets the bar and the Hindi and Hinglish bars.")
    self_hostable: bool
    tags: list[Tag]


class AnswerRecommendations(_Strict):
    default: str | None
    hindi_default: str | None
    hindi_budget: str | None
    fastest_passing: str | None
    cheapest_passing: str | None
    most_accurate: str | None
    best_hindi_tone: str | None


class AnswerItems(_Strict):
    total: int
    by_language: dict[str, int]
    unanswerable: int
    tables: int


class AnswerSpend(_Strict):
    answers_usd: float = Field(description="Generating every row's answers.")
    judge_usd: float = Field(description="The LLM judge's grading of Hindi and Hinglish replies.")
    total_usd: float


class AnswerBenchmarks(_Strict):
    schema_version: Literal[1]
    harness_version: str
    updated: str = Field(description="Date the rows were measured.")
    run: str
    items_sha256: str
    usd_inr_rate: float = Field(description="Rate the rupee figures were converted at.")
    method: str
    method_link: str
    items: AnswerItems
    spend: AnswerSpend
    bars: AnswerBars
    models: list[AnswerModelResult]
    recommendations: AnswerRecommendations
    self_hostable: list[str] = Field(
        description="Model ids with open weights, from this table and the fast-model table."
    )


def is_open_weights(model: str) -> bool:
    return model.startswith(OPEN_WEIGHT_PREFIXES)


def _slice(row: dict[str, Any], lang: str) -> dict[str, Any]:
    found: dict[str, Any] = row["by_language"][lang]
    return found


def _hindi_ok(row: dict[str, Any], bars: dict[str, float]) -> bool:
    s = _slice(row, HINDI_ALL)
    return (
        row["passes"]
        and (s["passed"] or 0) >= bars["hindi_pass_min"]
        and (s["refusal_correct_unanswerable"] or 0) >= bars["hindi_no_answer_min"]
        and (s["register_ok"] or 0) >= bars["hindi_register_min"]
    )


def _inr(row: dict[str, Any]) -> float:
    value = row["inr_per_1000_answers"]
    return float(value) if value is not None else float("inf")


def _ttft(row: dict[str, Any]) -> float:
    value = row["ttft_p50_ms"]
    return float(value) if value is not None else float("inf")


def _sort_key(row: dict[str, Any]) -> tuple[bool, float, float, str]:
    return (not row["passes"], -(row["passed"] or 0), _inr(row), row["key"])


def normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """The API document for the published file: bar results, tags and recommendations computed
    from the measurements, rows ordered best first. Pure; the input is not changed."""
    bars = dict(BARS)
    rows: list[dict[str, Any]] = []
    for r in raw["rows"]:
        sc, lat = r["scores"], r["latency"]
        unans = sc["refusal_correct_unanswerable"] or 0
        results = {
            "complete": r["answered"] >= r["items"] and r["errors"] == 0,
            "quality": (sc["passed"] or 0) >= bars["pass_min"],
            "no_answer": unans >= bars["no_answer_min"],
            "thinking": r["reasoning_violations"] == 0,
        }
        judge = r["judge"][0]["judge_model"] if r["judge"] else None
        rows.append(
            {
                "key": r["key"],
                "model": r["model"],
                "provider": r["provider"],
                "provider_order": r["provider_order"],
                "hosts": sorted(h for h in r["hosts"] if h != "unknown"),
                "thinking": r["variant"],
                "thinking_verified": results["thinking"],
                "items": r["items"],
                "answered": r["answered"],
                "passed": sc["passed"],
                "claim_check_pass": sc["claim_check_pass"],
                "hallucination_rate": sc["hallucination_rate"],
                "gold_recall": sc["gold_recall"],
                "citations_valid": sc["citations_valid"],
                "refusal_correct_answerable": sc["refusal_correct_answerable"],
                "refusal_correct_unanswerable": sc["refusal_correct_unanswerable"],
                "table_rate": sc["table_rate"],
                "ttft_p50_ms": lat["ttft_p50_ms"],
                "ttft_p90_ms": lat["ttft_p90_ms"],
                "total_p50_ms": lat["total_p50_ms"],
                "usd_per_1000_answers": r["usd_per_1000_answers"],
                "inr_per_1000_answers": r["inr_per_1000_answers"],
                "judge_model": judge,
                "by_language": r["by_language"],
                "bars": results,
                "fails": [k for k, ok in results.items() if not ok],
                "passes": all(results.values()),
                "hindi_passes": False,
                "self_hostable": is_open_weights(r["model"]),
                "tags": [],
            }
        )
    for row in rows:
        row["hindi_passes"] = _hindi_ok(row, bars)
    rows.sort(key=_sort_key)

    passing = [r for r in rows if r["passes"]]
    default = min(passing, key=_sort_key, default=None)
    fastest = min(passing, key=lambda r: (_ttft(r), r["key"]), default=None)
    priced = [r for r in passing if r["inr_per_1000_answers"] is not None]
    cheapest = min(priced, key=lambda r: (_inr(r), r["key"]), default=None)
    accurate = default
    hindi_ready = [r for r in rows if r["hindi_passes"]]
    hindi_default = min(
        hindi_ready,
        key=lambda r: (-(_slice(r, HINDI_ALL)["passed"] or 0), _ttft(r), r["key"]),
        default=None,
    )
    budget_pool = [
        r
        for r in passing
        if hindi_default is not None
        and (_slice(r, HINDI_ALL)["refusal_correct_unanswerable"] or 0)
        >= bars["hindi_no_answer_min"]
        and (_slice(r, HINDI_ALL)["passed"] or 0)
        >= (_slice(hindi_default, HINDI_ALL)["passed"] or 0)
        and (_slice(r, HINDI_ALL)["register_ok"] or 0) >= bars["hindi_budget_register_min"]
    ]
    hindi_budget = min(budget_pool, key=lambda r: (_inr(r), r["key"]), default=None)
    toned = [r for r in rows if _slice(r, HINDI_ALL)["naturalness"] is not None]
    tone = max(
        toned,
        key=lambda r: (_slice(r, HINDI_ALL)["naturalness"], -_inr(r), r["key"]),
        default=None,
    )
    picks: tuple[tuple[Tag, dict[str, Any] | None], ...] = (
        ("recommended", default),
        ("hindi_recommended", hindi_default),
        ("hindi_budget", hindi_budget),
        ("fastest", fastest),
        ("cheapest", cheapest),
        ("most_accurate", accurate),
        ("best_hindi_tone", tone),
    )
    for tag, pick in picks:
        if pick is not None:
            pick["tags"].append(tag)

    first = rows[0]["by_language"] if rows else {}
    judge_usd = sum(j["cost_usd"] for r in raw["rows"] for j in r["judge"])
    answers_usd = sum((r["usd_per_1000_answers"] or 0) * r["answered"] / 1000 for r in raw["rows"])
    match = re.search(r"\d{4}-\d{2}-\d{2}", raw["run"])
    return {
        "schema_version": raw["schema_version"],
        "harness_version": raw["harness_version"],
        "updated": match.group(0) if match else raw["updated"][:10],
        "run": raw["run"],
        "items_sha256": raw["items_sha256"],
        "usd_inr_rate": raw["usd_inr_rate"],
        "method": raw["method"],
        "method_link": METHOD_LINK,
        "items": {
            "total": rows[0]["items"] if rows else 0,
            "by_language": {k: first[k]["items"] for k in LANGUAGES[:3]} if first else {},
            "unanswerable": first[HINDI_ALL]["unanswerable_items"]
            + first["en"]["unanswerable_items"]
            if first
            else 0,
            "tables": first[HINDI_ALL]["table_items"] + first["en"]["table_items"] if first else 0,
        },
        "spend": {
            "answers_usd": round(answers_usd, 2),
            "judge_usd": round(judge_usd, 2),
            "total_usd": round(answers_usd + judge_usd, 2),
        },
        "bars": bars,
        "models": rows,
        "recommendations": {
            "default": default["key"] if default else None,
            "hindi_default": hindi_default["key"] if hindi_default else None,
            "hindi_budget": hindi_budget["key"] if hindi_budget else None,
            "fastest_passing": fastest["key"] if fastest else None,
            "cheapest_passing": cheapest["key"] if cheapest else None,
            "most_accurate": accurate["key"] if accurate else None,
            "best_hindi_tone": tone["key"] if tone else None,
        },
        "self_hostable": [],
    }


def _open_weight_ids(doc: dict[str, Any]) -> list[str]:
    ids = {r["model"] for r in doc["models"]}
    try:
        from callobuzz_rag.core.benchmark_results import load_fast_models

        fast = load_fast_models()
    except Exception:  # the fast table is optional here
        fast = None
    if fast is not None:
        ids |= {m.model for m in fast.models}
    return sorted(i for i in ids if is_open_weights(i))


@functools.lru_cache(maxsize=1)
def load_answer_models(path: Path = ANSWER_MODELS_PATH) -> AnswerBenchmarks | None:
    """The table, normalised and validated; None when the file is missing or unreadable (logged)."""
    try:
        doc = normalise(json.loads(path.read_text("utf-8")))
        doc["self_hostable"] = _open_weight_ids(doc)
        return AnswerBenchmarks.model_validate(doc)
    except FileNotFoundError:
        logger.info("No answer-model benchmark file at %s", path)
    except (OSError, ValueError, KeyError):
        logger.warning("Unreadable answer-model benchmark file %s", path, exc_info=True)
    return None
