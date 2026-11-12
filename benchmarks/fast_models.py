"""Fast-model benchmark: which small model labels a visitor's message best, how fast, at what cost.

    python -m benchmarks.fast_models run --dry-run                    # free: fake models, all checks
    python -m benchmarks.fast_models run --models openai/gpt-4.1-mini --smoke   # cents, 5 turns
    python -m benchmarks.fast_models run --candidates --run my-run --budget-usd 1.8
    python -m benchmarks.fast_models run --models a,b@groq --run my-run --budget-usd 0.5
    python -m benchmarks.fast_models hosts --run my-run     # fill hosts and costs (free)
    python -m benchmarks.fast_models score --run my-run     # re-score the saved labels (free)
    python -m benchmarks.fast_models merge --run my-run     # -> results/fast-models.json + docs

Each model labels the 266 held-out turns of tests/fixtures/conversation/labels.jsonl through the
engine's own `Labeler` and `LiteLLMProvider` (frozen prompt, nothing cached), each turn for the
business it was written for. Only the turns the rules do not decide are called. Thinking is off
(a family that cannot be off runs at its minimum and is marked so), checked on every call.
Generation, host backfill and scoring are separate, resumable, budget-capped steps (SV's
paid-benchmark rule); method and bars: benchmarks/README.md, "Fast models". Needs no stack, only
the engine's LLM settings: the key comes from `Settings()` (.env or RAG_LLM_API_KEY), else
OPENROUTER_API_KEY, and is never printed.
"""

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # `python benchmarks/fast_models.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

from benchmarks.fast_bench import RESULTS_ROOT, fake, run, score, verify

EXIT_OK, EXIT_CHECKS_FAILED, EXIT_STOPPED, EXIT_USAGE = 0, 1, 2, 3

# Chosen 2026-10-03: small or fast-hosted models that take JSON output on OpenRouter, pinned to
# the low-latency host that serves them where it matters (Groq, Cerebras). The first six anchors
# were measured before the label prompt was reordered for caching and are re-measured here;
# gpt-oss-120b is the control and cannot switch thinking off (it runs at its minimum, "low").
CANDIDATES = (
    "google/gemini-2.5-flash-lite",
    "google/gemma-4-31b-it",
    "google/gemma-4-26b-a4b-it",
    "meta-llama/llama-3.3-70b-instruct@groq",
    "qwen/qwen3-next-80b-a3b-instruct",
    "mistralai/mistral-small-3.2-24b-instruct",
    "mistralai/mistral-small-2603",
    "openai/gpt-4.1-mini",
    "openai/gpt-4.1-nano",
    "openai/gpt-6-luna",
    "deepseek/deepseek-v4.1-flash",
    "qwen/qwen3.8-27b@cerebras",
    "qwen/qwen3-30b-a3b-instruct-2507",
    "meta-llama/llama-3.1-8b-instruct@groq",
    "openai/gpt-oss-120b",
)
DRY_MODELS = {
    "fake/good": "complete",
    "fake/flaky": "complete",
    "fake/slow": "complete",
    "fake/thinks-anyway": "skipped_fail_fast",
    "fake/garbage": "skipped_fail_fast",
}
SMOKE_TURNS = 5

METHOD = (
    "266 held-out turns in English, Hindi and Hinglish, each asked of the business it was "
    "written for (tests/fixtures/conversation/labels.jsonl). The turns the rules decide (104) "
    "are labelled by the rules; the other 162 go to the model through the engine's own labeler "
    "and provider with the frozen prompt, nothing cached, thinking off, four calls in flight "
    "(benchmarks/fast_models.py). Latency is the label call alone; cost is the provider's billed "
    "cost; the serving host comes from OpenRouter's generation record. Every call is checked: "
    "reasoning tokens must be 0 with thinking off, and a model that cannot switch thinking off "
    "(gpt-oss) runs at its minimum and is marked so. One run per model, no tuning; a single run "
    "reads as roughly plus or minus 2 points."
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def smoke_turn_ids(rows: list[dict[str, Any]], model_ids: list[str]) -> list[str]:
    """Five model-decided turns, one per language first, then different labels."""
    by_id = {r["id"]: r for r in rows}
    chosen: list[str] = []
    for lang in ("en", "hi", "hi-Latn"):
        chosen += [t for t in model_ids if by_id[t]["language"] == lang][:1]
    seen = {by_id[t]["label"] for t in chosen}
    for t in model_ids:
        if len(chosen) >= SMOKE_TURNS:
            break
        if t not in chosen and by_id[t]["label"] not in seen:
            chosen.append(t)
            seen.add(by_id[t]["label"])
    for t in model_ids:
        if len(chosen) >= SMOKE_TURNS:
            break
        if t not in chosen:
            chosen.append(t)
    return chosen[:SMOKE_TURNS]


def _settings(dry_run: bool) -> Any:
    from callobuzz_rag.core.config import Settings

    return Settings(_env_file=None) if dry_run else Settings()


def _api_key(settings: Any) -> str:
    return settings.llm_api_key.get_secret_value() or os.environ.get("OPENROUTER_API_KEY", "")


def _estimator(provider: str) -> Any:
    from callobuzz_rag.llm.cost import priced_cost

    def estimate(model: str, prompt_tokens: int) -> float | None:
        try:
            cost = priced_cost(model, provider, prompt_tokens, 100)
        except Exception:
            return None
        return cost * 1.5 if cost else None

    return estimate


def _run_dir(args: argparse.Namespace, default: str) -> Path:
    root = Path(args.out_root) if args.out_root else RESULTS_ROOT
    if getattr(args, "dry_run", False) and not args.out_root:  # a dry run leaves no files behind
        root = Path(tempfile.gettempdir()) / "cob-fast-bench"
    return root / (args.run or default)


def _models(args: argparse.Namespace) -> list[Any]:
    from benchmarks.answer_bench.generate import parse_models

    if args.dry_run:
        return parse_models(args.models or ",".join(DRY_MODELS))
    if args.candidates:
        return parse_models(",".join(CANDIDATES))
    return parse_models(args.models or "")


def cmd_run(args: argparse.Namespace) -> int:
    import logging

    # The labeler logs (with a traceback) every call it turns into a fallback; the runner records
    # each of those itself, so the log would only bury the progress lines. The level is put back
    # afterwards: a caller that runs the command in-process (the tests do) keeps its own logging.
    engine_log = logging.getLogger("callobuzz_rag")
    previous = engine_log.level
    engine_log.setLevel(logging.CRITICAL)
    try:
        return _run(args)
    finally:
        engine_log.setLevel(previous)


def _run(args: argparse.Namespace) -> int:
    from benchmarks.answer_bench import hosts
    from benchmarks.answer_bench.generate import RunAbortError

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    models = _models(args)
    if not models:
        _log("--models (comma-separated) or --candidates is required")
        return EXIT_USAGE
    if args.dry_run and any(not m.model.startswith("fake/") for m in models):
        _log("--dry-run uses fake/* models only (it never calls a provider)")
        return EXIT_USAGE
    if not args.dry_run and any(m.model.startswith("fake/") for m in models):
        _log("fake/* models exist only in --dry-run")
        return EXIT_USAGE
    if args.smoke and len(models) != 1:
        _log("--smoke takes exactly one model")
        return EXIT_USAGE
    settings = _settings(args.dry_run)
    rows = score.load_rows()
    businesses = score.load_businesses()
    model_ids = score.model_turn_ids(rows)
    ids = smoke_turn_ids(rows, model_ids) if args.smoke else model_ids
    if args.limit:
        ids = ids[: args.limit]
    budget = args.budget_usd
    if budget is None:
        if args.smoke:
            budget = 0.05
        elif args.dry_run:
            budget = 1.0
        else:
            _log("--budget-usd is required for a paid run (a hard cap on this run's total spend)")
            return EXIT_USAGE
    if args.dry_run:
        default = f"dry-run-{stamp}"
    elif args.smoke:
        default = f"smoke-{_slug(models[0].key)}-{stamp[:8]}"
    else:
        default = f"{stamp[:8]}-sweep"
    run_dir = _run_dir(args, default)
    provider = args.provider or ("openrouter" if args.dry_run else settings.llm_provider) or ""
    api_key = "dry-run" if args.dry_run else _api_key(settings)
    if not api_key:
        _log("No LLM key: set RAG_LLM_API_KEY in .env (or the environment), or OPENROUTER_API_KEY")
        return EXIT_USAGE
    if provider != "openrouter" and not args.dry_run:
        _log(f"the provider is {provider!r}; the candidates are OpenRouter ids: pass --provider")
        return EXIT_USAGE
    labels_sha = _labels_sha()
    fake_model = fake.FakeLabelModel(rows) if args.dry_run else None
    lookup: Any
    if args.dry_run:
        lookup = fake.FakeLookup()
    elif args.no_host_lookup:
        lookup = hosts.NoLookup()
    else:
        lookup = hosts.OpenRouterLookup(api_key, settings.llm_api_base)
    cfg = run.RunConfig(
        run=run_dir.name,
        out_dir=run_dir,
        models=models,
        rows=rows,
        turn_ids=ids,
        businesses=businesses,
        labels_sha256=labels_sha,
        settings=settings,
        api_key=api_key,
        provider=provider,
        budget_usd=budget,
        max_error_rate=args.max_error_rate,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        max_total_retries=args.max_total_retries,
        backoff_s=0.01 if args.dry_run else args.backoff_s,
        retry_skipped=args.retry_skipped,
        dry_run=args.dry_run,
        smoke=args.smoke,
        inner=fake_model.acompletion if fake_model else None,
        lookup=lookup,
        estimate=(lambda m, p: fake.fake_cost(p, 100)) if args.dry_run else _estimator(provider),
        log=_log,
    )
    _log(
        f"run {run_dir.name}: {len(models)} model(s) x {len(ids)} model-decided turns "
        f"(of {len(rows)}), budget ${budget:.2f}, provider {provider}, concurrency "
        f"{args.concurrency}{' [DRY RUN: fake models, no calls]' if args.dry_run else ''}"
    )
    _log(f"  progress: {run_dir / 'progress.json'}")
    try:
        progress = asyncio.run(run.Runner(cfg).run())
    except RunAbortError as exc:
        _log(f"ABORTED: {exc}")
        return EXIT_STOPPED
    _log(
        f"generation {progress.status}: spent ${progress.spend_usd:.4f}"
        + (f" ({progress.stop_reason})" if progress.stop_reason else "")
    )
    if not args.no_host_lookup:
        _backfill(run_dir, lookup)
    summaries = _score(
        run_dir, rows, businesses, settings, ids if args.smoke or args.limit else None
    )
    if args.smoke:
        return _verify_smoke(run_dir, models[0].key, ids, require_host=not args.no_host_lookup)
    if args.dry_run:
        return _finish_dry_run(run_dir, cfg, summaries)
    return EXIT_STOPPED if progress.status == "stopped" else EXIT_OK


def _labels_sha() -> str:
    from benchmarks.answer_bench.items import file_hash
    from benchmarks.fast_bench import FIXTURES

    return file_hash(FIXTURES / "labels.jsonl") + file_hash(FIXTURES / "label_businesses.json")[:8]


def _backfill(run_dir: Path, lookup: Any) -> int:
    result = asyncio.run(run.backfill_run(run_dir, lookup))
    _log(
        f"hosts: {result.updated} labels updated, {result.unchanged} unchanged, "
        f"{result.unresolved} still unresolved"
    )
    return result.unresolved


def _score(
    run_dir: Path,
    rows: list[dict[str, Any]],
    businesses: dict[str, Any],
    settings: Any,
    ids: list[str] | None,
) -> dict[str, dict[str, Any]]:
    summaries = score.score_run(run_dir, rows, businesses, settings.usd_inr_rate, turn_ids=ids)
    _log(f"scored {len(summaries)} model(s) -> {run_dir / 'summary.md'}")
    return summaries


def _verify_smoke(run_dir: Path, model: str, ids: list[str], *, require_host: bool) -> int:
    _log("")
    _log(verify.table(run_dir))
    _log("")
    problems = verify.verify_smoke(run_dir, model, ids, require_host=require_host)
    if not problems:
        _log(f"VERIFIED: {len(ids)} labels complete, scored and summarised.")
        return EXIT_OK
    _log(f"FAILED: {len(problems)} problem(s):")
    for p in problems[:60]:
        _log(f"  - {p}")
    return EXIT_CHECKS_FAILED


def _finish_dry_run(run_dir: Path, cfg: Any, summaries: dict[str, dict[str, Any]]) -> int:
    """The dry run's own checks: designed outcomes, every field, resumability, the merge."""
    import json

    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH, FastModelBenchmarks

    problems = verify.verify_dry_run(
        run_dir, {m.key: DRY_MODELS[m.key] for m in cfg.models if m.key in DRY_MODELS}
    )
    flaky = [
        r
        for r in run.read_jsonl(run_dir / "labels.jsonl", run.LabelRecord)
        if r.model == "fake/flaky"
    ]
    if flaky and not any(r.retries for r in flaky):
        problems.append("fake/flaky: no retry was recorded")
    for r in run.read_jsonl(run_dir / "labels.jsonl", run.LabelRecord):
        if r.model in ("fake/thinks-anyway", "fake/garbage"):
            break
    else:
        problems.append("the failing fake models left no record to inspect")
    # Resume: a second run of the same directory makes no new call.
    before = sum(cfg.inner.__self__.calls.values())
    again = run.Runner(cfg)
    asyncio.run(again.run())
    after = sum(cfg.inner.__self__.calls.values())
    if after != before:
        problems.append(f"resume made {after - before} new calls; it must make none")
    # Merge into a temporary copy of the results file, never the real one.
    tmp = run_dir / "fast-models.test.json"
    shutil.copyfile(FAST_MODELS_PATH, tmp)
    summaries = score.score_run(
        run_dir, cfg.rows, cfg.businesses, cfg.settings.usd_inr_rate, turn_ids=cfg.turn_ids
    )
    doc = score.merge(summaries, "dry-run", "fast/dry-run/summary.md", tmp)
    FastModelBenchmarks.model_validate(doc)
    merged = {m["key"] for m in doc["models"]}
    if "fake/good" not in merged:
        problems.append("fake/good was not merged into the temporary results file")
    if {"fake/thinks-anyway", "fake/garbage"} & merged:
        problems.append("a failed fake model was merged")
    json.loads(tmp.read_text("utf-8"))
    _log("")
    _log((run_dir / "summary.md").read_text("utf-8"))
    if not problems:
        _log("VERIFIED: every field captured, persisted, scored and merged; resume made no calls.")
        return EXIT_OK
    _log(f"FAILED: {len(problems)} problem(s):")
    for p in problems[:60]:
        _log(f"  - {p}")
    return EXIT_CHECKS_FAILED


def cmd_hosts(args: argparse.Namespace) -> int:
    from benchmarks.answer_bench import hosts

    run_dir = _run_dir(args, "")
    if not (run_dir / "labels.jsonl").exists():
        _log(f"no labels in {run_dir}")
        return EXIT_USAGE
    settings = _settings(False)
    api_key = _api_key(settings)
    if not api_key:
        _log("No LLM key: set RAG_LLM_API_KEY")
        return EXIT_USAGE
    lookup = hosts.OpenRouterLookup(api_key, settings.llm_api_base, total_s=args.wait_s)
    missing = _backfill(run_dir, lookup)
    _score(run_dir, score.load_rows(), score.load_businesses(), settings, None)
    return EXIT_OK if not missing else EXIT_CHECKS_FAILED


def cmd_score(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args, "")
    if not (run_dir / "labels.jsonl").exists():
        _log(f"no labels in {run_dir}")
        return EXIT_USAGE
    settings = _settings(False)
    usd_inr = args.usd_inr if args.usd_inr is not None else settings.usd_inr_rate
    score.score_run(run_dir, score.load_rows(), score.load_businesses(), usd_inr)
    _log((run_dir / "summary.md").read_text("utf-8"))
    return EXIT_OK


def cmd_merge(args: argparse.Namespace) -> int:
    from benchmarks import tables

    from callobuzz_rag.core.benchmark_results import FAST_MODELS_PATH

    run_dir = _run_dir(args, "")
    summaries = verify.summary_rows(run_dir)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    path = Path(args.into) if args.into else FAST_MODELS_PATH
    doc = score.merge(
        summaries,
        stamp,
        f"fast/{run_dir.name}/summary.md",
        path,
        replace_all=args.replace_all,
        method=METHOD,
    )
    if not args.into:
        for out_path, text in tables.outputs(doc).items():
            out_path.write_text(text, encoding="utf-8", newline="\n")
    rec = doc["recommendations"]
    _log(f"{len(doc['models'])} rows in {path}; recommendations: {rec}")
    return EXIT_OK


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.fast_models",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--run", help="run name: the directory under benchmarks/results/fast/")
        sp.add_argument("--out-root", help="results root (default benchmarks/results/fast)")

    r = sub.add_parser("run", help="label the turns (resumable)")
    common(r)
    r.add_argument("--models", help="comma-separated; model@host1+host2 pins OpenRouter hosts")
    r.add_argument("--candidates", action="store_true", help="every candidate in CANDIDATES")
    r.add_argument("--provider", help="LiteLLM provider id (default RAG_LLM_PROVIDER)")
    r.add_argument("--dry-run", action="store_true", help="fake models: free, checks every field")
    r.add_argument("--smoke", action="store_true", help="1 model x 5 turns; exits 1 on any gap")
    r.add_argument("--budget-usd", type=float, help="hard cap on this run's total spend")
    r.add_argument("--max-error-rate", type=float, default=0.2)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--max-retries", type=int, default=3, help="per turn, on 429/5xx")
    r.add_argument("--max-total-retries", type=int, default=25, help="per run")
    r.add_argument("--backoff-s", type=float, default=2.0)
    r.add_argument("--limit", type=int, help="only the first N model-decided turns")
    r.add_argument("--no-host-lookup", action="store_true", help="skip OpenRouter's host lookup")
    r.add_argument("--retry-skipped", action="store_true", help="retry fail-fast/error-cap models")
    r.set_defaults(func=cmd_run)

    h = sub.add_parser("hosts", help="backfill host/cost/reasoning from OpenRouter (free)")
    common(h)
    h.add_argument("--wait-s", type=float, default=60.0, help="wait per missing record")
    h.set_defaults(func=cmd_hosts)

    s = sub.add_parser("score", help="score saved labels (no model calls)")
    common(s)
    s.add_argument("--usd-inr", type=float, help="default RAG_USD_INR_RATE")
    s.set_defaults(func=cmd_score)

    m = sub.add_parser("merge", help="merge the complete models into results/fast-models.json")
    common(m)
    m.add_argument("--replace-all", action="store_true", help="drop every older row")
    m.add_argument("--into", help="merge into this file instead (docs are then not rewritten)")
    m.set_defaults(func=cmd_merge)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    if args.command != "run" and not args.run:
        _log("--run is required")
        return EXIT_USAGE
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
