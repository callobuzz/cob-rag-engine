"""Real-document benchmark: PDF (typeset and scanned), DOCX, XLSX, PPTX, HTML, Markdown.

    python benchmarks/documents.py --label gpu

Uploads the files in benchmarks/documents/files/ through POST /api/v1/ingest — the
same path a customer's upload takes: parsing, chunking, embedding — then asks the 55
questions in benchmarks/documents/questions.jsonl through /query/retrieve. A question
is answered at rank k when a chunk from the right file among the top k contains ALL of
its facts (a table question lists the row label and the value, so the right cell has
to have come through the parser in the right row).

Measures what BEIR cannot: whether text, tables, spreadsheets, slides and scanned
pages survive parsing well enough to be found. No LLM involved.
"""

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "clients" / "python"))
sys.path.insert(0, str(HERE))

from cob_rag_client import CobRag  # noqa: E402
from run import BASE_RETRIEVAL, admin_key, bench_tenant, norm  # noqa: E402

FILES = HERE / "documents" / "files"
QUESTIONS = HERE / "documents" / "questions.jsonl"
FURNITURE = HERE / "documents" / "furniture"
K = 5
CONFIGS: dict[str, dict[str, object]] = {
    "dense": {"hybrid_search_enabled": False, "reranking_enabled": False, "mmr_enabled": False},
    "hybrid": {"hybrid_search_enabled": True, "reranking_enabled": False, "mmr_enabled": False},
    "hybrid+rerank": {
        "hybrid_search_enabled": True,
        "reranking_enabled": True,
        "mmr_enabled": False,
    },
}


def answered_at(chunks: list[dict[str, Any]], item: dict[str, Any]) -> int | None:
    """The rank of the first chunk that holds every fact and comes from the right file.

    A passage stored in several documents is returned once, from one of them, with the
    others in `also_in`: it counts when the right file is the one returned or one of those.
    A question may also name other files that hold the same passage (`also_in`).
    """
    facts = [norm(f) for f in item["facts"]]
    wanted = {item["file"], *item.get("also_in", [])}
    for rank, chunk in enumerate(chunks[:K], start=1):
        # What the answering model sees: the section heading and the chunk.
        text = norm(f"{chunk.get('section') or ''} {chunk.get('text', '')}")
        holders = {
            chunk.get("file_name"),
            *(a.get("file_name") for a in chunk.get("also_in") or []),
        }
        if holders & wanted and all(f in text for f in facts):
            return rank
    return None


def chunk_counts(rag: CobRag) -> dict[str, int]:
    """Chunks stored per file name (the active version of each document)."""
    counts: dict[str, int] = {}
    offset = 0
    while True:
        # The list endpoint caps a page at 100 documents.
        page = rag.list_documents(limit=100, offset=offset)
        for doc in page["items"]:
            detail = rag.get_document(doc["id"], chunk_limit=1)
            counts[doc["file_name"]] = detail["chunk_total"]
        offset += len(page["items"])
        if not page["items"] or offset >= page["count"]:
            return counts


def scrape_pages() -> list[tuple[str, bytes]]:
    """Ten pages of one shop site as the scraper delivers them: markdown, each with the
    site menu, call to action, disclaimer and newsletter line around its own text."""
    site = json.loads((FURNITURE / "scrape-site.json").read_text("utf-8"))
    shared = site["shared"]
    pages = []
    for n, page in enumerate(site["pages"], start=1):
        parts = [f"# {page['title']}", shared["menu"], *page["body"]]
        parts += [shared["cta"], shared["disclaimer"], shared["newsletter"]]
        pages.append((f"scrape-page-{n:02d}.md", ("\n\n".join(parts) + "\n").encode("utf-8")))
    return pages


def furniture_arm(
    admin: CobRag, api: str, name: str, config: dict[str, object], questions: list[dict[str, Any]]
) -> dict[str, Any]:
    """One knowledge base: three HTML pages sharing furniture and a price table, then ten
    scraped pages sent in auto mode, then the price-table and page questions."""
    corpus = admin.create_corpus(
        f"furniture-{name}-{int(time.time())}",
        tenant_id=bench_tenant(admin),
        retrieval_config={**BASE_RETRIEVAL, **CONFIGS["hybrid"], **config},
    )
    rag = CobRag(api, api_key=corpus["api_key"], timeout=900)
    try:
        statuses: dict[str, str] = {}
        for path in sorted(FURNITURE.glob("*.html")):
            job = rag.ingest(path, mode="new", wait=True, poll_interval=0.5, wait_timeout=900)
            statuses[path.name] = str(job.get("status"))
        # Auto mode without auto_confirm: the way the scraper sends a page.
        for filename, body in scrape_pages():
            job = rag.ingest(
                body, filename=filename, mode="auto", wait=True, poll_interval=0.5, wait_timeout=900
            )
            statuses[filename] = str(job.get("status"))
        chunks = chunk_counts(rag)
        rag.retrieve("warm-up", options={"final_k": K})
        ranks = []
        latencies = []
        for item in questions:
            t = time.perf_counter()
            found = rag.retrieve(item["q"], options={"final_k": K, "top_k": 20})["chunks"]
            latencies.append((time.perf_counter() - t) * 1000)
            ranks.append(answered_at(found, item))
        n = len(ranks)
        return {
            "arm": name,
            "config": config,
            "pending_review": sum(1 for s in statuses.values() if s == "pending_review"),
            "statuses": statuses,
            "chunks": chunks,
            "total_chunks": sum(chunks.values()),
            "questions": n,
            "hit@1": round(sum(1 for r in ranks if r == 1) / n, 3),
            "hit@5": round(sum(1 for r in ranks if r) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
            "latency_ms_p50": round(statistics.median(latencies)),
        }
    finally:
        admin.delete_corpus(corpus["id"], corpus["name"])


def furniture_run(admin: CobRag, api: str, label: str) -> None:
    """Page furniture, measured in one run: the same pages into a knowledge base with the
    smart-ingestion defaults (`after`) and into one with page-furniture stripping and the
    duplicate collapse switched off, which is how pages were handled before (`before`)."""
    lines_in = (FURNITURE / "questions.jsonl").read_text("utf-8").splitlines()
    questions = [json.loads(line) for line in lines_in if line]
    off = {"boilerplate_strip": False, "collapse_duplicates": False}
    arms = [
        furniture_arm(admin, api, "before", off, questions),
        furniture_arm(admin, api, "after", {}, questions),
    ]
    for a in arms:
        print(
            f"  furniture {a['arm']:7} chunks {a['total_chunks']:4}  "
            f"pending_review {a['pending_review']}  hit@1 {a['hit@1']:.0%}  "
            f"hit@5 {a['hit@5']:.0%}  MRR {a['mrr']:.2f}",
            flush=True,
        )
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = HERE / "results" / f"documents-{stamp}-{label}-furniture"
    out.with_suffix(".json").write_text(
        json.dumps({"date": stamp, "label": label, "arms": arms}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    before, after = arms
    lines = [
        f"# Page furniture — {stamp} ({label})",
        "",
        "Three HTML pages that share a menu, cookie notice, footer, disclaimer and a repair "
        "price table, then a ten-page scrape of one site sent in auto mode (the way the "
        "scraper sends it). `before` is a knowledge base with `boilerplate_strip` and "
        "`collapse_duplicates` off; `after` has the defaults.",
        "",
        "| | before | after |",
        "|---|---|---|",
        f"| Chunks stored | {before['total_chunks']} | {after['total_chunks']} |",
        f"| Pages waiting for review (of {len(before['statuses'])}) "
        f"| {before['pending_review']} | {after['pending_review']} |",
        f"| hit@1 | {before['hit@1']:.0%} | {after['hit@1']:.0%} |",
        f"| hit@5 | {before['hit@5']:.0%} | {after['hit@5']:.0%} |",
        f"| MRR | {before['mrr']:.2f} | {after['mrr']:.2f} |",
        f"| p50 latency | {before['latency_ms_p50']} ms | {after['latency_ms_p50']} ms |",
        "",
        "| File | Chunks before | Chunks after |",
        "|---|---|---|",
        *[
            f"| {f} | {before['chunks'].get(f, '-')} | {after['chunks'].get(f, '-')} |"
            for f in before["chunks"]
        ],
    ]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--label", default="local")
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument(
        "--furniture",
        action="store_true",
        help="also measure page furniture: shared-site pages and a 10-page scrape, with and "
        "without stripping (needs the stack; the default run is unchanged)",
    )
    args = parser.parse_args()

    admin = CobRag(args.api, api_key=admin_key(), timeout=900)
    corpus = admin.create_corpus(
        f"documents-{int(time.time())}",
        tenant_id=bench_tenant(admin),
        retrieval_config=BASE_RETRIEVAL,
    )
    rag = CobRag(args.api, api_key=corpus["api_key"], timeout=900)
    try:
        ingest: list[dict[str, Any]] = []
        for path in sorted(FILES.iterdir()):
            t = time.perf_counter()
            job = rag.ingest(path, mode="new", wait=True, poll_interval=0.5, wait_timeout=900)
            seconds = time.perf_counter() - t
            ingest.append(
                {"file": path.name, "status": job.get("status"), "seconds": round(seconds, 1)}
            )
            print(f"  {path.name:30} {job.get('status'):10} {seconds:6.1f} s", flush=True)
        stored = chunk_counts(rag)
        for row in ingest:
            row["chunks"] = stored.get(row["file"])

        questions = [json.loads(line) for line in QUESTIONS.read_text("utf-8").splitlines() if line]
        results = []
        for name in args.configs.split(","):
            admin.update_corpus(corpus["id"], retrieval_config={**BASE_RETRIEVAL, **CONFIGS[name]})
            rag.retrieve("warm-up", options={"final_k": K})
            ranks: list[int | None] = []
            latencies: list[float] = []
            misses = []
            for item in questions:
                t = time.perf_counter()
                chunks = rag.retrieve(item["q"], options={"final_k": K, "top_k": 20})["chunks"]
                latencies.append((time.perf_counter() - t) * 1000)
                rank = answered_at(chunks, item)
                ranks.append(rank)
                if rank is None:
                    top = chunks[0] if chunks else {}
                    misses.append(
                        {
                            "q": item["q"],
                            "kind": item["kind"],
                            "top_file": top.get("file_name"),
                            "top_text": (top.get("text") or "")[:160],
                        }
                    )
            by_kind: dict[str, list[int | None]] = defaultdict(list)
            for item, rank in zip(questions, ranks, strict=True):
                by_kind[item["kind"]].append(rank)
            n = len(ranks)
            result = {
                "config": name,
                "questions": n,
                "hit@1": round(sum(1 for r in ranks if r == 1) / n, 3),
                "hit@3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
                "hit@5": round(sum(1 for r in ranks if r) / n, 3),
                "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
                "latency_ms_p50": round(statistics.median(latencies)),
                "by_kind": {
                    kind: {"n": len(rs), "hit@5": round(sum(1 for r in rs if r) / len(rs), 3)}
                    for kind, rs in by_kind.items()
                },
                "misses": misses,
            }
            print(
                f"  {name:14} hit@1 {result['hit@1']:.0%}  hit@5 {result['hit@5']:.0%}  MRR {result['mrr']:.2f}",
                flush=True,
            )
            results.append(result)
    finally:
        admin.delete_corpus(corpus["id"], corpus["name"])

    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    out = HERE / "results" / f"documents-{stamp}-{args.label}"
    payload = {"date": stamp, "label": args.label, "ingest": ingest, "results": results}
    out.with_suffix(".json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    kinds = list(results[0]["by_kind"]) if results else []
    lines = [
        f"# Real-document benchmark — {stamp} ({args.label})",
        "",
        f"{len(ingest)} files, {results[0]['questions'] if results else 0} questions. A hit needs a chunk from the "
        "right file with every fact of the answer in it. Retrieval only, no LLM.",
        "",
        "| Configuration | hit@1 | hit@3 | hit@5 | MRR | p50 |",
        "|---|---|---|---|---|---|",
        *[
            f"| {r['config']} | {r['hit@1']:.0%} | {r['hit@3']:.0%} | {r['hit@5']:.0%} | {r['mrr']:.2f} | {r['latency_ms_p50']} ms |"
            for r in results
        ],
        "",
        "hit@5 by where the answer lives:",
        "",
        "| Answer in | Questions | " + " | ".join(r["config"] for r in results) + " |",
        "|---|---|" + "---|" * len(results),
        *[
            f"| {kind} | {results[0]['by_kind'][kind]['n']} | "
            + " | ".join(f"{r['by_kind'][kind]['hit@5']:.0%}" for r in results)
            + " |"
            for kind in kinds
        ],
        "",
        "| File | Ingest status | Seconds | Chunks |",
        "|---|---|---|---|",
        *[
            f"| {i['file']} | {i['status']} | {i['seconds']} | {i.get('chunks', '-')} |"
            for i in ingest
        ],
    ]
    for r in results:
        if r["misses"]:
            lines += [
                "",
                f"Missed by {r['config']}:",
                *[f"- ({m['kind']}) {m['q']}" for m in r["misses"]],
            ]
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}.md")
    if args.furniture:
        furniture_run(admin, args.api, args.label)


if __name__ == "__main__":
    main()
