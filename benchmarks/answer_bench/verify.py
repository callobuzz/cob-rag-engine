"""Check that a run captured and persisted everything (the dry run's and the smoke run's gate).

Reads the files back from disk (not the in-memory results), so a field that was measured but
never written counts as missing.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from benchmarks.answer_bench.items import Item
from benchmarks.answer_bench.records import (
    AnswerRecord,
    JudgeRecord,
    Progress,
    ScoreRecord,
    read_jsonl,
)
from benchmarks.answer_bench.summary import AnswerModelBenchmarks


@dataclass
class Report:
    rows: list[list[str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def record_problems(rec: AnswerRecord, *, require_host: bool) -> list[str]:
    p: list[str] = []
    if not rec.answer_raw.strip():
        p.append("empty answer")
    if rec.ttft_ms is None or rec.ttft_ms <= 0:
        p.append("TTFT missing")
    elif rec.total_ms < rec.ttft_ms:
        p.append("total < TTFT")
    if not rec.tokens_in or not rec.tokens_out:
        p.append("token counts missing")
    if not rec.cost_known or not rec.cost_usd or rec.cost_usd <= 0:
        p.append("cost missing")
    if rec.variant == "off" and ((rec.tokens_reasoning or 0) > 0 or rec.reasoning_seen):
        p.append(f"reasoning with thinking off ({rec.tokens_reasoning} tokens)")
    if rec.tokens_reasoning is None:
        p.append("reasoning tokens missing")
    if require_host and not rec.host:
        p.append("host missing")
    if not rec.finish_reason:
        p.append("finish reason missing")
    if not rec.generation_id:
        p.append("response id missing")
    params = rec.request_params
    if params.get("model") is None or params.get("stream") is not True:
        p.append("request params not captured")
    if rec.rail_failures:
        p.append("rails: " + ",".join(rec.rail_failures))
    return p


def verify_run(
    run_dir: Path,
    items: list[Item],
    models: list[str],
    variants: list[str],
    *,
    require_host: bool = True,
    expect_judge: bool = False,
) -> Report:
    report = Report()
    try:
        answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
        errors = read_jsonl(run_dir / "errors.jsonl", AnswerRecord)
        scores = read_jsonl(run_dir / "scores.jsonl", ScoreRecord)
        judged = read_jsonl(run_dir / "judge.jsonl", JudgeRecord)
    except ValueError as exc:
        report.problems.append(f"a record failed validation: {exc}")
        return report
    keys = [r.key for r in answers]
    if len(keys) != len(set(keys)):
        report.problems.append("duplicate (model, variant, item) records in answers.jsonl")
    have = set(keys)
    score_keys = {(s.model, s.variant, s.item_id) for s in scores}
    for model in models:
        for variant in variants:
            for item in items:
                if (model, variant, item.id) not in have:
                    report.problems.append(f"{model} [{variant}] {item.id}: no answer record")
    by_key = {r.key: r for r in answers}
    for key in sorted(by_key):
        rec = by_key[key]
        problems = record_problems(rec, require_host=require_host)
        if key not in score_keys:
            problems.append("not scored")
        for problem in problems:
            report.problems.append(f"{rec.model} [{rec.variant}] {rec.item_id}: {problem}")
        report.rows.append(
            [
                rec.item_id,
                rec.variant,
                str(rec.ttft_ms),
                str(rec.total_ms),
                f"{rec.tokens_in}/{rec.tokens_out}/{rec.tokens_reasoning}",
                f"{rec.cost_usd:.6f}" if rec.cost_usd is not None else "None",
                str(rec.host),
                str(rec.finish_reason),
                str(len(rec.answer_raw)),
                "ok" if not problems else "; ".join(problems),
            ]
        )
    for e in errors:
        if e.key not in have:
            report.problems.append(f"{e.model} [{e.variant}] {e.item_id}: error {e.error_kind}")
    if expect_judge:
        to_judge = {r.key for r in answers if r.language in ("hi", "hi-Latn")}
        graded = {(j.model, j.variant, j.item_id) for j in judged if j.error is None}
        if to_judge - graded:
            report.problems.append(f"{len(to_judge - graded)} Hindi/Hinglish answers not judged")
        for j in judged:
            if j.fluency is None or j.naturalness is None or not j.cost_known:
                report.problems.append(f"judge record {j.item_id}: missing score or cost")
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        report.problems.append("summary.json missing")
    else:
        try:
            doc = AnswerModelBenchmarks.model_validate(json.loads(summary_path.read_text("utf-8")))
            expected = {f"{m}|{v}" for m in models for v in variants}
            if {r.key for r in doc.rows} != expected:
                report.problems.append("summary rows do not match the models x variants run")
            for row in doc.rows:
                if row.latency.ttft_p50_ms is None or row.usd_per_1000_answers is None:
                    report.problems.append(f"summary {row.key}: latency or cost missing")
                if row.variant == "off" and row.reasoning_violations:
                    report.problems.append(f"summary {row.key}: reasoning with thinking off")
                if row.scores.passed is None:
                    report.problems.append(f"summary {row.key}: scores missing")
        except ValueError as exc:
            report.problems.append(f"summary.json invalid: {exc}")
    if not (run_dir / "summary.md").exists():
        report.problems.append("summary.md missing")
    try:
        Progress.model_validate_json((run_dir / "progress.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        report.problems.append(f"progress.json unreadable: {exc}")
    return report


HEADERS = [
    "item",
    "thinking",
    "ttft ms",
    "total ms",
    "in/out/reason",
    "cost $",
    "host",
    "finish",
    "chars",
    "checks",
]


def table(report: Report) -> str:
    rows = [HEADERS, *report.rows]
    widths = [max(len(r[i]) for r in rows) for i in range(len(HEADERS))]
    out = [" | ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)) for r in rows]
    out.insert(1, "-+-".join("-" * w for w in widths))
    return "\n".join(out)
