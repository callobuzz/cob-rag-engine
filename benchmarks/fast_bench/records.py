"""What a fast-model run writes (pydantic, extra=forbid, every field required).

Files in a run directory (benchmarks/results/fast/<run>/):
- `run.json`       the manifest, checked on resume;
- `labels.jsonl`   one line per completed (model, turn), appended as each finishes;
- `errors.jsonl`   one line per failed attempt (the key is retried on the next resume);
- `progress.json`  done/total, spend and errors per model, rewritten after every turn;
- `scores.jsonl`   one line per (model, turn), written by `score` from labels.jsonl;
- `summary.json` / `summary.md`.

Only turns the rules do not decide are called (162 of 266); the rest are labelled by the rules at
scoring time, which is deterministic and free.
"""

from typing import Any, Literal

from benchmarks.answer_bench.records import (  # noqa: F401  (shared, re-exported)
    ModelProgress,
    Progress,
    append_jsonl,
    read_jsonl,
    write_json,
    write_jsonl,
)
from pydantic import BaseModel, ConfigDict, model_validator

CostSource = Literal["provider_usage", "generation_api", "price_list", "unknown"]
HostSource = Literal["openrouter_generation", "provider_id", "fake", "unknown"]
Source = Literal["model", "fallback", "error"]

RAIL_EMPTY = "empty_reply"
RAIL_COST = "cost_unknown"
RAIL_TOKENS = "tokens_missing"
RAIL_REASONING = "reasoning_when_off"
WARN_HOST = "host_unknown"
WARN_PIN = "host_not_pinned_host"
WARN_TRUNCATED = "truncated"

# Thinking levels at which a model is asked for no thinking at all: reasoning tokens must be 0.
NO_THINKING = ("off", "provider-default")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelRecord(_Strict):
    harness_version: str
    run: str
    timestamp: str
    model: str
    provider: str
    provider_order: list[str]
    host: str | None
    host_source: HostSource
    generation_id: str | None
    turn_id: str
    language: str
    business: str
    source: Source
    fallback_reason: str | None
    label: str  # what the label call decided; business_question for a fallback
    label_language: str | None
    reply_raw: str
    ms: int  # the label call alone, as the engine times it
    tokens_in: int | None
    tokens_out: int | None
    tokens_reasoning: int | None
    tokens_cached: int | None
    cost_usd: float | None
    cost_known: bool
    cost_source: CostSource
    finish_reason: str | None
    thinking_requested: str
    thinking_effective: str
    thinking_sent: bool
    thinking_minimum_only: bool
    reasoning_text: bool
    leaked: list[str]
    error: str | None
    error_kind: str | None
    retries: int
    request_params: dict[str, Any]
    request_params_hash: str
    rail_failures: list[str]
    warnings: list[str]

    @model_validator(mode="after")
    def _shape(self) -> "LabelRecord":
        if (self.error is None) != (self.source != "error"):
            raise ValueError("an error record has source 'error' and a completed one does not")
        if self.cost_known and self.cost_usd is None:
            raise ValueError("cost_known with no cost_usd")
        return self

    @property
    def key(self) -> tuple[str, str]:
        return (self.model, self.turn_id)


def thinking_ok(rec: dict[str, Any]) -> bool:
    """Whether this call ran at the thinking it claims: no reasoning at all when it was asked
    to be off; at the family's minimum, the call just has to have sent it (the tokens it
    thinks are recorded, not asserted)."""
    if rec["thinking_effective"] in NO_THINKING:
        return (
            rec["tokens_reasoning"] == 0
            and not rec["leaked"]
            and not rec["reasoning_text"]
            and rec["tokens_reasoning"] is not None
        )
    return bool(rec["thinking_minimum_only"]) and rec["thinking_sent"]


def rail_checks(rec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(rail failures, warnings) of a completed record. A rail failure makes the record unusable
    as a measurement and counts towards fail-fast."""
    rails: list[str] = []
    if not str(rec["reply_raw"]).strip() and rec["fallback_reason"] != "timeout":
        rails.append(RAIL_EMPTY)
    missing_tokens = rec["tokens_in"] is None or rec["tokens_out"] is None or not rec["tokens_in"]
    if missing_tokens and rec["fallback_reason"] != "timeout":
        rails.append(RAIL_TOKENS)
    if rec["cost_usd"] is None and rec["fallback_reason"] != "timeout":
        rails.append(RAIL_COST)
    if rec["fallback_reason"] != "timeout" and not thinking_ok(rec):
        rails.append(RAIL_REASONING)
    warnings: list[str] = []
    if rec["host"] is None:
        warnings.append(WARN_HOST)
    elif rec["provider_order"] and rec["host_source"] == "openrouter_generation":
        pinned = [h.split("/")[0].lower() for h in rec["provider_order"]]
        if not any(p in str(rec["host"]).lower() for p in pinned):
            warnings.append(WARN_PIN)
    if rec["finish_reason"] == "length":
        warnings.append(WARN_TRUNCATED)
    return rails, warnings


class RunManifest(_Strict):
    run: str
    harness_version: str
    created: str
    labels_sha256: str
    provider: str
    dry_run: bool
    smoke: bool
    models: list[str]
