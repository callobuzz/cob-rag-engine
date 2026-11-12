"""How should the dense and keyword legs be fused? Tuned on BEIR dev/train, reported on test.

    python benchmarks/fusion_study.py

Needs the corpora `benchmarks/beir.py` imported (benchmarks/.cache/state.json). For
every query it asks the engine for the dense top-100 (`search_mode: dense`) and the
keyword top-100 (`search_mode: keyword`), with each leg's raw score from
`score_breakdown`, and caches both under benchmarks/.cache/runs/. Fusion is then
recomputed offline for every candidate setting — no further engine calls.

Settings are CHOSEN on the tuning qrels only (SciFact train, NFCorpus dev, FiQA dev)
and then REPORTED on every test split, so the published test numbers are not the
result of tuning on them.
"""

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from beir import (  # noqa: E402
    CACHE,
    CONFIGS,
    dataset_dir,
    load_qrels,
    ndcg_at,
    read_jsonl,
    recall_at,
)
from cob_rag_client import CobRag  # noqa: E402
from run import BASE_RETRIEVAL, admin_key  # noqa: E402

TUNE = {"scifact": "train", "nfcorpus": "dev", "fiqa": "dev"}
TEST = ["scifact", "nfcorpus", "arguana", "scidocs", "fiqa", "trec-covid"]
DEPTH = 100
Leg = dict[str, float]  # BEIR doc id -> best raw score in that leg


def leg(rag: CobRag, query: str, mode: str, field: str) -> Leg:
    result = rag.retrieve(query, options={"final_k": DEPTH, "top_k": DEPTH, "search_mode": mode})
    scores: Leg = {}
    for chunk in result.get("chunks", []):
        doc = chunk.get("external_id")
        value = (chunk.get("score_breakdown") or {}).get(field)
        if doc and value is not None and value > scores.get(doc, float("-inf")):
            scores[doc] = value
    return scores


def fetch_runs(api: str, name: str, split: str, concurrency: int) -> dict[str, Any]:
    path = CACHE / "runs" / f"{name}-{split}.json"
    if path.exists():
        return json.loads(path.read_text("utf-8"))  # type: ignore[no-any-return]
    entry = json.loads((CACHE / "state.json").read_text("utf-8"))[name]
    admin = CobRag(api, api_key=admin_key(), timeout=900)
    admin.update_corpus(entry["id"], retrieval_config={**BASE_RETRIEVAL, **CONFIGS["dense"]})
    rag = CobRag(api, api_key=entry["api_key"], timeout=900)
    folder = dataset_dir(name)
    queries = {str(q["_id"]): q["text"] for q in read_jsonl(folder / "queries.jsonl")}
    qrels = load_qrels(folder / "qrels" / f"{split}.tsv")
    ids = [q for q in qrels if q in queries]

    def one(qid: str) -> tuple[str, Leg, Leg]:
        dense = leg(rag, queries[qid], "dense", "dense_score")
        sparse = leg(rag, queries[qid], "keyword", "sparse_score")
        # BEIR's ignore_identical_ids: a query never retrieves itself (ArguAna).
        dense.pop(qid, None)
        sparse.pop(qid, None)
        return qid, dense, sparse

    # Checkpointed every 100 queries, so a long split survives an interrupted run.
    partial = path.with_suffix(".partial.json")
    done: dict[str, Any] = (
        json.loads(partial.read_text("utf-8")) if partial.exists() else {"dense": {}, "sparse": {}}
    )
    todo = [q for q in ids if q not in done["dense"]]
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for start in range(0, len(todo), 100):
            for qid, dense, sparse in pool.map(one, todo[start : start + 100]):
                done["dense"][qid], done["sparse"][qid] = dense, sparse
            partial.parent.mkdir(parents=True, exist_ok=True)
            partial.write_text(json.dumps(done), encoding="utf-8")
            print(f"  {name}-{split}: {len(done['dense'])}/{len(ids)} queries", flush=True)
    runs = {"qrels": qrels, **done}
    path.write_text(json.dumps(runs), encoding="utf-8")
    partial.unlink()
    return runs


# --- fusion functions -----------------------------------------------------------


def _ranks(scores: Leg) -> dict[str, int]:
    return {d: i for i, (d, _) in enumerate(sorted(scores.items(), key=lambda x: -x[1]), 1)}


def rrf(k: int, w: float) -> Callable[[Leg, Leg], Leg]:
    def fuse(dense: Leg, sparse: Leg) -> Leg:
        out: Leg = {}
        for weight, scores in ((w, dense), (1 - w, sparse)):
            for doc, rank in _ranks(scores).items():
                out[doc] = out.get(doc, 0.0) + weight / (k + rank)
        return out

    return fuse


def convex(alpha: float) -> Callable[[Leg, Leg], Leg]:
    """alpha * dense + (1 - alpha) * keyword, each min-max normalized within the query;
    a document missing from a leg gets that leg's 0."""

    def norm(scores: Leg) -> Leg:
        if not scores:
            return {}
        lo, hi = min(scores.values()), max(scores.values())
        return {d: (s - lo) / (hi - lo) if hi > lo else 1.0 for d, s in scores.items()}

    def fuse(dense: Leg, sparse: Leg) -> Leg:
        nd, ns = norm(dense), norm(sparse)
        return {d: alpha * nd.get(d, 0.0) + (1 - alpha) * ns.get(d, 0.0) for d in nd | ns}

    return fuse


def score(runs: dict[str, Any], fuse: Callable[[Leg, Leg], Leg]) -> tuple[float, float]:
    ndcg, recall = [], []
    for qid, rels in runs["qrels"].items():
        if qid not in runs["dense"]:
            continue
        fused = fuse(runs["dense"][qid], runs["sparse"][qid])
        ranking = [d for d, _ in sorted(fused.items(), key=lambda x: -x[1])]
        ndcg.append(ndcg_at(ranking, rels, 10))
        recall.append(recall_at(ranking, rels, 100))
    return statistics.fmean(ndcg), statistics.fmean(recall)


def candidates() -> dict[str, Callable[[Leg, Leg], Leg]]:
    out: dict[str, Callable[[Leg, Leg], Leg]] = {
        "dense only": convex(1.0),
        "keyword only": convex(0.0),
    }
    for k in (10, 30, 60, 100):
        for w in (0.5, 0.6, 0.7, 0.8):
            out[f"rrf k={k} w={w}"] = rrf(k, w)
    for alpha in (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95):
        out[f"convex a={alpha}"] = convex(alpha)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    tune = {n: fetch_runs(args.api, n, s, args.concurrency) for n, s in TUNE.items()}
    test = {n: fetch_runs(args.api, n, "test", args.concurrency) for n in TEST}
    funcs = candidates()

    table = []
    for label, fuse in funcs.items():
        tuned = {n: score(r, fuse)[0] for n, r in tune.items()}
        table.append((statistics.fmean(tuned.values()), label, tuned))
    table.sort(reverse=True)
    best = table[0][1]
    anchors = ["dense only", "keyword only", "rrf k=60 w=0.5", best]

    lines = [
        "# Fusion study — dense + keyword",
        "",
        "Chosen on tuning qrels (SciFact train, NFCorpus dev, FiQA dev); reported on test.",
        "",
        "## Tuning (nDCG@10), top 10 settings",
        "",
        "| Setting | mean | " + " | ".join(tune) + " |",
        "|---|---|" + "---|" * len(tune),
    ]
    for mean, label, tuned in table[:10]:
        lines.append(
            f"| {label} | {mean:.4f} | " + " | ".join(f"{v:.4f}" for v in tuned.values()) + " |"
        )
    lines += [
        "",
        f"## Test — chosen setting: `{best}`",
        "",
        "| Setting | " + " | ".join(TEST) + " | mean |",
        "|---|" + "---|" * (len(TEST) + 1),
    ]
    for label in dict.fromkeys(anchors):
        results = [score(test[n], funcs[label])[0] for n in TEST]
        lines.append(
            f"| {label} | "
            + " | ".join(f"{v:.4f}" for v in results)
            + f" | {statistics.fmean(results):.4f} |"
        )
    out = HERE / "results" / "fusion-study.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
