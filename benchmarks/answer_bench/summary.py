"""Aggregates per model x thinking level: `summary.json` (schema `AnswerModelBenchmarks`, the
shape of `benchmarks/results/answer-models.json`) and `summary.md`.

`publish` merges a run's rows into benchmarks/results/answer-models.json, the single file a
dashboard or a README table reads, replacing rows with the same key (model@hosts|variant).
"""

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from benchmarks.answer_bench import BENCH, HARNESS_VERSION
from benchmarks.answer_bench.records import (
    RAIL_REASONING,
    AnswerRecord,
    JudgeRecord,
    Progress,
    RunManifest,
    ScoreRecord,
    read_jsonl,
    write_json,
)
from pydantic import BaseModel, ConfigDict, Field

PUBLISHED = BENCH / "results" / "answer-models.json"
METHOD = (
    "Each model answers the same frozen items (benchmarks/data/answers/items.jsonl: English, "
    "Hindi and Hinglish; answerable, comparison-table and unanswerable questions) from the "
    "same 3-6 passages, through the engine's own grounded-answer path (C2 Customer support "
    "template, prompt compiler, fact gate, LiteLLM streaming). Scores are deterministic checks "
    "over the saved answers; fluency is an optional LLM judge."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Latency(_Strict):
    ttft_p50_ms: float | None
    ttft_p90_ms: float | None
    total_p50_ms: float | None
    total_p90_ms: float | None


class Scores(_Strict):
    passed: float | None = Field(description="Share of answers passing every check.")
    claim_check_pass: float | None = Field(
        description="Released answer keeps every gold fact and states no forbidden fact."
    )
    gate_false_removals: int = Field(
        0, description="Sentences the fact gate removed although every value is in the sources."
    )
    gold_recall: float | None = Field(description="Mean over answerable and table items.")
    hallucination_rate: float | None = Field(description="Share with a forbidden fact.")
    citations_valid: float | None
    cites_gold: float | None
    refusal_correct_answerable: float | None = Field(description="Answered, did not refuse.")
    refusal_correct_unanswerable: float | None = Field(description="Refused, as it should.")
    language_match: dict[str, float]
    table_rate: float | None = Field(description="Tables where one was expected.")
    length_ok: float | None


class Judge(_Strict):
    judge_model: str
    graded: int
    fluency: dict[str, float]
    naturalness: dict[str, float]
    script_ok: float | None
    register_ok: float | None
    cost_usd: float


class LanguageSlice(_Strict):
    """One language of the item set (en, hi, hi-Latn) or `hindi_all` (hi and hi-Latn together)."""

    items: int
    passed: float | None
    refusal_correct_unanswerable: float | None = Field(description="Refused, as it should.")
    unanswerable_items: int
    table_rate: float | None = Field(description="Tables where one was expected.")
    table_items: int
    ttft_p50_ms: float | None
    usd_per_1000_answers: float | None
    inr_per_1000_answers: float | None
    judged: int = Field(description="Answers the LLM judge graded; 0 for English.")
    fluency: float | None
    naturalness: float | None
    script_ok: float | None
    register_ok: float | None


class Row(_Strict):
    key: str = Field(description="model[@hosts]|variant")
    model: str
    provider: str
    provider_order: list[str]
    variant: str
    thinking_effective: list[str]
    thinking_source: list[str]
    hosts: dict[str, int]
    status: str
    status_reason: str | None
    items: int
    answered: int
    errors: int
    rail_failures: dict[str, int]
    reasoning_violations: int = Field(
        description="Reasoning tokens or deltas on a thinking-off variant."
    )
    latency: Latency
    tokens_mean: dict[str, float | None]
    cost_known_share: float | None
    usd_per_1000_answers: float | None
    inr_per_1000_answers: float | None
    scores: Scores
    judge: list[Judge]
    by_language: dict[str, LanguageSlice] = Field(
        default_factory=dict,
        description="The same measures per language: en, hi, hi-Latn and hindi_all.",
    )


class AnswerModelBenchmarks(_Strict):
    schema_version: Literal[1]
    harness_version: str
    updated: str
    run: str
    items_sha256: str
    usd_inr_rate: float
    method: str
    rows: list[Row]


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def _share(flags: list[bool]) -> float | None:
    return round(sum(flags) / len(flags), 4) if flags else None


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 4) if values else None


HINDI_ALL = "hindi_all"
_INDIC = ("hi", "hi-Latn")


def _slice(
    recs: list[AnswerRecord], sc: list[ScoreRecord], js: list[JudgeRecord], usd_inr: float
) -> LanguageSlice:
    unans = [s for s in sc if s.item_type == "unanswerable"]
    tables = [s for s in sc if s.item_type == "table"]
    costs = [r.cost_usd for r in recs if r.cost_usd is not None and r.cost_known]
    usd = round(statistics.fmean(costs) * 1000, 4) if costs else None
    graded = [j for j in js if j.error is None]
    return LanguageSlice(
        items=len(sc),
        passed=_share([s.passed for s in sc]),
        refusal_correct_unanswerable=_share([s.refusal_correct for s in unans]),
        unanswerable_items=len(unans),
        table_rate=_share([s.table_present for s in tables]),
        table_items=len(tables),
        ttft_p50_ms=_pct([r.ttft_ms for r in recs if r.ttft_ms is not None], 0.5),
        usd_per_1000_answers=usd,
        inr_per_1000_answers=round(usd * usd_inr, 2) if usd is not None else None,
        judged=len(graded),
        fluency=_mean([float(j.fluency) for j in graded if j.fluency is not None]),
        naturalness=_mean([float(j.naturalness) for j in graded if j.naturalness is not None]),
        script_ok=_share([j.script_ok for j in graded if j.script_ok is not None]),
        register_ok=_share([j.register_ok for j in graded if j.register_ok is not None]),
    )


def build(run_dir: Path, *, total_items: int, usd_inr: float) -> AnswerModelBenchmarks:
    manifest = RunManifest.model_validate_json((run_dir / "run.json").read_text("utf-8"))
    answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
    errors = read_jsonl(run_dir / "errors.jsonl", AnswerRecord)
    scores = read_jsonl(run_dir / "scores.jsonl", ScoreRecord)
    judged = read_jsonl(run_dir / "judge.jsonl", JudgeRecord)
    progress = (
        Progress.model_validate_json((run_dir / "progress.json").read_text("utf-8"))
        if (run_dir / "progress.json").exists()
        else None
    )
    groups: dict[tuple[str, str], list[AnswerRecord]] = defaultdict(list)
    for r in answers:
        groups[(r.model, r.variant)].append(r)
    for r in errors:
        groups.setdefault((r.model, r.variant), [])
    score_by = {(s.model, s.variant, s.item_id): s for s in scores}
    rows: list[Row] = []
    for (model, variant), recs in sorted(groups.items()):
        errs = [e for e in errors if e.model == model and e.variant == variant]
        failed_keys = {(e.item_id) for e in errs} - {r.item_id for r in recs}
        sc = [score_by[r.key] for r in recs if r.key in score_by]
        answerable = [s for s in sc if s.item_type != "unanswerable"]
        unanswerable = [s for s in sc if s.item_type == "unanswerable"]
        tables = [s for s in sc if s.item_type == "table"]
        by_lang: dict[str, list[bool]] = defaultdict(list)
        for s in sc:
            by_lang[s.language].append(s.language_match)
        costs = [r.cost_usd for r in recs if r.cost_usd is not None and r.cost_known]
        rails: Counter[str] = Counter(f for r in recs for f in r.rail_failures)
        prog = progress.models.get(f"{model}|{variant}") if progress else None
        judges = []
        for jm in sorted({j.judge_model for j in judged}):
            js = [
                j
                for j in judged
                if j.judge_model == jm
                and j.model == model
                and j.variant == variant
                and j.error is None
            ]
            if not js:
                continue
            flu: dict[str, list[float]] = defaultdict(list)
            nat: dict[str, list[float]] = defaultdict(list)
            for j in js:
                if j.fluency is not None:
                    flu[j.language].append(j.fluency)
                if j.naturalness is not None:
                    nat[j.language].append(j.naturalness)
            judges.append(
                Judge(
                    judge_model=jm,
                    graded=len(js),
                    fluency={k: round(statistics.fmean(v), 2) for k, v in sorted(flu.items())},
                    naturalness={k: round(statistics.fmean(v), 2) for k, v in sorted(nat.items())},
                    script_ok=_share([j.script_ok for j in js if j.script_ok is not None]),
                    register_ok=_share([j.register_ok for j in js if j.register_ok is not None]),
                    cost_usd=round(sum(j.cost_usd or 0.0 for j in js), 6),
                )
            )
        usd_1000 = round(statistics.fmean(costs) * 1000, 4) if costs else None
        first_judge = judges[0].judge_model if judges else None
        by_language: dict[str, LanguageSlice] = {}
        for name, langs in (
            *((lang, (lang,)) for lang in ("en", *_INDIC)),
            (HINDI_ALL, _INDIC),
        ):
            by_language[name] = _slice(
                [r for r in recs if r.language in langs],
                [s for s in sc if s.language in langs],
                [
                    j
                    for j in judged
                    if j.judge_model == first_judge
                    and j.model == model
                    and j.variant == variant
                    and j.language in langs
                ],
                usd_inr,
            )
        rows.append(
            Row(
                key=f"{model}|{variant}",
                model=model.split("@", 1)[0],
                provider=recs[0].provider if recs else manifest.provider,
                provider_order=recs[0].provider_order if recs else [],
                variant=variant,
                thinking_effective=sorted({r.thinking_effective for r in recs}),
                thinking_source=sorted({r.thinking_source for r in recs}),
                hosts=dict(Counter(r.host or "unknown" for r in recs).most_common()),
                # A run resumed after an early fail-fast leaves the old status in progress.json
                # although every item has an answer now: the answers are the record.
                status="complete"
                if len(recs) >= total_items
                else (prog.status if prog else "unknown"),
                status_reason=None if len(recs) >= total_items else (prog.reason if prog else None),
                items=total_items,
                answered=len(recs),
                errors=len(failed_keys),
                rail_failures=dict(sorted(rails.items())),
                reasoning_violations=rails.get(RAIL_REASONING, 0),
                latency=Latency(
                    ttft_p50_ms=_pct([r.ttft_ms for r in recs if r.ttft_ms is not None], 0.5),
                    ttft_p90_ms=_pct([r.ttft_ms for r in recs if r.ttft_ms is not None], 0.9),
                    total_p50_ms=_pct([r.total_ms for r in recs], 0.5),
                    total_p90_ms=_pct([r.total_ms for r in recs], 0.9),
                ),
                tokens_mean={
                    "in": _mean([r.tokens_in for r in recs if r.tokens_in is not None]),
                    "out": _mean([r.tokens_out for r in recs if r.tokens_out is not None]),
                    "reasoning": _mean(
                        [r.tokens_reasoning for r in recs if r.tokens_reasoning is not None]
                    ),
                },
                cost_known_share=_share([r.cost_known for r in recs]),
                usd_per_1000_answers=usd_1000,
                inr_per_1000_answers=round(usd_1000 * usd_inr, 2) if usd_1000 is not None else None,
                scores=Scores(
                    passed=_share([s.passed for s in sc]),
                    claim_check_pass=_share([s.claim_check_pass for s in sc]),
                    gate_false_removals=sum(s.gate_false_removals for s in sc),
                    gold_recall=_mean(
                        [s.gold_recall for s in answerable if s.gold_recall is not None]
                    ),
                    hallucination_rate=_share([bool(s.forbidden_hits) for s in sc]),
                    citations_valid=_share([s.citations_valid for s in sc]),
                    cites_gold=_share(
                        [s.cites_gold for s in answerable if s.cites_gold is not None]
                    ),
                    refusal_correct_answerable=_share([s.refusal_correct for s in answerable]),
                    refusal_correct_unanswerable=_share([s.refusal_correct for s in unanswerable]),
                    language_match={
                        k: round(sum(v) / len(v), 4) for k, v in sorted(by_lang.items())
                    },
                    table_rate=_share([s.table_present for s in tables]),
                    length_ok=_share([s.length_ok for s in sc]),
                ),
                judge=judges,
                by_language=by_language,
            )
        )
    return AnswerModelBenchmarks(
        schema_version=1,
        harness_version=HARNESS_VERSION,
        updated=datetime.now(UTC).isoformat(timespec="seconds"),
        run=manifest.run,
        items_sha256=manifest.items_sha256,
        usd_inr_rate=usd_inr,
        method=METHOD,
        rows=rows,
    )


def _f(value: float | None, fmt: str = "{:.0%}") -> str:
    return "-" if value is None else fmt.format(value)


def markdown(doc: AnswerModelBenchmarks) -> str:
    lines = [
        f"# Answer models: run `{doc.run}`",
        "",
        f"Harness {doc.harness_version}, items `{doc.items_sha256[:12]}`, updated {doc.updated}, "
        f"USD/INR {doc.usd_inr_rate}.",
        "",
        "| Model | Thinking | Status | Answered | TTFT p50 / p90 (ms) | Total p50 (ms) | "
        "$ / 1k | ₹ / 1k | Passed | Claim check | Gate false removals | Gold recall | Halluc. | "
        "Refusal ok (ans / unans) | Lang en / hi / hi-Latn | Tables | Reasoning violations |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in doc.rows:
        s = r.scores
        lang = " / ".join(_f(s.language_match.get(k)) for k in ("en", "hi", "hi-Latn"))
        lines.append(
            f"| {r.model}{'@' + '+'.join(r.provider_order) if r.provider_order else ''} "
            f"| {r.variant} | {r.status} | {r.answered}/{r.items} "
            f"| {_f(r.latency.ttft_p50_ms, '{:.0f}')} / {_f(r.latency.ttft_p90_ms, '{:.0f}')} "
            f"| {_f(r.latency.total_p50_ms, '{:.0f}')} "
            f"| {_f(r.usd_per_1000_answers, '{:.3f}')} | {_f(r.inr_per_1000_answers, '{:.2f}')} "
            f"| {_f(s.passed)} | {_f(s.claim_check_pass)} | {s.gate_false_removals} "
            f"| {_f(s.gold_recall)} "
            f"| {_f(s.hallucination_rate)} | {_f(s.refusal_correct_answerable)} / "
            f"{_f(s.refusal_correct_unanswerable)} | {lang} | {_f(s.table_rate)} "
            f"| {r.reasoning_violations} |"
        )
    judged = [(r, j) for r in doc.rows for j in r.judge]
    if judged:
        lines += [
            "",
            "## Hindi / Hinglish fluency (LLM judge, 1-5)",
            "",
            "| Model | Thinking | Judge | Graded | Fluency hi / hi-Latn | Naturalness hi / hi-Latn "
            "| Script ok | Register ok | Judge $ |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r, j in judged:
            lines.append(
                f"| {r.model} | {r.variant} | {j.judge_model} | {j.graded} "
                f"| {j.fluency.get('hi', '-')} / {j.fluency.get('hi-Latn', '-')} "
                f"| {j.naturalness.get('hi', '-')} / {j.naturalness.get('hi-Latn', '-')} "
                f"| {_f(j.script_ok)} | {_f(j.register_ok)} | {j.cost_usd:.4f} |"
            )
    hosts = [(r, r.hosts) for r in doc.rows]
    lines += ["", "## Hosts and notes", ""]
    for r, h in hosts:
        reason = f" Stopped: {r.status_reason}" if r.status_reason else ""
        rails = f" Rail failures: {r.rail_failures}." if r.rail_failures else ""
        lines.append(
            f"- `{r.key}`: hosts {h}; thinking sent as {r.thinking_effective} "
            f"({', '.join(r.thinking_source)}).{rails}{reason}"
        )
    lines += ["", doc.method, ""]
    return "\n".join(lines)


def write(run_dir: Path, *, total_items: int, usd_inr: float) -> AnswerModelBenchmarks:
    doc = build(run_dir, total_items=total_items, usd_inr=usd_inr)
    write_json(run_dir / "summary.json", doc)
    (run_dir / "summary.md").write_text(markdown(doc), encoding="utf-8", newline="\n")
    return doc


def publish(run_dir: Path, target: Path = PUBLISHED) -> AnswerModelBenchmarks:
    """Merge the run's rows into `target`, replacing rows with the same key."""
    doc = AnswerModelBenchmarks.model_validate_json((run_dir / "summary.json").read_text("utf-8"))
    if target.exists():
        old = AnswerModelBenchmarks.model_validate(json.loads(target.read_text("utf-8")))
        if old.items_sha256 != doc.items_sha256:
            raise ValueError(
                f"{target.name} was measured on another item set ({old.items_sha256[:12]}); "
                "rows from different item sets are not comparable"
            )
        keep = [r for r in old.rows if r.key not in {n.key for n in doc.rows}]
        doc = doc.model_copy(
            update={
                "rows": sorted([*keep, *doc.rows], key=lambda r: r.key),
                "run": "+".join(dict.fromkeys([*old.run.split("+"), *doc.run.split("+")])),
            }
        )
    write_json(target, doc)
    return doc
