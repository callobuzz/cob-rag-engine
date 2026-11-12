"""Local CPU model benchmark: which small model is good enough to answer for free.

    python -m benchmarks.local_llm run --dry-run                    # free: fake model, all checks
    python -m benchmarks.local_llm run --url http://127.0.0.1:11434 \\
        --models gemma3:4b,qwen3:4b --run cpu-2026-10-05 --budget-minutes 120
    python -m benchmarks.local_llm score --run cpu-2026-10-05       # re-score saved answers
    python -m benchmarks.local_llm publish --run cpu-2026-10-05     # -> results/local-llm.json

The models run in the `local-llm` compose service (Ollama) and answer the answer benchmark's
frozen English, Hindi and Hinglish items through the engine's own grounded-answer path and
LiteLLM provider (`ollama_chat`), exactly as the Free profile does. Nothing paid is ever called:
every call is checked to be `ollama_chat` to the address given with --url. Scoring is the free
fact gate (no judge). A model is pulled only when the server does not list it (`--no-pull`
refuses instead). Generation is resumable (same `--run`), and `--budget-minutes` caps the wall
clock. The passing rule and the published file are in `callobuzz_rag/core/local_benchmarks.py`;
method: benchmarks/README.md, "Local CPU models".
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

if __package__ in (None, ""):  # `python benchmarks/local_llm.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("LITELLM_MODE", "PRODUCTION")

from benchmarks.answer_bench import ITEMS_PATH
from benchmarks.answer_bench.items import Item, load_items

BENCH = Path(__file__).resolve().parent
RESULTS_ROOT = BENCH / "results" / "local-llm"
PUBLISHED = BENCH / "results" / "local-llm.json"
PROVIDER = "ollama_chat"
LANGS = ("en", "hi", "hi-Latn")
DEFAULT_URL = "http://127.0.0.1:11434"
MAX_TOKENS = 600  # what the Free profile allows an answer
METHOD = (
    "Each model answers the frozen English, Hindi and Hinglish answer items on this CPU through "
    "the engine's grounded-answer path (no hosted model, no judge). Fact score = the answer kept "
    "every required fact and stated no wrong one, after the engine's own fact gate."
)
EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 3
PLAIN_FAILS = {
    "fact_overall": "gets too many facts wrong",
    "fact_language": "is weak in one language",
    "refusals": "refuses questions it should answer",
    "too_slow": "takes too long to answer",
    "slow_first_words": "is slow to start",
    "errors": "did not finish every question",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------------- the server


def _client(url: str, transport: httpx.AsyncBaseTransport | None, timeout: float) -> Any:
    return httpx.AsyncClient(base_url=url, timeout=timeout, transport=transport)


async def server_models(
    url: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, int]:
    """Model name -> size in bytes, as the server lists them (`GET /api/tags`)."""
    async with _client(url, transport, 10.0) as client:
        resp = await client.get("/api/tags")
        resp.raise_for_status()
    return {
        str(m.get("name") or m.get("model")): int(m.get("size") or 0)
        for m in resp.json().get("models", [])
    }


def _has(models: dict[str, int], wanted: str) -> str | None:
    for name in models:
        if name == wanted or (":" not in wanted and name == f"{wanted}:latest"):
            return name
    return None


async def ensure_model(
    url: str,
    model: str,
    *,
    pull: bool,
    transport: httpx.AsyncBaseTransport | None = None,
    log: Callable[[str], None] = _log,
) -> bool:
    """Make sure the server holds `model`. Pulls only when it is missing; with pull=False a
    missing model is an error. Returns True when a download happened."""
    if _has(await server_models(url, transport), model):
        return False
    if not pull:
        raise RuntimeError(f"{model} is not on the server and --no-pull was given")
    log(f"  downloading {model} (once)...")
    async with (
        _client(url, transport, 3600.0) as client,
        client.stream("POST", "/api/pull", json={"model": model, "stream": True}) as r,
    ):
        r.raise_for_status()
        last = ""
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            status = str(json.loads(line).get("status", ""))
            if status and status != last and not status.startswith("pulling "):
                log(f"    {status}")
                last = status
    if not _has(await server_models(url, transport), model):
        raise RuntimeError(f"the download of {model} did not finish")
    return True


async def model_facts(
    url: str, model: str, transport: httpx.AsyncBaseTransport | None = None
) -> dict[str, Any]:
    """Size, parameter count and quantisation of a model on the server."""
    models = await server_models(url, transport)
    name = _has(models, model) or model
    out: dict[str, Any] = {
        "size_mb": round(models.get(name, 0) / 1_000_000) or None,
        "parameter_size": None,
        "quantization": None,
    }
    try:
        async with _client(url, transport, 10.0) as client:
            resp = await client.post("/api/show", json={"model": name})
            resp.raise_for_status()
        details = resp.json().get("details") or {}
        out["parameter_size"] = details.get("parameter_size")
        out["quantization"] = details.get("quantization_level")
    except (httpx.HTTPError, ValueError):
        pass
    return out


def hardware() -> dict[str, Any]:
    """The machine the numbers come from: they mean nothing without it."""
    ram: float | None = None
    try:
        import psutil  # optional

        ram = round(psutil.virtual_memory().total / 1024**3, 1)
    except Exception:
        ram = None
    return {
        "cpu": platform.processor() or platform.machine() or None,
        "cores": os.cpu_count(),
        "ram_gb": ram,
    }


# --------------------------------------------------------------------------------- measuring


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return float(ordered[index])


def _rate(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def measure_model(
    model: str, answers: list[Any], scores: list[Any], items: list[Item]
) -> dict[str, Any]:
    """One published row (without size facts) from a model's saved answers and scores."""
    mine = [a for a in answers if a.model == model]
    done = [a for a in mine if a.error is None]
    scored = {s.item_id: s for s in scores if s.model == model}
    per_lang = {lang: [i for i in items if i.language == lang] for lang in LANGS}
    fact: dict[str, float | None] = {}
    for lang, group in per_lang.items():
        fact[lang] = _rate(
            sum(1 for i in group if i.id in scored and scored[i.id].claim_check_pass), len(group)
        )
    fact["overall"] = _rate(
        sum(1 for i in items if i.id in scored and scored[i.id].claim_check_pass), len(items)
    )
    answerable = [i for i in items if i.type != "unanswerable"]
    refused = sum(1 for i in answerable if i.id in scored and scored[i.id].refusal)
    ttft = [float(a.ttft_ms) for a in done if a.ttft_ms is not None]
    total = [float(a.total_ms) for a in done]
    speeds = [
        a.tokens_out / ((a.total_ms - (a.ttft_ms or 0)) / 1000)
        for a in done
        if a.tokens_out and a.total_ms - (a.ttft_ms or 0) > 0
    ]
    return {
        "model": model,
        "items": len(items),
        "answered": len(done),
        "errors": len(mine) - len(done),
        "fact": {"overall": fact["overall"], **{lang: fact[lang] for lang in LANGS}},
        "refusal_on_answerable": _rate(refused, len(answerable)),
        "ttft_p50_ms": percentile(ttft, 0.5),
        "ttft_p90_ms": percentile(ttft, 0.9),
        "total_p50_ms": percentile(total, 0.5),
        "total_p90_ms": percentile(total, 0.9),
        "tokens_per_s": round(statistics.median(speeds), 1) if speeds else None,
    }


def score_run(run_dir: Path, items: list[Item]) -> list[Any]:
    from benchmarks.answer_bench.records import AnswerRecord, read_jsonl, write_jsonl
    from benchmarks.answer_bench.score import score_all

    answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
    scores = list(score_all(items, answers))
    write_jsonl(run_dir / "scores.jsonl", scores)
    return scores


def run_items(run_dir: Path, items: list[Item]) -> list[Item]:
    """The items a run covered (its progress file's count, else the whole list)."""
    try:
        doc = json.loads((run_dir / "local-run.json").read_text("utf-8"))
        wanted = set(doc["items"])
        return [i for i in items if i.id in wanted]
    except (OSError, ValueError, KeyError):
        return items


def collect_rows(
    run_dir: Path, items: list[Item], facts: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    from benchmarks.answer_bench.records import AnswerRecord, ScoreRecord, read_jsonl

    answers = read_jsonl(run_dir / "answers.jsonl", AnswerRecord)
    scores = read_jsonl(run_dir / "scores.jsonl", ScoreRecord)
    chosen = run_items(run_dir, items)
    models = sorted({a.model for a in answers})
    return [{**measure_model(m, answers, scores, chosen), **facts.get(m, {})} for m in models]


def build_document(run: str, rows: list[dict[str, Any]], machine: dict[str, Any]) -> dict[str, Any]:
    from callobuzz_rag.core.local_benchmarks import normalise

    full = [{"size_mb": None, "parameter_size": None, "quantization": None, **row} for row in rows]
    raw = {
        "schema_version": 1,
        "updated": datetime.now(UTC).strftime("%Y-%m-%d"),
        "run": run,
        "method": METHOD,
        "hardware": machine,
        "models": full,
    }
    return normalise(raw)


def summary_table(doc: dict[str, Any]) -> str:
    """The result in plain words, best first."""
    lines = [
        f"Local CPU models, run {doc['run']} on {doc['hardware'].get('cpu') or 'this machine'}"
    ]
    for row in doc["models"]:
        first = f"{row['ttft_p50_ms'] / 1000:.1f} s" if row["ttft_p50_ms"] is not None else "n/a"
        whole = f"{row['total_p50_ms'] / 1000:.1f} s" if row["total_p50_ms"] is not None else "n/a"
        f = row["fact"]

        def pct(v: float | None) -> str:
            return "n/a" if v is None else f"{round(v * 100)}%"

        verdict = (
            "good enough"
            if row["passed"]
            else "not good enough: " + ", ".join(PLAIN_FAILS[x] for x in row["fails"])
        )
        lines.append(
            f"  {row['model']}: first words after {first}, full answer in {whole}; right on "
            f"{pct(f['overall'])} (English {pct(f['en'])}, Hindi {pct(f['hi'])}, "
            f"Hinglish {pct(f['hi-Latn'])}); {verdict}"
        )
    if doc["best"]:
        lines.append(f"Best: {doc['best']}")
    elif doc["default"]:
        lines.append(
            f"No model passed. Closest: {doc['default']} (slow and less accurate: use it for a "
            "free trial tier only)."
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------------- generating


def pick_items(items: list[Item], per_language: int | None) -> list[Item]:
    """The items in English, Hindi and Hinglish only, the first `per_language` of each."""
    out: list[Item] = []
    for lang in LANGS:
        group = [i for i in items if i.language == lang]
        out.extend(group[:per_language] if per_language else group)
    return out


def guard(url: str, inner: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Every call must be `ollama_chat` to the address given: nothing paid, nothing elsewhere."""

    async def call(**kwargs: Any) -> Any:
        if kwargs.get("custom_llm_provider") != PROVIDER:
            raise AssertionError(
                f"refusing a call to provider {kwargs.get('custom_llm_provider')!r}"
            )
        if kwargs.get("api_base") != url:
            raise AssertionError("refusing a call to an address other than --url")
        return await inner(**kwargs)

    return call


def _settings(timeout: int) -> Any:
    from callobuzz_rag.core.config import Settings

    # Never the developer's env file: this run needs nothing from it.
    return Settings(_env_file=None, llm_timeout_seconds=timeout, llm_max_tokens=MAX_TOKENS)


async def generate(
    *,
    run_dir: Path,
    models: list[str],
    items: list[Item],
    all_items: list[Item],
    url: str,
    timeout: int,
    deadline: float | None,
    dry_run: bool,
    items_sha: str,
    log: Callable[[str], None] = _log,
) -> bool:
    """Answer every item with every model. Returns False when the time budget ran out."""
    from benchmarks.answer_bench import fake
    from benchmarks.answer_bench import generate as gen

    inner: Callable[..., Awaitable[Any]]
    if dry_run:
        inner = fake.FakeModel(all_items).acompletion
    else:
        import litellm

        inner = litellm.acompletion
    cfg = gen.RunConfig(
        run=run_dir.name,
        out_dir=run_dir,
        models=gen.parse_models(",".join(models)),
        variants=["off"],
        items=items,
        items_sha256=items_sha,
        settings=_settings(timeout),
        api_key="local",
        api_base=url,
        provider=PROVIDER,
        temperature=0.1,
        budget_usd=1.0,  # nothing here costs money; the guard refuses any other provider
        concurrency=1,  # one CPU answer at a time, as the engine runs it
        max_retries=1,
        backoff_s=0.01 if dry_run else 5.0,
        dry_run=dry_run,
        inner=guard(url, inner),
        estimate=lambda _m, _v, _p: 0.0,
        log=log,
    )
    runner = gen.Runner(cfg)
    remaining = None if deadline is None else max(1.0, deadline - time.monotonic())
    try:
        await asyncio.wait_for(runner.run(), timeout=remaining)
    except TimeoutError:
        log("Time budget reached; run the same command again to continue where it stopped.")
        return False
    return True


# --------------------------------------------------------------------------------- commands


def _run_dir(args: argparse.Namespace, default: str) -> Path:
    root = Path(args.out_root) if args.out_root else RESULTS_ROOT
    return root / (args.run or default)


def cmd_run(args: argparse.Namespace) -> int:
    all_items = load_items(ITEMS_PATH)
    chosen = pick_items(all_items, args.per_language)
    if not chosen:
        _log("no items to run")
        return EXIT_USAGE
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    dry = args.dry_run
    if dry:
        models = [m.strip() for m in (args.models or "fake/good").split(",") if m.strip()]
        if any(not m.startswith("fake/") for m in models):
            _log("--dry-run answers with fake/* models only (it never calls a server)")
            return EXIT_USAGE
        root = Path(args.out_root) if args.out_root else Path(tempfile.mkdtemp(prefix="local-llm-"))
        args.out_root = str(root)
    else:
        if not args.models:
            _log("--models is required (comma-separated Ollama tags, e.g. gemma3:4b,qwen3:4b)")
            return EXIT_USAGE
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        if any(m.startswith("fake/") for m in models):
            _log("fake/* models exist only in --dry-run")
            return EXIT_USAGE
    run_dir = _run_dir(args, f"dry-run-{stamp}" if dry else f"cpu-{stamp[:8]}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "local-run.json").write_text(
        json.dumps({"items": [i.id for i in chosen], "models": models, "url": args.url}),
        encoding="utf-8",
    )
    _log(
        f"run {run_dir.name}: {len(models)} model(s) x {len(chosen)} items"
        f"{' [DRY RUN: fake model, no calls]' if dry else f' on {args.url}'}"
    )
    facts: dict[str, dict[str, Any]] = {}
    if not dry:
        for model in models:
            try:
                asyncio.run(ensure_model(args.url, model, pull=not args.no_pull))
            except (RuntimeError, httpx.HTTPError) as exc:
                _log(f"cannot use {model}: {exc}")
                return EXIT_FAILED
            facts[model] = asyncio.run(model_facts(args.url, model))
    from benchmarks.answer_bench.items import file_hash

    deadline = (
        time.monotonic() + args.budget_minutes * 60 if args.budget_minutes is not None else None
    )
    finished = asyncio.run(
        generate(
            run_dir=run_dir,
            models=models,
            items=chosen,
            all_items=all_items,
            url=args.url,
            timeout=args.timeout,
            deadline=deadline,
            dry_run=dry,
            items_sha=file_hash(ITEMS_PATH),
        )
    )
    score_run(run_dir, all_items)
    (run_dir / "facts.json").write_text(json.dumps(facts, indent=2), encoding="utf-8")
    doc = build_document(run_dir.name, collect_rows(run_dir, all_items, facts), hardware())
    _log("")
    _log(summary_table(doc))
    return EXIT_OK if finished else EXIT_FAILED


def _load_facts(run_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        loaded = json.loads((run_dir / "facts.json").read_text("utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except (OSError, ValueError):
        return {}


def cmd_score(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args, "")
    if not (run_dir / "answers.jsonl").exists():
        _log(f"no answers in {run_dir}")
        return EXIT_USAGE
    items = load_items(ITEMS_PATH)
    score_run(run_dir, items)
    doc = build_document(
        run_dir.name, collect_rows(run_dir, items, _load_facts(run_dir)), hardware()
    )
    _log(summary_table(doc))
    return EXIT_OK


def cmd_publish(args: argparse.Namespace) -> int:
    run_dir = _run_dir(args, "")
    if not (run_dir / "scores.jsonl").exists():
        _log(f"no scores in {run_dir}: run `score` first")
        return EXIT_USAGE
    items = load_items(ITEMS_PATH)
    doc = build_document(
        run_dir.name, collect_rows(run_dir, items, _load_facts(run_dir)), hardware()
    )
    out = Path(args.out) if args.out else PUBLISHED
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _log(f"wrote {out}")
    _log(summary_table(doc))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmarks.local_llm", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="answer the items with each model")
    run.add_argument("--url", default=DEFAULT_URL, help="the local model server")
    run.add_argument("--models", help="comma-separated Ollama tags")
    run.add_argument("--run", help="run name (rerun with the same name to resume)")
    run.add_argument(
        "--out-root", help="where run folders go (default benchmarks/results/local-llm)"
    )
    run.add_argument("--per-language", type=int, help="only the first N items of each language")
    run.add_argument("--budget-minutes", type=float, help="wall-clock cap; resume to continue")
    run.add_argument("--timeout", type=int, default=300, help="seconds per answer")
    run.add_argument("--no-pull", action="store_true", help="refuse to download a missing model")
    run.add_argument("--dry-run", action="store_true", help="fake model, nothing is called")
    run.set_defaults(func=cmd_run)

    score = sub.add_parser("score", help="re-score saved answers (free)")
    score.add_argument("--run", required=True)
    score.add_argument("--out-root")
    score.set_defaults(func=cmd_score)

    publish = sub.add_parser("publish", help="write benchmarks/results/local-llm.json")
    publish.add_argument("--run", required=True)
    publish.add_argument("--out-root")
    publish.add_argument("--out", help="file to write (default benchmarks/results/local-llm.json)")
    publish.set_defaults(func=cmd_publish)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
