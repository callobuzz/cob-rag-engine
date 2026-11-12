"""Time to first token of an assistant turn, in-process, with and without the speculative
answer (RAG_ASSISTANT_SPECULATIVE_ANSWER). No stack, no model calls, no cost.

    .venv/Scripts/python -m benchmarks.ttft --label-ms 1520 --retrieval-ms 80 --answer-ms 800

What a visitor waits for: the time from the message to the first word of the reply. The real
director runs four kinds of turn against stand-ins with fixed latencies: the fast model's label
call (`--label-ms`), the search (`--retrieval-ms`) and the answer model's first token
(`--answer-ms`). The defaults are measured figures: the label p50 of the current fast model
(gpt-4.1-mini, 1.52 s, benchmarks/README.md "Fast models"), the CPU retrieval p50 (77 ms,
"Security and operations") and 0.8 s for the answer model's first token. Change them to
model another setup; every number scales with them.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tests.unit.conversation_helpers import (  # noqa: E402
    FakeLLM,
    fake_orch,
    make_director,
    make_turn,
)

TURNS = {
    "smalltalk (rules)": ("hello", None),
    "business question": ("how much does home collection cost in Kothrud?", "business_question"),
    "off-topic": ("who won the cricket world cup in 2011?", "off_topic"),
    "human request": ("is there a real person I can talk to about my sample?", "human_request"),
    "human request (rules)": ("can someone from your team call me back?", None),
}


class Stand(FakeLLM):
    def __init__(self, label: str | None, label_s: float, answer_s: float) -> None:
        reply = {
            "label": label,
            "confidence": 0.95,
            "language": "en",
            "topic": None,
            "frustrated": False,
            "needs_human": False,
        }
        super().__init__(
            json_replies=[reply] if label else [],
            stream=["Home collection costs Rs 150 ", "within Pune city."],
        )
        self.label_s = label_s
        self.answer_s = answer_s

    async def complete_json(self, messages: Any, **kw: Any) -> Any:
        await asyncio.sleep(self.label_s)
        return await super().complete_json(messages, **kw)

    async def complete_stream(self, messages: Any, **kw: Any) -> AsyncIterator[str]:
        await asyncio.sleep(self.answer_s)
        async for piece in super().complete_stream(messages, **kw):
            yield piece


async def one(text: str, label: str | None, args: argparse.Namespace, speculate: bool) -> float:
    from callobuzz_rag.conversation.envelope import TokenEvent
    from callobuzz_rag.core.config import Settings

    orch = fake_orch()
    found = orch.run_retrieval_pipeline.return_value

    async def search(**_: Any) -> Any:
        await asyncio.sleep(args.retrieval_ms / 1000)
        return found

    orch.run_retrieval_pipeline = AsyncMock(side_effect=search)
    llm = Stand(label, args.label_ms / 1000, args.answer_ms / 1000)
    director = make_director(llm, orch=orch)
    settings = Settings(_env_file=None, assistant_speculative_answer=speculate)
    director._deps.settings = settings
    director._labeler._timeout_s = settings.assistant_label_timeout_ms / 1000  # the engine's
    turn = make_turn(text)
    turn.persist = False
    started = time.perf_counter()
    async for event in director.run_stream(turn):
        if isinstance(event, TokenEvent):
            return (time.perf_counter() - started) * 1000
    raise RuntimeError(f"no token for {text!r}")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    rows = []
    for name, (text, label) in TURNS.items():
        before = [await one(text, label, args, False) for _ in range(args.runs)]
        after = [await one(text, label, args, True) for _ in range(args.runs)]
        rows.append(
            {
                "turn": name,
                "before_ms": round(statistics.median(before)),
                "after_ms": round(statistics.median(after)),
            }
        )
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "label_ms": args.label_ms,
        "retrieval_ms": args.retrieval_ms,
        "answer_first_token_ms": args.answer_ms,
        "runs": args.runs,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--label-ms", type=int, default=1520)
    parser.add_argument("--retrieval-ms", type=int, default=77)
    parser.add_argument("--answer-ms", type=int, default=800)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--out", default="", help="results/<name> to write .json and .md")
    args = parser.parse_args()
    out = asyncio.run(run(args))
    lines = [
        f"# Time to first token — {out['date']}",
        "",
        f"In-process (`benchmarks/ttft.py`): label call {args.label_ms} ms, search "
        f"{args.retrieval_ms} ms, answer model's first token {args.answer_ms} ms; median of "
        f"{args.runs} runs. Before = RAG_ASSISTANT_SPECULATIVE_ANSWER=false.",
        "",
        "| Turn | Before | After | Saved |",
        "|---|---|---|---|",
        *[
            f"| {r['turn']} | {r['before_ms']} ms | {r['after_ms']} ms | "
            f"{r['before_ms'] - r['after_ms']} ms |"
            for r in out["rows"]
        ],
    ]
    print("\n".join(lines))
    if args.out:
        base = HERE / "results" / args.out
        base.with_suffix(".json").write_text(
            json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
