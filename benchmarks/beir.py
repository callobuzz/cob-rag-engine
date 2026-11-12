"""BEIR retrieval benchmark for cob-rag-engine — standard public datasets, standard metrics.

    python benchmarks/beir.py --datasets scifact,nfcorpus --label cpu

Needs a running stack and RAG_ADMIN_API_KEY (from .env or the environment). No LLM key:
every configuration measures retrieval only, through the public HTTP API.

For each dataset it downloads the official BEIR zip (cached under benchmarks/.cache/),
imports the corpus through POST /api/v1/ingest/records, then asks every test query
through POST /api/v1/query/retrieve under each configuration and scores the ranking
against the official relevance judgements (qrels):

  nDCG@10     graded, linear gain, log2 discount — the pytrec_eval / BEIR definition
  Recall@100  share of relevant documents in the top 100 (also capped at 100 relevant,
              which is what BEIR reports for TREC-COVID)
  MRR@10      reciprocal rank of the first relevant document

Chunks are mapped back to BEIR document ids through `external_id` and de-duplicated
keeping the best rank. As in BEIR, a result whose id equals the query id is ignored.

Ingested corpora are kept (benchmarks/.cache/state.json) so later runs only query;
pass --fresh to rebuild them. Results land in benchmarks/results/.
"""

import argparse
import contextlib
import csv
import io
import json
import math
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from cob_rag_client import CobRag, CobRagError  # noqa: E402
from run import BASE_RETRIEVAL, admin_key, bench_tenant, percentile  # noqa: E402

CACHE = HERE / ".cache"
BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{}.zip"
DATASETS = ["scifact", "nfcorpus", "arguana", "scidocs", "fiqa", "trec-covid"]

# Each configuration names every ranking stage it uses, so a changed engine default
# cannot silently change what a configuration measures.
_OFF = {
    "hybrid_search_enabled": False,
    "reranking_enabled": False,
    "mmr_enabled": False,
    "pipeline_order_enabled": False,
    "fusion_strategy": "convex",
    "hybrid_dense_weight": 0.75,
}
_HYBRID = {**_OFF, "hybrid_search_enabled": True}
CONFIGS: dict[str, dict[str, object]] = {
    "dense": {**_OFF},
    "hybrid": _HYBRID,
    # The fusion the engine shipped with, for comparison (benchmarks/fusion_study.py).
    "hybrid(rrf)": {**_HYBRID, "fusion_strategy": "rrf", "rrf_k": 60},
    "hybrid+mmr": {**_HYBRID, "mmr_enabled": True},
    # The cross-encoder's order. Each run also scores "hybrid+rerank(fusion-order)" —
    # the same reranked chunks re-sorted by their fusion score, which is exactly what
    # the engine delivers with pipeline_order_enabled off (the setting's own A/B).
    "hybrid+rerank": {**_HYBRID, "reranking_enabled": True, "pipeline_order_enabled": True},
}
FUSION_ORDER = "hybrid+rerank(fusion-order)"
DEPTH = 100
# Chunks returned per query. Depth for recall; lower (--final-k 10) to measure stages
# that choose WHICH chunks survive, such as MMR.
FINAL_K = DEPTH
BATCH = 1000


# --- data ---------------------------------------------------------------------


def _hf_files(repo: str, folder: str) -> list[str]:
    url = f"https://huggingface.co/api/datasets/{repo}/tree/main/{folder}"
    with urllib.request.urlopen(url, timeout=60) as res:
        return [f["path"] for f in json.load(res) if f["path"].endswith(".parquet")]


def _from_huggingface(name: str, target: Path) -> None:
    """The BEIR authors' own copies under huggingface.co/BeIR — the same corpus, queries
    and qrels as the zip, as parquet. Much faster than the university mirror."""
    import pyarrow.parquet as pq

    target.mkdir(parents=True, exist_ok=True)
    for kind in ("corpus", "queries"):
        with (target / f"{kind}.jsonl").open("w", encoding="utf-8", newline="\n") as out:
            for path in _hf_files(f"BeIR/{name}", kind):
                url = f"https://huggingface.co/datasets/BeIR/{name}/resolve/main/{path}"
                with urllib.request.urlopen(url, timeout=900) as res:
                    table = pq.read_table(io.BytesIO(res.read()))
                for row in table.to_pylist():
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
    (target / "qrels").mkdir(exist_ok=True)
    for split in ("test", "dev", "train"):
        url = f"https://huggingface.co/datasets/BeIR/{name}-qrels/resolve/main/{split}.tsv"
        try:
            with urllib.request.urlopen(url, timeout=300) as res:
                (target / "qrels" / f"{split}.tsv").write_bytes(res.read())
        except urllib.error.HTTPError:
            continue  # not every dataset has every split


def dataset_dir(name: str) -> Path:
    target = CACHE / "beir" / name
    if (target / "corpus.jsonl").exists() and (target / "qrels" / "test.tsv").exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        print(f"  downloading {name} from huggingface.co/BeIR", flush=True)
        _from_huggingface(name, target)
        return target
    except Exception as e:  # fall back to the original zip
        print(f"  huggingface failed ({e}); trying the BEIR zip", flush=True)
    url = BEIR_URL.format(name)
    print(f"  downloading {url}", flush=True)
    with urllib.request.urlopen(url, timeout=3600) as res:
        payload = res.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        zf.extractall(target.parent)
    return target


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            qrels.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    return qrels


def records(corpus_path: Path) -> Iterator[dict[str, Any]]:
    for doc in read_jsonl(corpus_path):
        title = (doc.get("title") or "").strip()
        text = (doc.get("text") or "").strip() or title
        if not text:
            continue  # nothing to index; such a document cannot be retrieved by anyone
        record: dict[str, Any] = {"id": str(doc["_id"]), "text": text}
        if title:
            record["title"] = title[:1000]
        yield record


# --- metrics ------------------------------------------------------------------


def ndcg_at(ranking: list[str], rels: dict[str, int], k: int) -> float:
    dcg = sum(
        rels.get(doc, 0) / math.log2(i + 2) for i, doc in enumerate(ranking[:k]) if rels.get(doc)
    )
    ideal = sorted((g for g in rels.values() if g > 0), reverse=True)[:k]
    idcg = sum(g / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def recall_at(ranking: list[str], rels: dict[str, int], k: int, capped: bool = False) -> float:
    relevant = {d for d, g in rels.items() if g > 0}
    if not relevant:
        return 0.0
    found = len(relevant.intersection(ranking[:k]))
    return found / (min(len(relevant), k) if capped else len(relevant))


def mrr_at(ranking: list[str], rels: dict[str, int], k: int) -> float:
    for i, doc in enumerate(ranking[:k]):
        if rels.get(doc, 0) > 0:
            return 1.0 / (i + 1)
    return 0.0


# --- engine -------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    path = CACHE / "state.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def save_state(state: dict[str, Any]) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def ensure_corpus(admin: CobRag, name: str, fresh: bool) -> dict[str, str]:
    state = load_state()
    entry = state.get(name)
    if entry and not fresh:
        try:
            admin.get_corpus(entry["id"])
            return entry  # type: ignore[no-any-return]
        except CobRagError:
            pass
    if entry and fresh:
        with contextlib.suppress(CobRagError):
            admin.delete_corpus(entry["id"], f"beir-{name}")
    corpus = admin.create_corpus(
        f"beir-{name}", tenant_id=bench_tenant(admin), retrieval_config=dict(BASE_RETRIEVAL)
    )
    entry = {"id": corpus["id"], "api_key": corpus["api_key"]}
    state[name] = entry
    save_state(state)
    return entry


def ingest(rag: CobRag, corpus_path: Path, inflight: int) -> dict[str, Any]:
    """Import in batches of 1000, `inflight` jobs at a time. Re-running is cheap: the
    engine skips records whose content is unchanged."""
    batch: list[dict[str, Any]] = []
    pending: list[str] = []
    totals = {"records": 0, "created": 0, "updated": 0, "unchanged": 0, "chunks": 0}
    t0 = time.perf_counter()

    def settle(job_id: str) -> None:
        job = rag.wait_for_job(job_id, poll_interval=1.0, timeout=3600)
        if job.get("status") != "completed":
            raise RuntimeError(f"import job {job_id} ended {job.get('status')}: {job}")
        for key, value in (job.get("records") or {}).items():
            totals[key] = totals.get(key, 0) + value

    def submit(chunk: list[dict[str, Any]]) -> None:
        while len(pending) >= inflight:
            settle(pending.pop(0))
        pending.append(rag.ingest_records(chunk)["job_id"])
        totals["records"] += len(chunk)
        rate = totals["records"] / (time.perf_counter() - t0)
        print(f"\r  queued {totals['records']:,} records ({rate:,.0f}/s)", end="", flush=True)

    for record in records(corpus_path):
        batch.append(record)
        if len(batch) == BATCH:
            submit(batch)
            batch = []
    if batch:
        submit(batch)
    while pending:
        settle(pending.pop(0))
    seconds = time.perf_counter() - t0
    print(f"\r  imported {totals['records']:,} records in {seconds:,.0f}s" + " " * 20, flush=True)
    return {**totals, "seconds": round(seconds, 1)}


def _docs(chunks: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    ranking: list[str] = []
    for chunk in chunks:
        doc = chunk.get("external_id")
        if doc and doc not in seen:
            seen.add(doc)
            ranking.append(doc)
    return ranking


def rank(rag: CobRag, query: str) -> tuple[list[str], list[str], float]:
    """(ranking as returned, the same chunks re-sorted by fusion score, milliseconds)."""
    t = time.perf_counter()
    result = rag.retrieve(query, options={"final_k": FINAL_K, "top_k": DEPTH})
    elapsed = (time.perf_counter() - t) * 1000
    chunks = result.get("chunks", [])
    by_fusion = sorted(
        chunks, key=lambda c: -((c.get("score_breakdown") or {}).get("fused_score") or 0.0)
    )
    return _docs(chunks), _docs(by_fusion), elapsed


def _metrics(
    results: list[tuple[str, list[str]]], qrels: dict[str, dict[str, int]]
) -> dict[str, Any]:
    n = len(results)
    return {
        "queries": n,
        "ndcg@10": round(sum(ndcg_at(r, qrels[q], 10) for q, r in results) / n, 4),
        "recall@100": round(sum(recall_at(r, qrels[q], 100) for q, r in results) / n, 4),
        "recall_cap@100": round(
            sum(recall_at(r, qrels[q], 100, capped=True) for q, r in results) / n, 4
        ),
        "mrr@10": round(sum(mrr_at(r, qrels[q], 10) for q, r in results) / n, 4),
        "docs_returned_median": statistics.median(len(r) for _, r in results),
    }


def evaluate(
    rag: CobRag,
    queries: dict[str, str],
    qrels: dict[str, dict[str, int]],
    concurrency: int,
    latency_queries: int,
    checkpoint: Path,
    restart: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Metrics for the ranking as returned, and for it re-sorted by fusion score.

    Progress is checkpointed every 50 queries in `checkpoint`, so a long run (the
    reranked ones take an hour across all six datasets) resumes where it stopped.
    """
    ids = [q for q in qrels if q in queries]
    state: dict[str, Any] = (
        json.loads(checkpoint.read_text("utf-8"))
        if checkpoint.exists() and not restart
        else {"latency": None, "rankings": {}}
    )

    def save() -> None:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_text(json.dumps(state), encoding="utf-8")

    rag.retrieve("warm-up", options={"final_k": 5})

    # Latency is measured apart from quality: one request at a time, as a single
    # user would see it, on the first `latency_queries` queries. The quality pass
    # below runs `concurrency` requests at once, which says nothing about latency.
    if state["latency"] is None:
        state["latency"] = [rank(rag, queries[qid])[2] for qid in ids[:latency_queries]]
        save()
    single: list[float] = state["latency"]

    def one(qid: str) -> tuple[str, list[str], list[str]]:
        ranking, by_fusion, _ = rank(rag, queries[qid])
        # BEIR's ignore_identical_ids: a query never retrieves itself (ArguAna).
        return qid, [d for d in ranking if d != qid], [d for d in by_fusion if d != qid]

    todo = [q for q in ids if q not in state["rankings"]]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(todo), 50):
            for qid, ranking, by_fusion in pool.map(one, todo[start : start + 50]):
                state["rankings"][qid] = [ranking, by_fusion]
            save()

    results = [(q, *state["rankings"][q]) for q in ids]
    latency = {
        "latency_ms_p50": round(statistics.median(single)) if single else None,
        "latency_ms_p95": round(percentile(single, 95)) if single else None,
        "latency_queries": len(single),
    }
    returned = _metrics([(q, r) for q, r, _ in results], qrels)
    by_fusion = _metrics([(q, f) for q, _, f in results], qrels)
    checkpoint.unlink()
    return {**returned, **latency}, {**by_fusion, **latency}


def docker_footprint() -> list[dict[str, str]]:
    try:
        out = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    rows = [json.loads(line) for line in out.splitlines() if line.strip()]
    return [
        {"name": r["Name"], "mem": r["MemUsage"], "cpu": r["CPUPerc"]}
        for r in rows
        if r["Name"].startswith("cob-rag")
    ]


# --- report -------------------------------------------------------------------


def _ms(value: Any) -> str:
    return "—" if value is None else f"{value} ms"


def write_report(payload: dict[str, Any], out: Path) -> None:
    out.with_suffix(".json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    lines = [
        f"# BEIR retrieval benchmark — {payload['date']} ({payload['label']})",
        "",
        "Official BEIR test splits, scored against the official qrels. Retrieval only, no LLM.",
        f"Embedding model: `{payload['embedding_model']}`. Depth {DEPTH}.",
        "",
        "Latency: one request at a time, first 100 test queries, depth 100, no answer generation.",
        "",
        "| Dataset | Config | nDCG@10 | Recall@100 | R_cap@100 | MRR@10 | p50 | p95 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for ds in payload["datasets"]:
        for r in ds["results"]:
            lines.append(
                f"| {ds['name']} | {r['config']} | {r['ndcg@10']:.3f} | {r['recall@100']:.3f} | "
                f"{r['recall_cap@100']:.3f} | {r['mrr@10']:.3f} | {_ms(r['latency_ms_p50'])} | "
                f"{_ms(r['latency_ms_p95'])} |"
            )
    lines += [
        "",
        "| Dataset | Documents | Queries | Chunks | Import time |",
        "|---|---|---|---|---|",
    ]
    for ds in payload["datasets"]:
        imp = ds["import"]
        lines.append(
            f"| {ds['name']} | {imp['records']:,} | {ds['queries']:,} | {imp['chunks']:,} | "
            f"{imp['seconds']:,.0f} s |"
        )
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local", help="e.g. cpu, gpu — goes in the file name")
    parser.add_argument("--datasets", default=",".join(DATASETS))
    parser.add_argument("--configs", default=",".join(CONFIGS), help="comma-separated subset")
    parser.add_argument("--concurrency", type=int, default=8, help="queries in flight")
    parser.add_argument(
        "--latency-queries", type=int, default=100, help="queries timed one at a time"
    )
    parser.add_argument("--inflight", type=int, default=4, help="import jobs in flight")
    parser.add_argument("--max-queries", type=int, default=0, help="0 = all test queries")
    parser.add_argument("--fresh", action="store_true", help="re-create the corpora")
    parser.add_argument("--skip-import", action="store_true", help="query existing corpora")
    parser.add_argument(
        "--restart", action="store_true", help="ignore saved progress for these configs"
    )
    parser.add_argument(
        "--final-k", type=int, default=DEPTH, help="chunks returned (100 = full depth)"
    )
    args = parser.parse_args()
    global FINAL_K
    FINAL_K = args.final_k
    suffix = "" if FINAL_K == DEPTH else f"@final_k={FINAL_K}"

    admin = CobRag(args.api, api_key=admin_key(), timeout=900)
    health = admin.health()
    payload: dict[str, Any] = {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "label": args.label,
        "health": health,
        "embedding_model": "",
        "datasets": [],
    }
    out = HERE / "results" / f"beir-{payload['date']}-{args.label}"
    if out.with_suffix(".json").exists():
        # Runs of the same label merge: one dataset (or config) per invocation is
        # how a long suite fits in a working session.
        payload["datasets"] = json.loads(out.with_suffix(".json").read_text("utf-8"))["datasets"]

    for name in args.datasets.split(","):
        print(f"== {name}", flush=True)
        folder = dataset_dir(name)
        entry = ensure_corpus(admin, name, args.fresh)
        rag = CobRag(args.api, api_key=entry["api_key"], timeout=900)
        if not payload["embedding_model"]:
            embedding = admin.get_corpus(entry["id"]).get("embedding_config") or {}
            payload["embedding_model"] = embedding.get("model", "engine default")

        state = load_state()
        if args.skip_import and state.get(name, {}).get("import"):
            imported = state[name]["import"]
        else:
            imported = ingest(rag, folder / "corpus.jsonl", args.inflight)
            previous = state.get(name, {}).get("import")
            if previous and not (imported["created"] or imported["updated"]):
                imported = previous  # a resumed pass; keep the real import's numbers
            else:
                state = load_state()
                state[name]["import"] = imported
                save_state(state)

        queries = {str(q["_id"]): q["text"] for q in read_jsonl(folder / "queries.jsonl")}
        qrels = load_qrels(folder / "qrels" / "test.tsv")
        if args.max_queries:
            qrels = dict(list(qrels.items())[: args.max_queries])
        ds: dict[str, Any] = {
            "name": name,
            "queries": len(qrels),
            "import": imported,
            "footprint": docker_footprint(),
            "results": [],
        }
        for config in [c for c in args.configs.split(",") if c]:  # "" = import only
            admin.update_corpus(entry["id"], retrieval_config={**BASE_RETRIEVAL, **CONFIGS[config]})
            checkpoint = CACHE / "progress" / f"{args.label}-{name}-{config}{suffix}.json"
            scores, by_fusion = evaluate(
                rag,
                queries,
                qrels,
                args.concurrency,
                args.latency_queries,
                checkpoint,
                restart=args.fresh or args.restart,
            )
            print(f"  {config:24} {json.dumps(scores)}", flush=True)
            ds["results"].append({"config": config + suffix, **scores})
            if config == "hybrid+rerank":
                print(f"  {FUSION_ORDER:24} {json.dumps(by_fusion)}", flush=True)
                ds["results"].append({"config": FUSION_ORDER + suffix, **by_fusion})
        previous = next((d for d in payload["datasets"] if d["name"] == name), None)
        if previous is not None:
            # Keep configurations measured earlier and not re-run now.
            ran = {r["config"] for r in ds["results"]}
            ds["results"] = [r for r in previous["results"] if r["config"] not in ran] + ds[
                "results"
            ]
            payload["datasets"].remove(previous)
        payload["datasets"].append(ds)
        payload["datasets"].sort(key=lambda d: DATASETS.index(d["name"]))
        write_report(payload, out)  # after every dataset, so a long run leaves partial results

    print(f"wrote {out}.md and .json")


if __name__ == "__main__":
    main()
