"""What a run writes, validated on write and on read (pydantic, extra=forbid, no defaults).

Every field is required: a record that lacks one fails validation, and the run stops rather than
paying for answers it cannot use. A field may be `null` only where the value can genuinely be
unknown (a provider that sends no usage), and the rails then flag it (`rail_failures`).

Files in a run directory (benchmarks/results/answers/<run>/):
- `run.json`       the manifest: item-set hash, harness version, settings; checked on resume;
- `answers.jsonl`  one line per completed (model, variant, item), appended as each finishes;
- `errors.jsonl`   one line per failed attempt (the key is retried on the next resume);
- `progress.json`  done/total, spend and errors per model, rewritten after every item;
- `scores.jsonl`   written by `score` from answers.jsonl, never by generation;
- `judge.jsonl`    written by `judge`, appended per item, resumable;
- `summary.json` / `summary.md`.
"""

import contextlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

CostSource = Literal["provider_usage", "generation_api", "price_list", "unknown"]
HostSource = Literal["openrouter_generation", "provider_id", "fake", "unknown"]

# Rail failures: the record is unusable as a measurement (and counts towards fail-fast).
RAIL_EMPTY = "empty_answer"
RAIL_TTFT = "ttft_missing"
RAIL_COST = "cost_unknown"
RAIL_TOKENS = "tokens_missing"
RAIL_REASONING = "reasoning_when_off"
RAIL_TRUNCATED = "truncated"
# Warnings: worth a look, not a failed measurement.
WARN_HOST = "host_unknown"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnswerRecord(_Strict):
    harness_version: str
    run: str
    timestamp: str
    model: str
    provider: str
    provider_order: list[str]
    host: str | None
    host_source: HostSource
    generation_id: str | None
    variant: str
    thinking_effective: str
    thinking_source: Literal["engine", "harness"]
    item_id: str
    item_hash: str
    language: str
    item_type: str
    question: str
    answer_raw: str
    answer_released: str
    route: Literal["grounded", "no_answer", "error"]
    gate_removed: int
    ttft_ms: int | None
    first_release_ms: int | None
    thinking_ms: int | None
    total_ms: int
    tokens_in: int | None
    tokens_out: int | None
    tokens_reasoning: int | None
    reasoning_seen: bool
    cost_usd: float | None
    cost_known: bool
    cost_source: CostSource
    finish_reason: str | None
    error: str | None
    error_kind: str | None
    retries: int
    request_params: dict[str, Any]
    request_params_hash: str
    prompt_hash: str
    rail_failures: list[str]
    warnings: list[str]

    @model_validator(mode="after")
    def _shape(self) -> "AnswerRecord":
        if self.error is None and self.route == "error":
            raise ValueError("a completed record cannot have route 'error'")
        if self.error is not None and self.route != "error":
            raise ValueError("an error record must have route 'error'")
        if self.cost_known and self.cost_usd is None:
            raise ValueError("cost_known with no cost_usd")
        return self

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.model, self.variant, self.item_id)


class ScoreRecord(_Strict):
    harness_version: str
    scored_at: str
    model: str
    variant: str
    item_id: str
    language: str
    item_type: str
    claim_check_pass: bool  # the released answer keeps every gold fact, states no forbidden one
    claims_removed: int
    removed_sentences: list[str]
    gate_false_removals: int  # removed sentences whose every value is in the sources
    gold_recall: float | None
    gold_missing: list[str]
    forbidden_hits: list[str]
    citations: list[int]
    citations_invalid: list[int]
    citations_valid: bool
    cites_gold: bool | None
    refusal: bool
    refusal_correct: bool
    reply_language: str
    language_match: bool
    table_expected: bool
    table_present: bool
    table_ok: bool | None
    words: int
    length_band: list[int]
    length_ok: bool
    rail_failures: list[str]
    passed: bool


class JudgeRecord(_Strict):
    harness_version: str
    timestamp: str
    model: str
    variant: str
    item_id: str
    judge_model: str
    language: str
    fluency: int | None
    naturalness: int | None
    script_ok: bool | None
    register_ok: bool | None
    notes: str
    cost_usd: float | None
    cost_known: bool
    tokens_in: int | None
    tokens_out: int | None
    error: str | None
    request_params_hash: str


class RunManifest(_Strict):
    run: str
    harness_version: str
    created: str
    items_sha256: str
    provider: str
    temperature: float
    dry_run: bool
    smoke: bool
    models: list[str]
    variants: list[str]


class ModelProgress(_Strict):
    model: str
    variant: str
    total: int
    done: int
    errors: int
    failures: int
    retries: int
    spend_usd: float
    status: Literal[
        "pending",
        "running",
        "complete",
        "incomplete",
        "skipped_fail_fast",
        "stopped_error_rate",
        "stopped_budget",
        "stopped_retry_cap",
        "stopped_run",
    ]
    reason: str | None


class Progress(_Strict):
    run: str
    harness_version: str
    started: str
    updated: str
    status: Literal["running", "complete", "stopped"]
    stop_reason: str | None
    budget_usd: float | None
    spend_usd: float
    reserved_usd: float
    retries: int
    models: dict[str, ModelProgress]


def append_jsonl(path: Path, record: BaseModel) -> None:
    """Append one validated record as one line, flushed to disk before returning."""
    line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.flush()
        with contextlib.suppress(OSError):
            os.fsync(fh.fileno())


def read_jsonl[M: BaseModel](path: Path, model: type[M], *, repair_tail: bool = False) -> list[M]:
    """Every record of `path`, validated. A run killed mid-write can leave a partial LAST line:
    with `repair_tail` it is cut off the file (and reported); any other bad line raises."""
    if not path.exists():
        return []
    raw = path.read_text("utf-8")
    lines = raw.split("\n")
    out: list[M] = []
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            out.append(model.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last = all(not rest.strip() for rest in lines[n + 1 :])
            if repair_tail and last and not raw.endswith("\n"):
                good = "\n".join(lines[:n])
                path.write_text(good + ("\n" if good else ""), encoding="utf-8", newline="\n")
                print(f"repaired {path.name}: dropped a partial last line ({len(line)} chars)")
                break
            raise ValueError(f"{path}:{n + 1}: invalid record: {exc}") from exc
    return out


def _replace(tmp: Path, path: Path, attempts: int = 20, delay_s: float = 0.05) -> None:
    """os.replace that survives a reader briefly holding the target open on Windows.

    Windows refuses to replace a file another process has open (antivirus, the search indexer,
    someone tailing progress.json), raising PermissionError; retry with a short backoff.
    """
    import time

    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay_s * (attempt + 1))


def write_json(path: Path, data: BaseModel | dict[str, Any]) -> None:
    """Write atomically (temp file + replace), so a reader never sees half a file."""
    payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    _replace(tmp, path)


def write_jsonl(path: Path, records: list[BaseModel]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for r in records:
            fh.write(json.dumps(r.model_dump(mode="json"), ensure_ascii=False) + "\n")
    _replace(tmp, path)
