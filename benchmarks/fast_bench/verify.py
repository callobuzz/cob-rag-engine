"""Checks that a run captured everything: the gate of the smoke run and of the free dry run.

`verify_smoke` demands that every record of a real (or fake) model is complete: a measurement
SV is about to pay for is only worth paying for when nothing is missing. `verify_dry_run` also
checks the fake models' designed failures (fail-fast, retries) and resumability.
"""

import json
from pathlib import Path
from typing import Any

from benchmarks.fast_bench.records import LabelRecord, Progress, read_jsonl, thinking_ok

LABELS = (
    "business_question",
    "buying_intent",
    "domain_explainer",
    "off_topic",
    "human_request",
    "abuse",
    "smalltalk",
    "other",
)
SUMMARY_FIELDS = (
    "overall",
    "per_language",
    "business_recall",
    "reply_language",
    "fallback_rate",
    "latency_s",
    "usd_per_1000_labels",
    "inr_per_1000_labels",
    "cached_prompt_share",
    "reasoning_tokens",
    "thinking_effective",
    "thinking_control_verified",
    "hosts",
)


def record_problems(rec: LabelRecord, *, require_host: bool) -> list[str]:
    """Everything a usable, fully captured record must have."""
    p: list[str] = []
    where = f"{rec.model} {rec.turn_id}"

    def bad(what: str) -> None:
        p.append(f"{where}: {what}")

    if rec.source != "model":
        bad(f"source is {rec.source} ({rec.fallback_reason or rec.error})")
    if rec.rail_failures:
        bad(f"rail failures {rec.rail_failures}")
    if rec.label not in LABELS:
        bad(f"label {rec.label!r} is not a label")
    if rec.ms <= 0:
        bad("no latency")
    if not rec.reply_raw.strip():
        bad("no reply text")
    if not rec.tokens_in or not rec.tokens_out:
        bad("tokens missing")
    if rec.tokens_reasoning is None:
        bad("reasoning tokens not reported")
    if rec.tokens_cached is None:
        bad("cached tokens not reported")
    if not rec.cost_known or not rec.cost_usd or rec.cost_usd <= 0:
        bad(f"cost not known and positive ({rec.cost_usd}, {rec.cost_source})")
    if not rec.thinking_effective:
        bad("no thinking_effective")
    if not thinking_ok(rec.model_dump()):
        bad(
            f"thinking is not as requested (effective {rec.thinking_effective}, reasoning "
            f"tokens {rec.tokens_reasoning}, leaked {rec.leaked})"
        )
    if not rec.request_params or "model" not in rec.request_params:
        bad("request params not captured")
    if not rec.generation_id:
        bad("no generation id")
    if require_host and not rec.host:
        bad("host unknown")
    return p


def verify_smoke(
    run_dir: Path, model: str, turn_ids: list[str], *, require_host: bool
) -> list[str]:
    problems: list[str] = []
    recs = [r for r in read_jsonl(run_dir / "labels.jsonl", LabelRecord) if r.model == model]
    got = {r.turn_id for r in recs}
    problems += [f"{model} {t}: no record" for t in turn_ids if t not in got]
    for rec in recs:
        problems += record_problems(rec, require_host=require_host)
    errs = read_jsonl(run_dir / "errors.jsonl", LabelRecord)
    problems += [
        f"{e.model} {e.turn_id}: error {e.error_kind}: {(e.error or '')[:120]}" for e in errs
    ]
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        return [*problems, "summary.json was not written"]
    summary = json.loads(summary_path.read_text("utf-8"))["models"].get(model)
    if summary is None:
        return [*problems, f"{model}: not in summary.json"]
    for field in SUMMARY_FIELDS:
        if summary.get(field) is None:
            problems.append(f"{model}: summary field {field} is empty")
    if summary["latency_s"]["p50"] is None:
        problems.append(f"{model}: no latency p50")
    if not (run_dir / "summary.md").exists() or not (run_dir / "scores.jsonl").read_text("utf-8"):
        problems.append("scores.jsonl or summary.md is empty")
    return problems


def table(run_dir: Path) -> str:
    recs = read_jsonl(run_dir / "labels.jsonl", LabelRecord)
    head = "turn | label | ms | tok in/out/reasoning/cached | cost $ | host | thinking"
    rows = [
        f"{r.turn_id} | {r.label} | {r.ms} | {r.tokens_in}/{r.tokens_out}/{r.tokens_reasoning}/"
        f"{r.tokens_cached} | {r.cost_usd} | {r.host} | {r.thinking_effective}"
        f"{' (sent)' if r.thinking_sent else ''}"
        for r in recs
    ]
    return "\n".join([head, *rows])


def verify_dry_run(run_dir: Path, expected: dict[str, str]) -> list[str]:
    """`expected` maps each fake model to the status it must end in."""
    problems: list[str] = []
    progress = Progress.model_validate_json((run_dir / "progress.json").read_text("utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text("utf-8"))["models"]
    for model, status in expected.items():
        got = progress.models[f"{model}|off"].status
        if got != status:
            problems.append(f"{model}: status {got}, expected {status}")
    for model, status in expected.items():
        if status != "complete":
            continue
        recs = [r for r in read_jsonl(run_dir / "labels.jsonl", LabelRecord) if r.model == model]
        if not recs:
            problems.append(f"{model}: no records")
        for rec in recs:
            problems += record_problems(rec, require_host=True)
        s = summary.get(model)
        if s is None:
            problems.append(f"{model}: not scored")
            continue
        for field in SUMMARY_FIELDS:
            if s.get(field) is None:
                problems.append(f"{model}: summary field {field} is empty")
        if s["status"] != "complete" or not s["thinking_control_verified"]:
            problems.append(
                f"{model}: summary says {s['status']}, verified {s['thinking_control_verified']}"
            )
    return problems


def summary_rows(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / "summary.json").read_text("utf-8"))["models"]  # type: ignore[no-any-return]
