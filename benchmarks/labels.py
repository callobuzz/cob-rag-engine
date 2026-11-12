"""Turn-label benchmark (conversation layer C1): accuracy of the director's labels in
English, Hindi and Hinglish over tests/fixtures/conversation/labels.jsonl.

    python benchmarks/labels.py --label gpu

Needs a running stack with an LLM (engine default RAG_LLM_* or --llm-* flags, sent as
X-LLM-* headers) and RAG_ADMIN_API_KEY. Every row names the business it was written for
(a diagnostic lab, a saree store, a coaching institute: tests/fixtures/conversation/
label_businesses.json); one knowledge base per business is created with that business's
template, name and profile published as its assistant, because "off topic" and "a general
question about the field" only mean something once the assistant knows the field. Every
fixture is asked through POST /api/v1/query/stream with assistant.debug and the `meta` event
read (label, language, label source), then the stream is closed so no answer is generated.

The set is held out: the label prompt and rules are tuned on labels_dev.jsonl
(benchmarks/labels_dev.py) and this runs once per change, at the end. Bars (design §11, C1 spec): overall >= 90 %, each language
>= 85 %, business_question recall >= 97 %, language >= 95 %.

`--thinking off|minimal|low|medium|high|provider-default` sets the thinking of the label
model (labels are a fast-model call, so it is stored as each knowledge base's
`llm_config.fast_thinking`); unset = the engine's RAG_LLM_FAST_THINKING. The result records it.

Run it with the stack's real default fast model (RAG_LLM_FAST_MODEL): the label call has a
timeout (RAG_ASSISTANT_LABEL_TIMEOUT_MS) and a model that thinks for seconds will time out and
silently degrade every turn to business_question. The result therefore also reports
`fallback_rate`: the share of model-decided turns that fell back (timeout, bad JSON, no
credential). A high rate with a good business_question recall is a failed run, not a pass.
"""

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from cob_rag_client import CobRag  # noqa: E402
from run import admin_key, bench_tenant  # noqa: E402
from thinking_args import LEVELS, llm_config, recorded  # noqa: E402

FIXTURES = HERE.parent / "tests" / "fixtures" / "conversation" / "labels.jsonl"
BUSINESSES = FIXTURES.parent / "label_businesses.json"
BARS = {"overall": 0.90, "per_language": 0.85, "business_recall": 0.97, "language": 0.95}
MAX_FALLBACK_RATE = 0.05


def meta_for(
    client: httpx.Client,
    api: str,
    key: str,
    corpus_id: str,
    row: dict[str, Any],
    llm: dict[str, str],
) -> dict[str, Any]:
    conv_id = None
    if row["history"]:
        conv = client.post(
            f"{api}/api/v1/corpora/{corpus_id}/conversations",
            headers={"X-API-Key": key},
            json={"title": "label-bench"},
        ).json()
        conv_id = conv["id"]
        for turn in row["history"][::2]:  # replay the visitor's earlier messages
            client.post(
                f"{api}/api/v1/corpora/{corpus_id}/conversations/{conv_id}/messages",
                headers={"X-API-Key": key, **llm},
                json={"content": turn["content"]},
            )
    body = {
        "query": row["text"],
        "corpus_id": corpus_id,
        "assistant": {"debug": True, **({"conversation_id": conv_id} if conv_id else {})},
    }
    with client.stream(
        "POST",
        f"{api}/api/v1/query/stream",
        json=body,
        headers={"X-API-Key": key, **llm},
    ) as res:
        res.raise_for_status()
        event = None
        for line in res.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "meta":
                return json.loads(line[6:])  # type: ignore[no-any-return]
    raise RuntimeError(f"no meta event for {row['id']}")


def publish_business(admin: CobRag, corpus_id: str, business: dict[str, str]) -> None:
    """The knowledge base's assistant as an owner would set it up: the business's template,
    name and profile, published."""
    draft = admin.assistant_draft_from_template(
        corpus_id, business["template"], keep_identity=False
    )
    draft = admin.patch_assistant_draft(
        corpus_id,
        {
            "identity": {
                "business_name": business["business_name"],
                "business_profile": business["business_profile"],
            }
        },
        base_revision=draft["draft_revision"],
    )
    admin.publish_assistant(corpus_id, base_revision=draft["draft_revision"], note="labels")


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    ok = [r for r in results if r["got"] == r["expected"]]
    by_lang: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_lang[r["language"]].append(r)
    business = [r for r in results if r["expected"] == "business_question"]
    by_model = [r for r in results if r["source"] != "rules"]
    return {
        "overall": round(len(ok) / n, 3),
        "per_language": {
            k: round(sum(r["got"] == r["expected"] for r in v) / len(v), 3)
            for k, v in by_lang.items()
        },
        "business_recall": round(
            sum(r["got"] == "business_question" for r in business) / len(business), 3
        ),
        "language": round(sum(r["got_language"] == r["language"] for r in results) / n, 3),
        "model_rows": len(by_model),
        "fallback_rate": round(
            sum(r["source"] == "fallback" for r in by_model) / max(len(by_model), 1), 3
        ),
        "sources": dict(Counter(r["source"] for r in results)),
        "confusion": dict(
            Counter(f"{r['expected']}->{r['got']}" for r in results if r["got"] != r["expected"])
        ),
    }


def passed(summary: dict[str, Any]) -> bool:
    return bool(
        summary["overall"] >= BARS["overall"]
        and min(summary["per_language"].values()) >= BARS["per_language"]
        and summary["business_recall"] >= BARS["business_recall"]
        and summary["language"] >= BARS["language"]
        and summary["fallback_rate"] <= MAX_FALLBACK_RATE
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local")
    parser.add_argument("--llm-provider", default="")
    parser.add_argument("--llm-model", default="")
    parser.add_argument("--llm-api-key", default="")
    parser.add_argument(
        "--thinking",
        choices=LEVELS,
        default=None,
        help="Thinking of the label (fast) model, stored as llm_config.fast_thinking; "
        "unset = the engine's RAG_LLM_FAST_THINKING.",
    )
    args = parser.parse_args()
    thinking = recorded(thinking=None, fast_thinking=args.thinking)["fast_thinking"]
    kb_llm = llm_config(thinking=None, fast_thinking=args.thinking)
    llm = {
        k: v
        for k, v in {
            "X-LLM-Provider": args.llm_provider,
            "X-LLM-Model": args.llm_model,
            "X-LLM-API-Key": args.llm_api_key,
        }.items()
        if v
    }

    key = admin_key()
    admin = CobRag(args.api, api_key=key, timeout=300)
    rows = [json.loads(x) for x in FIXTURES.read_text("utf-8").splitlines() if x.strip()]
    businesses = json.loads(BUSINESSES.read_text("utf-8"))
    tenant = bench_tenant(admin)
    corpora: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    try:
        for name in sorted({r["business"] for r in rows}):
            corpora[name] = admin.create_corpus(
                f"labels-{name}-{int(time.time())}",
                tenant_id=tenant,
                **({"llm_config": kb_llm} if kb_llm else {}),
            )
            publish_business(admin, corpora[name]["id"], businesses[name])
        with httpx.Client(timeout=120) as client:
            for row in rows:
                corpus_id = corpora[row["business"]]["id"]
                meta = meta_for(client, args.api, key, corpus_id, row, llm)
                results.append(
                    {
                        "id": row["id"],
                        "expected": row["label"],
                        "got": meta["label"],
                        "source": meta.get("label_source") or "unknown",
                        "language": row["language"],
                        "got_language": meta["language"],
                    }
                )
    finally:
        for corpus in corpora.values():
            admin.delete_corpus(corpus["id"], corpus["name"])

    n = len(results)
    summary = summarise(results)
    ok = passed(summary)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = HERE / "results" / f"labels-{stamp}-{args.label}"
    out.with_suffix(".json").write_text(
        json.dumps(
            {
                "date": stamp,
                "label": args.label,
                "thinking": thinking,
                "passed": ok,
                "bars": {**BARS, "max_fallback_rate": MAX_FALLBACK_RATE},
                "summary": summary,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    confusions = sorted(summary["confusion"].items(), key=lambda kv: -kv[1])[:10]
    lines = [
        f"# Turn-label benchmark — {stamp} ({args.label})",
        "",
        f"{n} turns (English, Hindi, Hinglish), label-model thinking: {thinking}. "
        f"**{'PASS' if ok else 'FAIL'}**",
        "",
        "| Measure | Result | Bar |",
        "|---|---|---|",
        f"| Overall accuracy | {summary['overall']:.1%} | ≥ 90% |",
        *[f"| {k} accuracy | {v:.1%} | ≥ 85% |" for k, v in summary["per_language"].items()],
        f"| business_question recall | {summary['business_recall']:.1%} | ≥ 97% |",
        f"| Reply language | {summary['language']:.1%} | ≥ 95% |",
        f"| Label fallbacks (of {summary['model_rows']} model-decided turns) | "
        f"{summary['fallback_rate']:.1%} | ≤ {MAX_FALLBACK_RATE:.0%} |",
        "",
        "Most frequent confusions:",
        "",
        *[f"- {k}: {v}" for k, v in confusions],
    ]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md — {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
