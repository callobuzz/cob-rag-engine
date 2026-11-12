"""Scoring: accuracy, recall, reply language, fallbacks, latency and cost from the SAVED records.

Nothing here calls a model. A label is re-derived from the saved raw reply with the engine's own
parser (so a change to the parser can be re-scored for free); the turns the rules decide are
labelled by the rules, which is deterministic. A model is scored only when every turn it was
asked is present; otherwise it is `incomplete` and never merged into fast-models.json.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from benchmarks.fast_bench import FIXTURES, HARNESS_VERSION
from benchmarks.fast_bench.records import LabelRecord, read_jsonl, thinking_ok, write_json

DEFAULT_BARS = {
    "overall": 0.90,
    "per_language": 0.85,
    "business_recall": 0.97,
    "reply_language": 0.95,
    "max_fallback_rate": 0.05,
}


def load_rows() -> list[dict[str, Any]]:
    path = FIXTURES / "labels.jsonl"
    return [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]


def load_businesses() -> dict[str, dict[str, str]]:
    return json.loads((FIXTURES / "label_businesses.json").read_text("utf-8"))  # type: ignore[no-any-return]


def model_turn_ids(rows: list[dict[str, Any]]) -> list[str]:
    """The turns the rules do not decide (the only ones a model is asked)."""
    from callobuzz_rag.conversation.labeler import Labeler

    rules = Labeler(llm=None, cache=None, timeout_s=1, ttl_s=0)  # type: ignore[arg-type]
    return [r["id"] for r in rows if rules.by_rules(r["text"]) is None]


def pct(values: list[int], q: float) -> float | None:
    """The q-quantile of millisecond values, in seconds, two decimals."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * q))] / 1000, 2)


def rederive(rec: LabelRecord, config: Any) -> tuple[str, str]:
    """(label, source) the engine's parser makes of the saved reply."""
    from callobuzz_rag.conversation.labeler import Labeler, _loads_lenient
    from callobuzz_rag.llm.thinking import strip_reasoning_text

    raw, _ = _loads_lenient(strip_reasoning_text(rec.reply_raw))
    parsed = Labeler._parse(raw, config) if isinstance(raw, dict) else None
    if parsed is None:
        return "business_question", "fallback"
    return parsed.label.value, "model"


def reasoning_effort(rec: LabelRecord) -> str | None:
    """The thinking effort the engine sent, in fast-models.json's vocabulary; None = none sent."""
    if not rec.thinking_sent:
        return None
    return {
        "off": "none",
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
    }.get(rec.thinking_effective)


def _summarise(
    key: str,
    recs: list[LabelRecord],
    rows: list[dict[str, Any]],
    turn_ids: list[str],
    configs: dict[str, Any],
    errors: int,
    usd_inr: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from callobuzz_rag.conversation.labeler import Labeler
    from callobuzz_rag.conversation.language import detect_language, reply_language

    rules = Labeler(llm=None, cache=None, timeout_s=1, ttl_s=0)  # type: ignore[arg-type]
    by_turn = {r.turn_id: r for r in recs}
    missing = [t for t in turn_ids if t not in by_turn]
    results: list[dict[str, Any]] = []
    for row in rows:
        config = configs[row["business"]]
        rec = by_turn.get(row["id"])
        out = rules.by_rules(row["text"])
        if out is not None:
            got, source, ms = out.label.value, "rules", None
        elif rec is not None:
            (got, source), ms = rederive(rec, config), rec.ms
        else:
            continue
        lang, _ = reply_language(config, detect_language(row["text"]))
        results.append(
            {
                "model": key,
                "id": row["id"],
                "expected": row["label"],
                "got": got,
                "correct": got == row["label"],
                "source": source,
                "language": row["language"],
                "got_language": lang,
                "language_ok": lang == row["language"],
                "ms": ms,
            }
        )
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_lang[r["language"]].append(r)
    business = [r for r in results if r["expected"] == "business_question"]
    called = [r for r in results if r["source"] != "rules"]
    ms_ok = [r["ms"] for r in called if r["source"] == "model"]
    answered = [r for r in recs if r.fallback_reason != "timeout"]
    priced = [r for r in answered if r.cost_usd is not None]
    cost_ok = bool(answered) and len(priced) == len(answered) and all(r.cost_known for r in priced)
    usd_total = sum(r.cost_usd or 0.0 for r in recs)
    prompt = sum(r.tokens_in or 0 for r in recs)
    complete = not missing and errors == 0
    hosts = Counter(r.host or "unknown" for r in recs)
    verified = bool(answered) and complete and all(thinking_ok(r.model_dump()) for r in answered)
    last = recs[-1] if recs else None
    summary = {
        "key": key,
        "model": recs[0].model.split("@")[0] if recs else key.split("@")[0],
        "provider": recs[0].provider if recs else "openrouter",
        "provider_order": list(recs[0].provider_order) if recs else [],
        "status": "complete" if complete else "incomplete",
        "turns": len(rows),
        "model_turns": len(called),
        "missing_turns": missing,
        "errors": errors,
        "overall": round(sum(r["correct"] for r in results) / max(len(results), 1), 3),
        "per_language": {
            k: round(sum(r["correct"] for r in v) / len(v), 3) for k, v in sorted(by_lang.items())
        },
        "business_recall": round(
            sum(r["got"] == "business_question" for r in business) / max(len(business), 1), 3
        ),
        "reply_language": round(sum(r["language_ok"] for r in results) / max(len(results), 1), 3),
        "fallback_rate": round(
            sum(r["source"] == "fallback" for r in called) / max(len(called), 1), 3
        ),
        "fallback_reasons": dict(Counter(r.fallback_reason for r in recs if r.fallback_reason)),
        "latency_s": {"p50": pct(ms_ok, 0.5), "p90": pct(ms_ok, 0.9), "p99": pct(ms_ok, 0.99)},
        "usd_total": round(usd_total, 6),
        "usd_per_1000_labels": round(usd_total / len(recs) * 1000, 3) if cost_ok else None,
        "inr_per_1000_labels": round(usd_total / len(recs) * 1000 * usd_inr, 2)
        if cost_ok
        else None,
        "cached_prompt_share": round(sum(r.tokens_cached or 0 for r in recs) / prompt, 3)
        if prompt
        else None,
        "reasoning_tokens": sum(r.tokens_reasoning or 0 for r in recs),
        "thinking_effective": sorted({r.thinking_effective for r in recs}),
        "thinking_minimum_only": bool(last and last.thinking_minimum_only),
        "thinking_control_verified": verified,
        "reasoning_effort": reasoning_effort(last) if last else None,
        "hosts": dict(hosts),
        "rail_failures": dict(Counter(f for r in recs for f in r.rail_failures)),
        "confusion": dict(
            Counter(f"{r['expected']}->{r['got']}" for r in results if not r["correct"])
        ),
    }
    return summary, results


def passes(summary: dict[str, Any], bars: dict[str, float]) -> bool:
    return bool(
        summary["status"] == "complete"
        and summary["overall"] >= bars["overall"]
        and min(summary["per_language"].values()) >= bars["per_language"]
        and summary["business_recall"] >= bars["business_recall"]
        and summary["reply_language"] >= bars["reply_language"]
        and summary["fallback_rate"] <= bars["max_fallback_rate"]
    )


def read_bars(path: Path | None = None) -> dict[str, float]:
    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH

    try:
        return dict(json.loads((path or FAST_MODELS_PATH).read_text("utf-8"))["bars"])
    except (OSError, ValueError, KeyError):
        return dict(DEFAULT_BARS)


def score_run(
    run_dir: Path,
    rows: list[dict[str, Any]],
    businesses: dict[str, dict[str, str]],
    usd_inr: float,
    *,
    turn_ids: list[str] | None = None,
    bars: dict[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Score every model in the run from labels.jsonl (and errors.jsonl for its error counts);
    write scores.jsonl, summary.json and summary.md; return the summaries by model key."""
    from benchmarks.labels_dev import build_config

    ids = turn_ids if turn_ids is not None else model_turn_ids(rows)
    bars = bars or read_bars()
    configs = {name: build_config(b) for name, b in businesses.items()}
    recs = read_jsonl(run_dir / "labels.jsonl", LabelRecord)
    errs = read_jsonl(run_dir / "errors.jsonl", LabelRecord)
    done_keys = {r.key for r in recs}
    models: dict[str, list[LabelRecord]] = defaultdict(list)
    for r in recs:
        models[r.model].append(r)
    for r in errs:
        models.setdefault(r.model, [])
    summaries: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    for key in sorted(models):
        # An error the model later got past (a resumed run) is not an open error.
        open_errors = len(
            {e.turn_id for e in errs if e.model == key} - {k[1] for k in done_keys if k[0] == key}
        )
        wanted = set(ids)
        mine = [r for r in sorted(models[key], key=lambda r: r.timestamp) if r.turn_id in wanted]
        summary, results = _summarise(key, mine, rows, ids, configs, open_errors, usd_inr)
        summary["passes"] = passes(summary, bars)
        summaries[key] = summary
        lines += [json.dumps(r, ensure_ascii=False) for r in results]
    (run_dir / "scores.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")
    write_json(
        run_dir / "summary.json",
        {"harness_version": HARNESS_VERSION, "bars": bars, "models": summaries},
    )
    (run_dir / "summary.md").write_text(markdown(summaries, bars, run_dir.name), "utf-8")
    return summaries


def markdown(summaries: dict[str, dict[str, Any]], bars: dict[str, float], run: str) -> str:
    head = [
        f"# Fast-model label run: {run}",
        "",
        "| Model | Status | Overall | en / hi / hi-Latn | Recall | Reply lang | Fallbacks | "
        "p50 / p90 / p99 s | $ / 1,000 | Rs / 1,000 | Host | Thinking | Verified | Result |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    body = []
    for s in sorted(summaries.values(), key=lambda s: (s["latency_s"]["p90"] or 99, s["key"])):
        lat = s["latency_s"]
        langs = " / ".join(
            f"{s['per_language'].get(k, 0) * 100:.1f}" for k in ("en", "hi", "hi-Latn")
        )
        body.append(
            f"| {s['key']} | {s['status']} | {s['overall']:.1%} | {langs} | "
            f"{s['business_recall']:.1%} | {s['reply_language']:.1%} | {s['fallback_rate']:.1%} | "
            f"{lat['p50']} / {lat['p90']} / {lat['p99']} | {s['usd_per_1000_labels']} | "
            f"{s['inr_per_1000_labels']} | {', '.join(sorted(s['hosts']))} | "
            f"{'/'.join(s['thinking_effective'])}{' (minimum)' if s['thinking_minimum_only'] else ''} | "
            f"{'yes' if s['thinking_control_verified'] else 'NO'} | "
            f"{'PASS' if s['passes'] else 'fail'} |"
        )
    foot = [
        "",
        f"Bars: overall {bars['overall']:.0%}, each language {bars['per_language']:.0%}, "
        f"recall {bars['business_recall']:.0%}, reply language {bars['reply_language']:.0%}, "
        f"fallbacks at most {bars['max_fallback_rate']:.0%}.",
    ]
    return "\n".join([*head, *body, *foot]) + "\n"


# ---------------------------------------------------------------- merge into fast-models.json


def to_row(summary: dict[str, Any], stamp: str, report: str) -> dict[str, Any]:
    """One fast-models.json row (the schema of callobuzz_rag.core.benchmark_results)."""
    return {
        "model": summary["model"],
        "provider": summary["provider"],
        "provider_order": summary["provider_order"],
        "reasoning_effort": summary["reasoning_effort"],
        "thinking_control_verified": summary["thinking_control_verified"],
        "date": stamp,
        "report": report,
        "turns": summary["turns"],
        "model_turns": summary["model_turns"],
        "overall": summary["overall"],
        "per_language": summary["per_language"],
        "business_recall": summary["business_recall"],
        "reply_language": summary["reply_language"],
        "fallback_rate": summary["fallback_rate"],
        "latency_s": summary["latency_s"],
        "usd_per_1000_labels": summary["usd_per_1000_labels"],
        "cached_prompt_share": summary["cached_prompt_share"],
    }


def merge(
    summaries: dict[str, dict[str, Any]],
    stamp: str,
    report: str,
    path: Path,
    *,
    replace_all: bool = False,
    method: str | None = None,
) -> dict[str, Any]:
    """Merge the complete models into the results file at `path`, normalised (bar results,
    tags, recommendations recomputed). `replace_all` drops every older row (the new run
    re-measures them all); otherwise only rows with the same key are replaced."""
    from callobuzz_rag.core.benchmark_results import model_key, normalise

    doc = json.loads(path.read_text("utf-8"))
    new = {
        model_key(s["model"], s["provider_order"]): to_row(s, stamp, report)
        for s in summaries.values()
        if s["status"] == "complete"
    }
    kept = [] if replace_all else [m for m in doc["models"] if m["key"] not in new]
    doc["models"] = [*kept, *new.values()]
    doc["updated"] = stamp
    if method is not None:
        doc["method"] = {**doc["method"], "summary": method}
    doc = normalise(doc)
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    return doc
