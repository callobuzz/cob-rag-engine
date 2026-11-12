"""Gate-1 threshold benchmark (conversation layer C1): how high can the search-confidence
threshold be before it blocks real answers?

    python benchmarks/gate.py --label tei-bge-base [--write-defaults]

Ingests benchmarks/documents/files/ like documents.py, asks its 55 answerable questions and
the 60 of documents/unanswerable.jsonl through POST /api/v1/query/retrieve, and records the
best dense cosine of the returned chunks (`score_breakdown.dense_score`). The threshold is
the highest value that still keeps >= 97 % of the answerable questions whose answer was
retrieved (hit@5); the report says how many unanswerable ones it blocks. --write-defaults
stores it in src/callobuzz_rag/conversation/data/gate_defaults.json under the engine's
embedding model. Run it once per embedding model (the TEI default, and BGE-M3 with
benchmarks/compose.local-embeddings.yml). LLM-free.

Provenance: the corpus is retrieved with the benchmark's base settings plus hybrid search
(HyDE, classification and multi-query off) and the engine's smart-ingestion defaults
(boilerplate strip, passage collapse). A knowledge base that turns HyDE or query variants on
takes the best score over more search legs, so the gate is more permissive there.
"""

import argparse
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from cob_rag_client import CobRag  # noqa: E402
from documents import CONFIGS, FILES, QUESTIONS, K, answered_at  # noqa: E402
from run import BASE_RETRIEVAL, admin_key, bench_tenant  # noqa: E402

UNANSWERABLE = HERE / "documents" / "unanswerable.jsonl"
DEFAULTS = HERE.parent / "src" / "callobuzz_rag" / "conversation" / "data" / "gate_defaults.json"
KEEP = 0.97


def choose_threshold(answerable: list[float], keep: float = KEEP) -> float:
    """Highest t such that at least `keep` of `answerable` scores are >= t."""
    ordered = sorted(answerable, reverse=True)
    index = max(0, math.ceil(keep * len(ordered)) - 1)
    return round(ordered[index], 4)


def best_dense(chunks: list[dict[str, Any]]) -> float | None:
    scores = [(c.get("score_breakdown") or {}).get("dense_score") for c in chunks]
    present = [s for s in scores if s is not None]
    return max(present) if present else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local")
    parser.add_argument("--write-defaults", action="store_true")
    args = parser.parse_args()

    admin = CobRag(args.api, api_key=admin_key(), timeout=900)
    corpus = admin.create_corpus(
        f"gate-{int(time.time())}",
        tenant_id=bench_tenant(admin),
        retrieval_config={**BASE_RETRIEVAL, **CONFIGS["hybrid"]},
    )
    rag = CobRag(args.api, api_key=corpus["api_key"], timeout=900)
    ingest: dict[str, str] = {}
    try:
        for path in sorted(FILES.iterdir()):
            # The scanned notice has no extractable text: its job is skipped, not completed.
            job = rag.ingest(path, mode="new", wait=True, poll_interval=0.5, wait_timeout=900)
            ingest[path.name] = str(job.get("status"))
        model = (admin.get_corpus(corpus["id"]).get("applied_embedding") or {}).get(
            "model", "unknown"
        )
        answerable_rows = [json.loads(x) for x in QUESTIONS.read_text("utf-8").splitlines() if x]
        other_rows = [json.loads(x) for x in UNANSWERABLE.read_text("utf-8").splitlines() if x]
        hits: list[float] = []
        misses = 0
        for item in answerable_rows:
            chunks = rag.retrieve(item["q"], options={"final_k": K, "top_k": 20})["chunks"]
            score = best_dense(chunks)
            if answered_at(chunks, item) is not None and score is not None:
                hits.append(score)
            else:
                misses += 1
        blocked_scores: dict[str, list[float]] = {"absent": [], "off_topic": []}
        for item in other_rows:
            chunks = rag.retrieve(item["q"], options={"final_k": K, "top_k": 20})["chunks"]
            blocked_scores[item["kind"]].append(best_dense(chunks) or 0.0)
    finally:
        admin.delete_corpus(corpus["id"], corpus["name"])

    threshold = choose_threshold(hits)
    kept = sum(s >= threshold for s in hits) / len(hits)
    blocked = {
        k: round(sum(s < threshold for s in v) / len(v), 3) for k, v in blocked_scores.items()
    }
    all_other = [s for v in blocked_scores.values() for s in v]
    blocked_all = round(sum(s < threshold for s in all_other) / len(all_other), 3)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    entry = {
        "threshold": threshold,
        "answerable_kept": round(kept, 3),
        "unanswerable_blocked": blocked_all,
        "n": len(hits) + len(all_other),
        "measured_on": stamp,
        "settings": "benchmark base retrieval + hybrid; smart ingestion on",
    }
    out = HERE / "results" / f"gate-{stamp}-{args.label}"
    out.with_suffix(".json").write_text(
        json.dumps(
            {
                "date": stamp,
                "label": args.label,
                "model": model,
                **entry,
                "blocked_by_kind": blocked,
                "answerable_hits": len(hits),
                "answerable_misses": misses,
                "ingest": ingest,
                "answerable_scores": sorted(hits),
                "unanswerable_scores": blocked_scores,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    out.with_suffix(".md").write_text(
        "\n".join(
            [
                f"# Search-confidence gate — {stamp} ({args.label})",
                "",
                f"Embedding model `{model}`. {len(hits)} answerable questions retrieved at "
                f"hit@{K} ({misses} not retrieved, excluded); {len(all_other)} unanswerable.",
                "",
                "| Threshold | Answerable kept | Absent blocked | Off-topic blocked "
                "| All unanswerable blocked |",
                "|---|---|---|---|---|",
                f"| {threshold} | {kept:.1%} | {blocked['absent']:.1%} "
                f"| {blocked['off_topic']:.1%} | {blocked_all:.1%} |",
            ]
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if args.write_defaults:
        raw = json.loads(DEFAULTS.read_text("utf-8"))
        raw.setdefault("models", {})[model] = entry
        DEFAULTS.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md (threshold {threshold} for {model})")


if __name__ == "__main__":
    main()
