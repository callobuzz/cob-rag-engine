"""`python -m callobuzz_rag.cob.eval --mode offline|synced|llm`.

offline  no network, no database: the CI gate (exit 1 when a bar fails).
synced   the real retrieval over the synced COB knowledge base; stores the result for the
         Engine screen. Needs the datastores and the embedding service.
llm      real COB turns. Spends money: `--estimate` prints the cost and stops; the run needs
         `--confirm-cost <usd>` at or above the estimate (exit 2 otherwise).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from callobuzz_rag.cob.eval.items import EvalItem, load_eval_items


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m callobuzz_rag.cob.eval", description=__doc__)
    p.add_argument("--mode", choices=["offline", "synced", "llm"], default="offline")
    p.add_argument("--estimate", action="store_true", help="llm: print the cost and stop")
    p.add_argument("--confirm-cost", type=float, default=None, metavar="USD")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--role", choices=["admin", "operator_owner", "owner", "member", "public"])
    p.add_argument("--lang", choices=["en", "hi", "hi-Latn"])
    p.add_argument("--out-dir", type=Path, default=None, help="llm: where the report goes")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    return p


def _items(args: argparse.Namespace) -> list[EvalItem]:
    from callobuzz_rag.cob.eval.llm import select_items

    return select_items(load_eval_items(), role=args.role, lang=args.lang, limit=args.limit)


def run_offline_mode(args: argparse.Namespace) -> int:
    from callobuzz_rag.cob.eval.offline import run_offline
    from callobuzz_rag.cob.kb import load_docs_index

    report = run_offline(load_docs_index(), _items(args))
    if args.json:
        print(json.dumps(report.summary()))
    else:
        s = report.summary()
        print(
            f"COB eval (offline): {s['items']} items, hit@5 {s['hit_at_5']:.2%} "
            f"(Hindi/Hinglish {s['hit_at_5_indic']:.2%}), role leaks {s['role_leaks']}, "
            f"negatives failed {s['negatives_failed']}, routing failed {s['routing_failed']}: "
            f"{'passed' if s['passed'] else 'FAILED'}"
        )
        for name, rows in (
            ("role leak", report.role_leaks),
            ("negative", report.negatives_failed),
            ("routing", report.routing_failed),
        ):
            for row in rows:
                print(f"  {name}: {row[0]}  {row[1]}")
        for item_id in report.misses:
            print(f"  miss: {item_id}")
    return 0 if report.passed else 1


async def _synced(args: argparse.Namespace) -> int:
    from callobuzz_rag.cob.eval.synced import evaluate_after_sync, system_search
    from callobuzz_rag.cob.kb import load_docs_index
    from callobuzz_rag.core.config import get_settings
    from callobuzz_rag.db.engine import create_engine, create_session_factory
    from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider

    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    vector_store = QdrantVectorProvider(settings)
    try:
        summary = await evaluate_after_sync(
            factory,
            load_docs_index(),
            lambda session, corpus: system_search(session, settings, corpus, vector_store),
            items=_items(args),
        )
    finally:
        await vector_store.close()
        await engine.dispose()
    print(json.dumps(summary) if args.json else f"COB eval (synced): {summary}")
    return 0 if summary.get("passed") else 1


def run_llm_mode(args: argparse.Namespace) -> int:
    from callobuzz_rag.cob.eval import llm, report
    from callobuzz_rag.cob.kb import load_docs_index
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    model, provider = str(settings.llm_model), str(settings.llm_provider)
    items = _items(args)
    cost = llm.estimate(items, model=model, provider=provider)
    paid = len(llm.paid_items(items))
    print(
        f"COB eval (llm): {len(items)} items, {paid} may call {model}; "
        f"estimated cost ${cost.total_usd:.2f} (answer-model calls only; router and translation calls on the fast model are not counted)"
    )
    if cost.unpriced_models:
        print(f"  no price known for: {', '.join(cost.unpriced_models)}")
    if args.estimate:
        return 0
    blocked = llm.cost_rail(cost.total_usd, args.confirm_cost)
    if blocked is not None:
        print(
            f"Not run: pass --confirm-cost {max(cost.total_usd, 0.01):.2f} (or more) to spend it.",
            file=sys.stderr,
        )
        return blocked

    async def go() -> list[llm.ItemResult]:
        runner = await llm.InProcessRunner.create()
        try:
            return await llm.run_llm_eval(runner, items)
        finally:
            await runner.aclose()

    results = asyncio.run(go())
    built = report.build_report(
        results,
        model=model,
        estimated_usd=cost.total_usd,
        confirmed_usd=float(args.confirm_cost),
        version=load_docs_index().version,
        today=datetime.now(UTC).date(),
    )
    out_dir = args.out_dir or Path("benchmarks") / "results"
    json_path, md_path = report.write_report(built, out_dir)
    overall: dict[str, Any] = built["overall"]
    print(
        f"Pass rate {overall['pass_rate']} over {built['items']} items. Wrote {json_path} and {md_path}"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "offline":
        return run_offline_mode(args)
    if args.mode == "synced":
        return asyncio.run(_synced(args))
    return run_llm_mode(args)


if __name__ == "__main__":
    sys.exit(main())
