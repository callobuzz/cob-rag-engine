"""Answer-model benchmark: answer quality, speed and cost of answer models on IDENTICAL context.

    python -m benchmarks.answers run --dry-run                       # free: fake model, all checks
    python -m benchmarks.answers run --models openai/gpt-4.1-mini --smoke     # ~1 cent, 3 items
    python -m benchmarks.answers run --models a,b --thinking off,low --run my-run --budget-usd 2
    python -m benchmarks.answers score --run my-run                  # re-score saved answers
    python -m benchmarks.answers regate --all-smoke                  # re-run the fact gate, free
    python -m benchmarks.answers smoke-summary                       # consolidated smoke table
    python -m benchmarks.answers judge --run my-run --judge-model openai/gpt-4.1 --budget-usd 0.5
    python -m benchmarks.answers publish --run my-run                # -> results/answer-models.json

Every model answers the frozen items in benchmarks/data/answers/items.jsonl from exactly the
same passages, through the engine's own grounded-answer path and LiteLLM provider (streamed, so
TTFT is real). Generation, scoring and judging are separate steps; generation is resumable,
budget-capped and fail-fast. Method and cost controls: benchmarks/README.md, "Answer models".
Needs no running stack: only the engine's LLM settings (RAG_LLM_API_KEY, RAG_LLM_PROVIDER).
"""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # `python benchmarks/answers.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

from benchmarks.answer_bench import ITEMS_PATH, RESULTS_ROOT
from benchmarks.answer_bench.items import SMOKE_ITEM_IDS, load_items, select_items

EXIT_OK, EXIT_CHECKS_FAILED, EXIT_STOPPED, EXIT_USAGE = 0, 1, 2, 3


def _log(msg: str) -> None:
    print(msg, flush=True)


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:48]


def _settings() -> Any:
    from callobuzz_rag.core.config import Settings

    return Settings()


# What a grounded answer typically costs in output tokens (the frozen items' answers run 80-300
# tokens), used only for the FIRST call of a model x variant; after that the runner reserves
# 1.25 x the dearest finished call. Reserving max_tokens x price would stop a tiny, budgeted
# smoke run before its second item. The hard cap itself is unchanged: spent + reserved +
# estimate must stay under it, and the spend is the provider's actual cost.
TYPICAL_OUTPUT_TOKENS = {"off": 300}
TYPICAL_OUTPUT_THINKING = 1500


def _estimator(provider: str) -> Any:
    from callobuzz_rag.llm.cost import priced_cost

    def estimate(model: str, variant: str, prompt_tokens: int) -> float | None:
        completion = TYPICAL_OUTPUT_TOKENS.get(variant, TYPICAL_OUTPUT_THINKING)
        try:
            cost = priced_cost(model, provider, prompt_tokens, completion)
        except Exception:
            return None
        return cost * 1.5 if cost else None

    return estimate


def _run_dir(args: argparse.Namespace, default: str) -> Path:
    root = Path(args.out_root) if args.out_root else RESULTS_ROOT
    return root / (args.run or default)


def cmd_run(args: argparse.Namespace) -> int:
    from benchmarks.answer_bench import fake, generate, hosts
    from benchmarks.answer_bench import items as items_mod
    from benchmarks.answer_bench.thinking import check_level

    items_path = Path(args.items_file) if args.items_file else ITEMS_PATH
    all_items = load_items(items_path)
    variants = [check_level(v.strip()) for v in args.thinking.split(",") if v.strip()]
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    settings = _settings()
    if args.dry_run:
        models = generate.parse_models(args.models or "fake/good")
        if any(not m.model.startswith("fake/") for m in models):
            _log("--dry-run answers with fake/* models only (it never calls a provider)")
            return EXIT_USAGE
        if not args.models and args.thinking == "off":
            variants = ["off", "low"]
    else:
        if not args.models:
            _log("--models is required (comma-separated, e.g. openai/gpt-4.1-mini,qwen/qwen3-32b)")
            return EXIT_USAGE
        models = generate.parse_models(args.models)
        if any(m.model.startswith("fake/") for m in models):
            _log("fake/* models exist only in --dry-run")
            return EXIT_USAGE
    if args.smoke:
        if len(models) != 1:
            _log("--smoke takes exactly one model")
            return EXIT_USAGE
        ids = list(SMOKE_ITEM_IDS)
    else:
        ids = [x.strip() for x in args.items.split(",")] if args.items else None
    chosen = select_items(all_items, ids=ids, limit=args.limit)
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
        default = f"{stamp[:8]}-{_slug('-'.join(m.model.split('/')[-1] for m in models))}"
    run_dir = _run_dir(args, default)
    provider = args.provider or settings.llm_provider or ""
    api_key = "dry-run" if args.dry_run else settings.llm_api_key.get_secret_value()
    if not api_key:
        _log("No LLM key: set RAG_LLM_API_KEY in .env (or the environment)")
        return EXIT_USAGE
    inner = fake.FakeModel(all_items).acompletion if args.dry_run else None
    if args.dry_run:
        lookup: Any = fake.FakeLookup()
    elif args.no_host_lookup or provider != "openrouter":
        lookup = hosts.NoLookup()
    else:
        lookup = hosts.OpenRouterLookup(api_key, settings.llm_api_base)
    cfg = generate.RunConfig(
        run=run_dir.name,
        out_dir=run_dir,
        models=models,
        variants=variants,
        items=chosen,
        items_sha256=items_mod.file_hash(items_path),
        settings=settings,
        api_key=api_key,
        api_base=settings.llm_api_base,
        provider=provider,
        temperature=args.temperature if args.temperature is not None else settings.llm_temperature,
        budget_usd=budget,
        max_error_rate=args.max_error_rate,
        concurrency=args.concurrency,
        max_retries=args.max_retries,
        max_total_retries=args.max_total_retries,
        backoff_s=0.01 if args.dry_run else args.backoff_s,
        retry_skipped=args.retry_skipped,
        dry_run=args.dry_run,
        smoke=args.smoke,
        inner=inner,
        lookup=lookup,
        estimate=(lambda m, v, p: fake.fake_cost(p, 600)) if args.dry_run else _estimator(provider),
        log=_log,
    )
    _log(
        f"run {run_dir.name}: {len(models)} model(s) x {len(variants)} thinking level(s) x "
        f"{len(chosen)} items, budget ${budget:.2f}, provider {provider or '(from model)'}"
        f"{' [DRY RUN: fake model, no calls]' if args.dry_run else ''}"
    )
    _log(f"  progress: {run_dir / 'progress.json'}")
    try:
        progress = asyncio.run(generate.Runner(cfg).run())
    except generate.RunAbortError as exc:
        _log(f"ABORTED: {exc}")
        return EXIT_STOPPED
    _log(
        f"generation {progress.status}: spent ${progress.spend_usd:.4f}"
        + (f" ({progress.stop_reason})" if progress.stop_reason else "")
    )
    _score(run_dir, all_items, settings.usd_inr_rate)
    model_keys = [m.key for m in models]
    if args.smoke:
        return _verify(run_dir, chosen, model_keys, variants, require_host=not args.no_host_lookup)
    if args.dry_run:
        return _finish_dry_run(run_dir, all_items, chosen, model_keys, variants, settings)
    return EXIT_STOPPED if progress.status == "stopped" else EXIT_OK


def _score(run_dir: Path, items: list[Any], usd_inr: float) -> None:
    from benchmarks.answer_bench import summary
    from benchmarks.answer_bench.records import AnswerRecord, read_jsonl, write_jsonl
    from benchmarks.answer_bench.score import score_all

    answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
    scores = score_all(items, answers)
    write_jsonl(run_dir / "scores.jsonl", list(scores))
    doc = summary.write(run_dir, total_items=_run_items(run_dir, items), usd_inr=usd_inr)
    _log(
        f"scored {len(scores)} answers -> {run_dir / 'scores.jsonl'}; "
        f"summary -> {run_dir / 'summary.md'} ({len(doc.rows)} rows)"
    )


def _run_items(run_dir: Path, items: list[Any]) -> int:
    """How many items the run covers: the progress file's per-model total, else all."""
    from benchmarks.answer_bench.records import Progress

    try:
        progress = Progress.model_validate_json((run_dir / "progress.json").read_text("utf-8"))
        return max((m.total for m in progress.models.values()), default=len(items))
    except (OSError, ValueError):
        return len(items)


def _verify(
    run_dir: Path,
    items: list[Any],
    models: list[str],
    variants: list[str],
    *,
    require_host: bool,
    expect_judge: bool = False,
) -> int:
    from benchmarks.answer_bench.verify import table, verify_run

    report = verify_run(
        run_dir, items, models, variants, require_host=require_host, expect_judge=expect_judge
    )
    _log("")
    _log(table(report))
    _log("")
    if report.ok:
        _log(f"VERIFIED: {len(report.rows)} answer records complete, scored and summarised.")
        return EXIT_OK
    _log(f"FAILED: {len(report.problems)} problem(s):")
    for p in report.problems[:60]:
        _log(f"  - {p}")
    return EXIT_CHECKS_FAILED


def _judge_provider_factory(settings: Any, api_key: str, provider: str) -> Any:
    import dataclasses

    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    names = {f.name for f in dataclasses.fields(ResolvedLLMConfig)}

    def make(model: str) -> Any:
        kw: dict[str, Any] = {
            "model": model,
            "api_key": api_key,
            "base_url": settings.llm_api_base,
            "temperature": 0.0,
            "max_tokens": 400,
            "provider": provider or None,
            "timeout": settings.llm_timeout_seconds,
        }
        if "reasoning_effort" in names:
            kw["reasoning_effort"] = None
        return LiteLLMProvider.from_resolved_config(ResolvedLLMConfig(**kw))

    return make


def _finish_dry_run(
    run_dir: Path,
    all_items: list[Any],
    chosen: list[Any],
    models: list[str],
    variants: list[str],
    settings: Any,
) -> int:
    from benchmarks.answer_bench import fake
    from benchmarks.answer_bench.judge import judge_run

    factory = _judge_provider_factory(settings, "dry-run", "openrouter")
    asyncio.run(
        judge_run(
            run_dir,
            chosen,
            judge_model="fake/judge",
            provider_factory=factory,
            budget_usd=1.0,
            inner=fake.FakeModel(all_items).acompletion,
            log=lambda m: None,
        )
    )
    _score(run_dir, all_items, settings.usd_inr_rate)
    code = _verify(run_dir, chosen, models, variants, require_host=True, expect_judge=True)
    _log((run_dir / "summary.md").read_text("utf-8"))
    return code


def cmd_score(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args, "")
    if not (run_dir / "answers.jsonl").exists():
        _log(f"no answers in {run_dir}")
        return EXIT_USAGE
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    usd_inr = args.usd_inr if args.usd_inr is not None else _settings().usd_inr_rate
    _score(run_dir, items, usd_inr)
    _log((run_dir / "summary.md").read_text("utf-8"))
    return EXIT_OK


def cmd_judge(args: argparse.Namespace) -> int:
    from benchmarks.answer_bench import fake
    from benchmarks.answer_bench.judge import judge_run

    run_dir = _run_dir(args, "")
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    settings = _settings()
    dry = args.judge_model.startswith("fake/")
    provider = args.provider or settings.llm_provider or ""
    api_key = "dry-run" if dry else settings.llm_api_key.get_secret_value()
    if not api_key:
        _log("No LLM key: set RAG_LLM_API_KEY in .env (or the environment)")
        return EXIT_USAGE
    asyncio.run(
        judge_run(
            run_dir,
            items,
            judge_model=args.judge_model,
            provider_factory=_judge_provider_factory(settings, api_key, provider),
            budget_usd=args.budget_usd,
            inner=fake.FakeModel(items).acompletion if dry else None,
            log=_log,
        )
    )
    _score(run_dir, items, settings.usd_inr_rate)
    return EXIT_OK


def _hosts_runs(args: argparse.Namespace) -> list[Path]:
    root = Path(args.out_root) if args.out_root else RESULTS_ROOT
    if args.all_smoke:
        return sorted(p for p in root.glob("smoke-*") if (p / "answers.jsonl").exists())
    return [_run_dir(args, "")]


def cmd_hosts(args: argparse.Namespace) -> int:
    """Fill host / cost / native reasoning tokens from OpenRouter's generation records. Free."""
    from benchmarks.answer_bench import hosts
    from benchmarks.answer_bench.backfill import backfill_run

    runs = _hosts_runs(args)
    if not runs:
        _log("no runs to backfill")
        return EXIT_USAGE
    settings = _settings()
    api_key = settings.llm_api_key.get_secret_value()
    if not api_key:
        _log("No LLM key: set RAG_LLM_API_KEY")
        return EXIT_USAGE
    lookup = hosts.OpenRouterLookup(api_key, settings.llm_api_base, total_s=args.wait_s)
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    missing = 0
    for run_dir in runs:
        if not (run_dir / "answers.jsonl").exists():
            _log(f"no answers in {run_dir}")
            return EXIT_USAGE
        result = asyncio.run(backfill_run(run_dir, lookup))
        _log(
            f"{run_dir.name}: {result.updated} updated, {result.unchanged} already complete, "
            f"{result.unresolved} still unresolved"
        )
        missing += result.unresolved
        if result.updated:
            _score(run_dir, items, settings.usd_inr_rate)
    return EXIT_OK if not missing else EXIT_CHECKS_FAILED


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-check a saved run (no generation): the smoke gate, run from disk."""
    from benchmarks.answer_bench.records import RunManifest

    run_dir = _run_dir(args, "")
    if not (run_dir / "run.json").exists():
        _log(f"no run at {run_dir}")
        return EXIT_USAGE
    manifest = RunManifest.model_validate_json((run_dir / "run.json").read_text("utf-8"))
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    chosen = select_items(items, ids=list(SMOKE_ITEM_IDS) if manifest.smoke else None, limit=None)
    return _verify(
        run_dir, chosen, manifest.models, manifest.variants, require_host=not args.no_host
    )


def cmd_regate(args: argparse.Namespace) -> int:
    """Re-run the engine's grounded-answer path (fact gate, length-stop handling) over the
    saved raw answers, rewrite `answer_released` / `gate_removed` / `route`, re-score. Free."""
    from benchmarks.answer_bench.regate import regate_run

    runs = _hosts_runs(args)
    if not runs:
        _log("no runs to regate")
        return EXIT_USAGE
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    settings = _settings()
    for run_dir in runs:
        if not (run_dir / "answers.jsonl").exists():
            _log(f"no answers in {run_dir}")
            return EXIT_USAGE
        result = regate_run(run_dir, items, settings)
        _log(
            f"{run_dir.name}: {result.changed} released answers changed, "
            f"{result.unchanged} unchanged, {result.skipped} error records skipped"
        )
        _score(run_dir, items, settings.usd_inr_rate)
    return EXIT_OK


def cmd_smoke_summary(args: argparse.Namespace) -> int:
    """Write the consolidated smoke summary from every smoke-* run (no model calls)."""
    from benchmarks.answer_bench.smoke_summary import write

    root = Path(args.out_root) if args.out_root else RESULTS_ROOT
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = Path(args.out) if args.out else root / f"smoke-summary-{stamp}.md"
    items = load_items(Path(args.items_file) if args.items_file else ITEMS_PATH)
    rows = write(root, out, items, args.title or f"Answer benchmark smoke, {stamp}")
    _log(f"{len(rows)} smoke runs -> {out}")
    _log(out.read_text("utf-8"))
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    from benchmarks.answer_bench.summary import PUBLISHED, publish

    doc = publish(_run_dir(args, ""))
    _log(f"{len(doc.rows)} rows -> {PUBLISHED}")
    return EXIT_OK


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m benchmarks.answers",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--run", help="run name: the directory under benchmarks/results/answers/")
        sp.add_argument("--out-root", help="results root (default benchmarks/results/answers)")
        sp.add_argument(
            "--items-file", help="item set (default benchmarks/data/answers/items.jsonl)"
        )
        sp.add_argument("--provider", help="LiteLLM provider id (default RAG_LLM_PROVIDER)")

    r = sub.add_parser("run", help="generate answers (resumable)")
    common(r)
    r.add_argument("--models", help="comma-separated; model@host1+host2 pins OpenRouter hosts")
    r.add_argument(
        "--thinking",
        default="off",
        help="comma-separated levels: off,minimal,low,medium,high,provider-default",
    )
    r.add_argument("--dry-run", action="store_true", help="fake model: free, checks every field")
    r.add_argument("--smoke", action="store_true", help="1 model x 3 items; exits 1 on any gap")
    r.add_argument("--budget-usd", type=float, help="hard cap on this run's total spend")
    r.add_argument("--max-error-rate", type=float, default=0.2)
    r.add_argument("--concurrency", type=int, default=2)
    r.add_argument("--max-retries", type=int, default=3, help="per item, on 429/5xx")
    r.add_argument("--max-total-retries", type=int, default=25, help="per run")
    r.add_argument("--backoff-s", type=float, default=2.0)
    r.add_argument("--items", help="comma-separated item ids (default: all)")
    r.add_argument("--limit", type=int, help="only the first N items")
    r.add_argument("--temperature", type=float, help="default RAG_LLM_TEMPERATURE")
    r.add_argument(
        "--no-host-lookup",
        action="store_true",
        help="skip OpenRouter's generation lookup (host then unknown)",
    )
    r.add_argument(
        "--retry-skipped",
        action="store_true",
        help="try model x variants that fail-fast or the error cap stopped earlier",
    )
    r.set_defaults(func=cmd_run)

    s = sub.add_parser("score", help="score saved answers (no model calls)")
    common(s)
    s.add_argument("--usd-inr", type=float, help="default RAG_USD_INR_RATE")
    s.set_defaults(func=cmd_score)

    j = sub.add_parser("judge", help="LLM judge of Hindi/Hinglish fluency (resumable)")
    common(j)
    j.add_argument("--judge-model", required=True)
    j.add_argument("--budget-usd", type=float, required=True)
    j.set_defaults(func=cmd_judge)

    h = sub.add_parser("hosts", help="backfill host/cost/reasoning from OpenRouter (free)")
    common(h)
    h.add_argument("--all-smoke", action="store_true", help="every smoke-* run under the root")
    h.add_argument(
        "--wait-s", type=float, default=60.0, help="how long to wait for each missing record"
    )
    h.set_defaults(func=cmd_hosts)

    v = sub.add_parser("verify", help="re-check a saved run without generating (smoke gate)")
    common(v)
    v.add_argument("--no-host", action="store_true", help="do not require the host")
    v.set_defaults(func=cmd_verify)

    rg = sub.add_parser(
        "regate", help="re-run the engine's fact gate over saved raw answers and re-score (free)"
    )
    common(rg)
    rg.add_argument("--all-smoke", action="store_true", help="every smoke-* run under the root")
    rg.set_defaults(func=cmd_regate)

    ss = sub.add_parser("smoke-summary", help="consolidated table of every smoke-* run (free)")
    common(ss)
    ss.add_argument("--out", help="default <root>/smoke-summary-<today>.md")
    ss.add_argument("--title", help="the page title")
    ss.set_defaults(func=cmd_smoke_summary)

    pub = sub.add_parser("publish", help="merge a run's summary into results/answer-models.json")
    common(pub)
    pub.set_defaults(func=cmd_publish)
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args(argv)
    no_run = ("run", "smoke-summary")
    if args.command not in no_run and not args.run and not getattr(args, "all_smoke", False):
        _log("--run is required")
        return EXIT_USAGE
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
