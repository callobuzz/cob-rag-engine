"""Turn-label DEV run: the labeller in-process over a tuning set, no stack needed.

    python benchmarks/labels_dev.py --fast-model openai/gpt-4.1-nano --label nano

Tune the label prompt and rules against tests/fixtures/conversation/labels_dev.jsonl only;
labels.jsonl is the held-out benchmark (benchmarks/labels.py, through the public API) and is
run once, at the end. Each row names a business (tests/fixtures/conversation/
label_businesses.json): its template plus a name and a profile, as an owner would set them.

The LLM comes from the engine's settings (RAG_LLM_*, the repo .env), with the fast model
overridable by --fast-model. Nothing is cached. Prints accuracy per language, business-question
recall, the fallback rate, latency of the model calls, the label prompt's size in tokens, and
every miss; writes nothing unless --out is given.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "tests" / "fixtures" / "conversation"


def build_config(business: dict[str, str]) -> Any:
    from callobuzz_rag.conversation.templates import load_template

    config = load_template(business["template"]).config.model_copy(deep=True)
    config.identity = config.identity.model_copy(
        update={
            "business_name": business["business_name"],
            "business_profile": business["business_profile"],
        }
    )
    return config


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.fast_model:
        os.environ["RAG_LLM_FAST_MODEL"] = args.fast_model
    import tiktoken

    from callobuzz_rag.conversation.labeler import Labeler
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.llm.models import LLMMessage

    if args.prompt_file:  # A/B a prompt variant without editing the module
        import callobuzz_rag.conversation.labeler as labeler_module

        labeler_module.LABEL_PROMPT = Path(args.prompt_file).read_text("utf-8")
    settings = Settings()
    llm = LiteLLMProvider(settings)
    labeler = Labeler(
        llm=llm, cache=None, timeout_s=settings.assistant_label_timeout_ms / 1000, ttl_s=0
    )
    businesses = json.loads((FIXTURES / "label_businesses.json").read_text("utf-8"))
    rows = [
        json.loads(x)
        for x in (FIXTURES / args.fixtures).read_text("utf-8").splitlines()
        if x.strip()
    ]
    if args.only:
        rows = [r for r in rows if r["id"].startswith(args.only)]
    enc = tiktoken.get_encoding("cl100k_base")
    gate = asyncio.Semaphore(args.concurrency)
    prompt_tokens: list[int] = []

    async def one(row: dict[str, Any]) -> dict[str, Any]:
        config = build_config(businesses[row["business"]])
        history = [LLMMessage(role=h["role"], content=h["content"]) for h in row["history"]]
        out = labeler.by_rules(row["text"])
        if out is None:
            prompt_tokens.append(len(enc.encode(labeler._prompt(row["text"], config, history))))
            async with gate:
                out = await labeler.by_model(
                    row["text"],
                    config=config,
                    corpus_id=uuid.uuid4(),
                    scope="dev",
                    history=history,
                )
        return {
            "id": row["id"],
            "text": row["text"],
            "expected": row["label"],
            "got": out.label.value,
            "confidence": out.confidence,
            "source": out.source,
            "language": row["language"],
            "got_language": out.language,
            "ms": out.ms,
        }

    results = await asyncio.gather(*(one(r) for r in rows))
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_lang[r["language"]].append(r)
    business = [r for r in results if r["expected"] == "business_question"]
    model = [r for r in results if r["source"] != "rules"]
    ms = sorted(r["ms"] for r in model if r["source"] == "model")
    summary = {
        "label": args.label,
        "fast_model": settings.llm_fast_model or settings.llm_model,
        "rows": len(results),
        "overall": round(sum(r["got"] == r["expected"] for r in results) / len(results), 3),
        "per_language": {
            k: round(sum(r["got"] == r["expected"] for r in v) / len(v), 3)
            for k, v in sorted(by_lang.items())
        },
        "business_recall": round(
            sum(r["got"] == "business_question" for r in business) / max(len(business), 1), 3
        ),
        "label_language": round(
            sum(r["got_language"] == r["language"] for r in model) / max(len(model), 1), 3
        ),
        "model_rows": len(model),
        "fallback_rate": round(
            sum(r["source"] == "fallback" for r in model) / max(len(model), 1), 3
        ),
        "sources": dict(Counter(r["source"] for r in results)),
        "model_ms_p50": statistics.median(ms) if ms else None,
        "model_ms_p90": ms[int(len(ms) * 0.9)] if ms else None,
        "prompt_tokens_mean": round(statistics.mean(prompt_tokens)) if prompt_tokens else None,
        "confusion": dict(
            Counter(f"{r['expected']}->{r['got']}" for r in results if r["got"] != r["expected"])
        ),
    }
    return {"summary": summary, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fast-model", default="")
    parser.add_argument("--label", default="dev")
    parser.add_argument("--fixtures", default="labels_dev.jsonl")
    parser.add_argument("--only", default="", help="id prefix, e.g. dhl")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default="")
    parser.add_argument("--prompt-file", default="", help="label prompt variant to try")
    args = parser.parse_args()
    out = asyncio.run(run(args))
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps(out["summary"], indent=2, ensure_ascii=False))
    for r in out["results"]:
        if r["got"] != r["expected"]:
            print(
                f"  MISS {r['id']} {r['expected']} -> {r['got']} "
                f"({r['source']}, {r['confidence']:.2f}) {r['text']}"
            )
    if args.out:
        Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
