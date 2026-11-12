"""Does "thinking off" really switch thinking off? One tiny call per model through OpenRouter.

    .venv/Scripts/python -m benchmarks.thinking_check --drift      # free: no key, no calls
    .venv/Scripts/python -m benchmarks.thinking_check --dry-run    # what would be sent
    .venv/Scripts/python -m benchmarks.thinking_check              # paid: about 2 calls a model

`--drift` compares the engine's family table (`callobuzz_rag/llm/thinking.py`) with the live
OpenRouter catalogue's per-model `reasoning` metadata (`GET /api/v1/models`, public): a model
whose thinking is mandatory must not be sent `effort: "none"` / `enabled: false`, and an effort
the model does not list is flagged.

The paid mode streams the same short question through the engine's own `LiteLLMProvider`
(so the exact parameters, fallbacks and reasoning filter are the engine's) twice per model:
once with thinking `off` and once at `provider-default`. Per call it records time to the first
answer token, `usage.completion_tokens_details.reasoning_tokens`, whether a reasoning field
arrived, whether reasoning text reached the visible answer (`check_thinking_off`), the thinking
the engine says it ran at, and OpenRouter's own `usage.cost`. A host may ignore the switch, so
the verdict is per call. Results go to benchmarks/results/thinking-check-<date>-<label>.{json,md}.

The key: OPENROUTER_API_KEY or RAG_LLM_API_KEY in the environment, else the engine's
RAG_LLM_API_KEY from the repo's .env (read by Settings, never printed). Cost: two calls of a
~40-token prompt and at most 300 output tokens per model; well under one US cent per model.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from callobuzz_rag.llm.thinking import (  # noqa: E402
    SNAPSHOT_PATH,
    check_thinking_off,
    describe,
    family_of,
)

CATALOGUE = "https://openrouter.ai/api/v1/models"
# Families the vendor documents as unable to switch thinking fully off even where OpenRouter's
# catalogue says `mandatory: false` (Google: "Gemini 3 Flash and Flash-Lite also do not support
# full thinking-off", ai.google.dev/gemini-api/docs/thinking, 2026-09-25). The vendor wins.
# OpenAI's o-series and codex models and Moonshot's Kimi K3 likewise document no off switch.
VENDOR_SAYS_MINIMUM = frozenset({"gemini-3-flash", "openai-o", "openai-mandatory", "kimi-k3"})
QUESTION = (
    "In one short sentence: what are the opening hours if the shop opens at 9 and closes at 6?"
)

# The answer-model candidates (docs/internal/research/2026-10-03-answer-model-candidates.md),
# the control and the fast-model candidates (2026-10-03-llm-shortlist.md).
DEFAULT_MODELS = (
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "google/gemma-4-26b-a4b-it",
    "google/gemma-4-31b-it",
    "anthropic/claude-haiku-4.5",
    "qwen/qwen3.5-35b-a3b",
    "qwen/qwen3.5-9b",
    "qwen/qwen3.8-27b",
    "qwen/qwen3-next-80b-a3b-instruct",
    "qwen/qwen3-30b-a3b-instruct-2507",
    "openai/gpt-6-luna",
    "openai/gpt-5.4-nano",
    "openai/gpt-4.1-mini",
    "mistralai/mistral-small-2603",
    "mistralai/mistral-small-3.2-24b-instruct",
    "meta-llama/llama-4-scout",
    "meta-llama/llama-3.3-70b-instruct",
    "deepseek/deepseek-v4.1-flash",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "google/gemini-3.1-flash-lite",
    "google/gemini-3.5-flash-lite",
    "nvidia/nemotron-3.5-lightning",
)


# ---------------------------------------------------------------- drift (free)


def catalogue_reasoning() -> dict[str, Any]:
    with urllib.request.urlopen(CATALOGUE, timeout=60) as res:
        data = json.loads(res.read().decode("utf-8"))["data"]
    return {m["id"]: m.get("reasoning") for m in data}


def drift_of(model: str, meta: Any, *, present: bool) -> tuple[list[str], list[str]]:
    """(problems, notes) for what the engine sends for `off` against the catalogue's
    metadata. A problem is a request the model would refuse or a family the table gets
    wrong; a note is worth knowing but harmless, such as a model planned from the catalogue
    alone or a vendor that documents a stricter floor than the catalogue."""
    if not present:
        return ["not in the OpenRouter catalogue"], []
    plan = describe(model, openrouter=True, level="off")
    reasoning = plan["body"].get("reasoning", {})
    family = family_of(model)
    problems: list[str] = []
    notes: list[str] = []
    if family is None:
        notes.append("not in the family table: planned from the catalogue snapshot")
    if meta is None:
        if reasoning:
            problems.append(f"sends {reasoning} to a model the catalogue lists without reasoning")
        elif family is not None and not family.plain:
            notes.append("catalogue lists no reasoning: nothing is sent")
        return problems, notes
    mandatory = bool(meta.get("mandatory"))
    efforts = meta.get("supported_efforts") or []
    if mandatory and (reasoning.get("effort") == "none" or reasoning.get("enabled") is False):
        problems.append("reasoning is mandatory but the first attempt switches it off")
    if family is not None and not family.plain and not family.can_disable and not mandatory:
        message = "catalogue says reasoning can be disabled; the table treats it as minimum-only"
        (notes if family.name in VENDOR_SAYS_MINIMUM else problems).append(message)
    effort = reasoning.get("effort")
    if effort and efforts and effort not in efforts:
        problems.append(f"effort {effort!r} not in supported_efforts {efforts}")
    if family is not None and family.plain:
        problems.append("catalogue lists reasoning; the table says the model never thinks")
    return problems, notes


def write_snapshot() -> int:
    """Refresh the engine's copy of the catalogue's reasoning metadata (the file
    `llm/thinking.py` reads for OpenRouter calls)."""
    meta = catalogue_reasoning()
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    body = {
        "source": CATALOGUE,
        "fetched": stamp,
        "note": "reasoning metadata per model id; null = the model does not reason",
        "models": {k: meta[k] for k in sorted(meta)},
    }
    SNAPSHOT_PATH.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {SNAPSHOT_PATH} ({len(meta)} models, {stamp})")
    return 0


def run_drift(models: list[str], *, every: bool) -> int:
    meta = catalogue_reasoning()
    ids = sorted(meta) if every else models
    rows = []
    for model in ids:
        problems, notes = drift_of(model, meta.get(model), present=model in meta)
        plan = describe(model, openrouter=True, level="off")
        rows.append((model, plan, meta.get(model), problems, notes))
    bad = [r for r in rows if r[3]]
    unknown = [r for r in rows if not r[3] and family_of(r[0]) is None]
    for model, plan, info, problems, notes in rows if not every else bad:
        mark = "DRIFT" if problems else "ok"
        sent = plan["body"].get("reasoning") or "nothing"
        print(f"{mark:5} {model:45} off -> {sent} ({plan['effective']}); catalogue {info}")
        for line in problems + [f"note: {n}" for n in notes]:
            print(f"      - {line}")
    print(
        f"{len(rows)} models checked, {len(bad)} with drift, {len(unknown)} planned from the catalogue alone"
    )
    return 1 if bad else 0


# ---------------------------------------------------------------- live (paid)


def _key() -> str:
    for name in ("OPENROUTER_API_KEY", "RAG_LLM_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    from callobuzz_rag.core.config import Settings

    return Settings().llm_api_key.get_secret_value()


async def one_call(model: str, level: str, key: str, max_tokens: int) -> dict[str, Any]:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm.cost import request_cost_of
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.llm.models import LLMMessage
    from callobuzz_rag.llm.stream_stats import collect_stream_stats

    settings = Settings(
        _env_file=None,
        llm_provider="openrouter",
        llm_model=model,
        llm_api_key=key,
        llm_thinking=level,
        llm_temperature=0.0,
        llm_max_tokens=max_tokens,
        llm_timeout_seconds=90,
    )
    provider = LiteLLMProvider(settings)
    started = time.monotonic()
    first: float | None = None
    parts: list[str] = []
    error = None
    with collect_stream_stats() as stats:
        try:
            async for delta in provider.complete_stream(
                [LLMMessage(role="user", content=QUESTION)]
            ):
                if first is None:
                    first = time.monotonic()
                parts.append(delta)
        except Exception as exc:  # report, do not stop the run
            error = str(exc)[:300]
    spent = request_cost_of(provider)
    answer = "".join(parts)
    reasoning_tokens = spent.reasoning_tokens if spent and spent.calls else None
    if stats.reasoning_tokens:
        reasoning_tokens = stats.reasoning_tokens
    check = check_thinking_off(
        content=answer,
        reasoning_tokens=reasoning_tokens,
        reasoning_text="(reasoning field)" if stats.first_reasoning_at is not None else None,
        thinking_effective=provider.thinking_effective(),
    )
    return {
        "model": model,
        "level": level,
        "error": error,
        "ttft_ms": round((first - started) * 1000) if first else None,
        "total_ms": round((time.monotonic() - started) * 1000),
        "reasoning_tokens": reasoning_tokens,
        "completion_tokens": spent.completion_tokens if spent else None,
        "cost_usd": spent.cost_usd if spent else None,
        "reasoning_field": stats.first_reasoning_at is not None,
        "check": check.as_dict(),
        "answer": answer[:200],
    }


async def run_live(models: list[str], label: str, max_tokens: int) -> int:
    key = _key()
    if not key:
        print("No key: set OPENROUTER_API_KEY (or RAG_LLM_API_KEY in .env).")
        return 2
    rows: list[dict[str, Any]] = []
    for model in models:
        for level in ("off", "provider-default"):
            row = await one_call(model, level, key, max_tokens)
            rows.append(row)
            print(
                f"{model:42} {level:16} ttft={row['ttft_ms']}ms "
                f"reasoning={row['reasoning_tokens']} effective={row['check']['thinking_effective']} "
                f"ok={row['check']['thinking_ok']} cost={row['cost_usd']}"
                + (f" ERROR {row['error']}" if row["error"] else "")
            )
    spend = sum(r["cost_usd"] or 0.0 for r in rows)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = HERE / "results" / f"thinking-check-{stamp}-{label}"
    out.with_suffix(".json").write_text(
        json.dumps(
            {"date": stamp, "label": label, "spend_usd": spend, "rows": rows},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        f"# Thinking-off check — {stamp} ({label})",
        "",
        f"Spend: ${spend:.5f} (OpenRouter `usage.cost`). One streamed call per model and level, "
        "same question, temperature 0.",
        "",
        "| Model | Off: effective | Off: reasoning tokens | Off: clean | Off TTFT ms | "
        "Default: reasoning tokens | Default TTFT ms |",
        "|---|---|---|---|---|---|---|",
    ]
    by = {(r["model"], r["level"]): r for r in rows}
    for model in models:
        off, default = by[(model, "off")], by[(model, "provider-default")]
        lines.append(
            f"| `{model}` | {off['check']['thinking_effective']} | {off['reasoning_tokens']} | "
            f"{'yes' if off['check']['thinking_ok'] else 'NO ' + ', '.join(off['check']['leaked'])}"
            f"{' (error)' if off['error'] else ''} | {off['ttft_ms']} | "
            f"{default['reasoning_tokens']} | {default['ttft_ms']} |"
        )
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md; spend ${spend:.5f}")
    failed = [
        r
        for r in rows
        if r["level"] == "off"
        and (r["error"] or not r["check"]["thinking_ok"])
        and r["check"]["thinking_effective"] == "off"
    ]
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument("--label", default="local")
    parser.add_argument("--max-tokens", type=int, default=300)
    parser.add_argument("--drift", action="store_true", help="compare with the live catalogue")
    parser.add_argument("--all", action="store_true", help="with --drift: every catalogue model")
    parser.add_argument(
        "--snapshot", action="store_true", help="refresh llm/openrouter_reasoning.json"
    )
    parser.add_argument("--dry-run", action="store_true", help="print what would be sent")
    args = parser.parse_args()
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if args.snapshot:
        sys.exit(write_snapshot())
    if args.drift:
        sys.exit(run_drift(models, every=args.all))
    if args.dry_run:
        for model in models:
            plan = describe(model, openrouter=True, level="off")
            print(
                f"{model:45} {plan['family']:22} off -> {plan['body'] or plan['kwargs'] or 'nothing'}"
                f" ({plan['effective']}{', minimum only' if plan['minimum_only'] else ''})"
            )
        return
    sys.exit(asyncio.run(run_live(models, args.label, args.max_tokens)))


if __name__ == "__main__":
    main()
