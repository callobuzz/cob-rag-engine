"""Reproducible retrieval benchmark for cob-rag-engine.

    python benchmarks/run.py --api http://127.0.0.1:8000 --label cpu

Needs a running stack and RAG_ADMIN_API_KEY (from .env or the environment).
For every configuration it creates a fresh corpus, ingests the 8-document
Summit Trail knowledge base, asks the 40 labelled questions through
POST /api/v1/query/retrieve and scores the ranking:

  hit@k  the answer-bearing chunk (right file AND containing the fact) is in the top k
  MRR    mean reciprocal rank of that chunk (0 when it is not in the top 5)

It measures retrieval, not generation, so no LLM key is needed and the numbers
do not depend on which model you use. Results land in benchmarks/results/.
"""

import argparse
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))

from cob_rag_client import CobRag, CobRagError  # noqa: E402

CONFIGS: dict[str, dict[str, object]] = {
    "dense": {"hybrid_search_enabled": False, "reranking_enabled": False},
    "hybrid": {"hybrid_search_enabled": True, "reranking_enabled": False},
    "hybrid+rerank": {"hybrid_search_enabled": True, "reranking_enabled": True},
}
# Steps that need an LLM are off, so every configuration measures retrieval alone.
BASE_RETRIEVAL = {
    "query_classification_enabled": False,
    "hyde_enabled": False,
    "multi_query_count": 1,
    "cache_enabled": False,
    "temporal_decay_enabled": False,
}
TOP_K = 5


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def admin_key() -> str:
    key = os.environ.get("RAG_ADMIN_API_KEY", "")
    env_file = HERE.parent / ("." + "env")
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("RAG_ADMIN_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("Set RAG_ADMIN_API_KEY (or run from a checkout with a filled .env)")
    return key


BENCH_TENANT = "benchmarks"


def bench_tenant(admin: CobRag) -> str:
    """The id of the tenant named `benchmarks`, created if missing (max_corpora=100).

    Every corpus belongs to a tenant, so the admin key must name one to create a corpus.
    It is created on the default plan, which is unlimited unless the operator changed it
    (accounts A6); a warning is printed if its plan caps anything a run needs.
    """
    tenant_id = ""
    # The list is paginated: search by name rather than scanning the first page.
    for tenant in admin.list_tenants(search=BENCH_TENANT, status="active", limit=500):
        if tenant.get("name") == BENCH_TENANT:
            tenant_id = str(tenant["id"])
            break
    if not tenant_id:
        tenant_id = str(admin.create_tenant(BENCH_TENANT, max_corpora=100)["id"])
    _warn_if_capped(admin, tenant_id)
    return tenant_id


def _warn_if_capped(admin: CobRag, tenant_id: str) -> None:
    """Runs must never hit a plan limit: questions per month (the admin key is not capped,
    but tenant and corpus keys are) or active documents (which apply to the admin too)."""
    try:
        plan = admin.tenant_plan(tenant_id)
    except CobRagError:  # an engine from before plans (accounts A6)
        return
    limits = plan.get("limits") or {}
    capped = {k: v for k, v in limits.items() if v is not None and k != "max_knowledge_bases"}
    if capped:
        print(
            f"WARNING: the {BENCH_TENANT!r} tenant's plan {plan['plan']['name']!r} limits "
            f"{capped}; results may hit 409/429. Move it to an unlimited plan "
            "(POST /api/v1/admin/tenants/{id}/plan).",
            file=sys.stderr,
            flush=True,
        )


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def run_config(api: str, key: str, name: str, overrides: dict[str, object]) -> dict[str, object]:
    admin = CobRag(api, api_key=key, timeout=600)
    corpus = admin.create_corpus(
        f"benchmark-{name}-{int(time.time())}",
        tenant_id=bench_tenant(admin),
        retrieval_config={**BASE_RETRIEVAL, **overrides},
    )
    rag = CobRag(api, api_key=corpus["api_key"], timeout=600)
    try:
        docs = sorted((HERE / "dataset" / "docs").glob("*.md"))
        t0 = time.perf_counter()
        for doc in docs:
            job = rag.ingest(doc, mode="new", wait=True, poll_interval=0.5, wait_timeout=900)
            if job.get("status") != "completed":
                raise RuntimeError(f"ingest of {doc.name} ended {job.get('status')}: {job}")
        ingest_s = time.perf_counter() - t0

        questions = [
            json.loads(line)
            for line in (HERE / "dataset" / "questions.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        ]
        rag.retrieve("warm-up", options={"final_k": TOP_K})  # load models before timing

        latencies: list[float] = []
        ranks: list[int | None] = []
        misses: list[str] = []
        for item in questions:
            t = time.perf_counter()
            result = rag.retrieve(item["q"], options={"final_k": TOP_K, "top_k": 20})
            latencies.append((time.perf_counter() - t) * 1000)
            rank = None
            for position, chunk in enumerate(result.get("chunks", [])[:TOP_K], start=1):
                if chunk.get("file_name") == item["file"] and norm(item["fact"]) in norm(
                    chunk.get("text", "")
                ):
                    rank = position
                    break
            ranks.append(rank)
            if rank is None:
                misses.append(item["q"])

        n = len(questions)

        def hit(k: int) -> float:
            return sum(1 for r in ranks if r is not None and r <= k) / n

        return {
            "config": name,
            "questions": n,
            "documents": len(docs),
            "hit@1": round(hit(1), 3),
            "hit@3": round(hit(3), 3),
            "hit@5": round(hit(5), 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "latency_ms_p50": round(statistics.median(latencies)),
            "latency_ms_p95": round(percentile(latencies, 95)),
            "ingest_seconds_total": round(ingest_s, 1),
            "ingest_seconds_per_doc": round(ingest_s / len(docs), 1),
            "misses": misses,
        }
    finally:
        admin.delete_corpus(corpus["id"], corpus["name"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local", help="e.g. cpu, gpu — goes in the file name")
    parser.add_argument("--configs", default=",".join(CONFIGS), help="comma-separated subset")
    args = parser.parse_args()

    key = admin_key()
    health = CobRag(args.api, api_key=key).health()
    results = []
    for name in args.configs.split(","):
        print(f"== {name}", flush=True)
        result = run_config(args.api, key, name, CONFIGS[name])
        print(json.dumps({k: v for k, v in result.items() if k != "misses"}), flush=True)
        results.append(result)

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = HERE / "results" / f"{stamp}-{args.label}"
    payload = {"date": stamp, "label": args.label, "health": health, "results": results}
    out.with_suffix(".json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )

    lines = [
        f"# Retrieval benchmark — {stamp} ({args.label})",
        "",
        "8 documents, 40 labelled questions, top 5 chunks. A hit needs the right file AND the",
        "answer fact in the chunk text. No LLM involved.",
        "",
        "| Configuration | hit@1 | hit@3 | hit@5 | MRR | p50 latency | p95 latency | ingest / doc |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['config']} | {r['hit@1']:.0%} | {r['hit@3']:.0%} | {r['hit@5']:.0%} | "
            f"{r['mrr']:.2f} | {r['latency_ms_p50']} ms | {r['latency_ms_p95']} ms | "
            f"{r['ingest_seconds_per_doc']} s |"
        )
    for r in results:
        if r["misses"]:
            lines += ["", f"Missed by {r['config']}:", *[f"- {q}" for q in r["misses"]]]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md and .json")


if __name__ == "__main__":
    main()
