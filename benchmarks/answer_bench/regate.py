"""Re-run the engine's grounded-answer path over saved raw answers, with no model call.

When the fact gate (or anything else between the model's stream and the visitor) changes, the
saved `answer_released`, `gate_removed` and `route` describe the old engine. `regate` replays
each record's `answer_raw` through `conversation.answering.grounded` itself, from a stand-in
provider that streams the saved text in small deltas and reports the saved finish reason (so a
length stop is handled as the engine would handle it), and rewrites those three fields. Every
other field, including what the generation cost, is kept: nothing is paid for twice.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from benchmarks.answer_bench.items import Item
from benchmarks.answer_bench.prompt import compile_turn, sources, user_message
from benchmarks.answer_bench.records import AnswerRecord, read_jsonl, write_jsonl

DELTA_CHARS = 7  # small deltas, so the gate sees a stream, not one block


class ReplayLLM:
    """Streams one saved answer; the only provider method `grounded` calls."""

    has_credentials = True

    def __init__(self, text: str, finish_reason: str | None) -> None:
        self._text = text
        self._finish = finish_reason

    def is_known_non_reasoning(self, model: str) -> bool:
        return False  # no max_tokens: the text is already written

    async def complete_stream(self, messages: Any, **_: Any) -> AsyncIterator[str]:
        from callobuzz_rag.llm.stream_stats import current_stream_stats

        for i in range(0, len(self._text), DELTA_CHARS):
            yield self._text[i : i + DELTA_CHARS]
        stats = current_stream_stats()
        if stats is not None:
            stats.finish_reason = self._finish


@dataclass(frozen=True)
class Regated:
    released: str
    removed: int
    route: str
    truncated: bool


async def regate_answer(item: Item, rec: AnswerRecord, settings: Any) -> Regated:
    from benchmarks.answer_bench.prompt import assistant_config

    from callobuzz_rag.api.stage_events import StageClock
    from callobuzz_rag.conversation.answering import Acc, AnswerDeps, Retrieved, grounded

    deps = AnswerDeps(
        settings=settings,
        session_factory=_unused,
        orchestrator_factory=_unused,
        retrieval_kwargs={},
        llm=ReplayLLM(rec.answer_raw, rec.finish_reason),  # type: ignore[arg-type]
        model_name=rec.model,
        translate_cache=None,
        payer="operator",
    )
    acc = Acc(query_id=uuid.uuid4())
    retrieved = Retrieved(
        gate=None,
        sources=sources(item),
        user_message=user_message(item, settings),
        queries=[item.question],
        error=None,
        error_code=None,
        stage_timings={},
        timings={},
    )
    async for _ in grounded(
        deps,
        config=assistant_config(item.business),
        compile_turn=compile_turn(item),
        retrieved=retrieved,
        question=item.question,
        history=[],
        acc=acc,
        clock=StageClock(),
    ):
        pass
    route = "no_answer" if acc.route.value == "no_answer" else "grounded"
    return Regated(acc.text(), acc.removed, route, acc.truncated)


def _unused(*_: Any) -> Any:
    raise AssertionError("regate never retrieves")


@dataclass
class RegateResult:
    changed: int
    unchanged: int
    skipped: int  # error records: nothing was generated


def regate_run(run_dir: Path, items: list[Item], settings: Any) -> RegateResult:
    """Rewrite `answer_released`, `gate_removed` and `route` of every completed record."""
    path = run_dir / "answers.jsonl"
    records = read_jsonl(path, AnswerRecord)
    by_id = {i.id: i for i in items}
    out: list[AnswerRecord] = []
    result = RegateResult(0, 0, 0)
    for rec in records:
        item = by_id.get(rec.item_id)
        if rec.error is not None or item is None:
            result.skipped += 1
            out.append(rec)
            continue
        new = asyncio.run(regate_answer(item, rec, settings))
        updated = rec.model_copy(
            update={
                "answer_released": new.released,
                "gate_removed": new.removed,
                "route": new.route,
            }
        )
        if updated != rec:
            result.changed += 1
        else:
            result.unchanged += 1
        out.append(AnswerRecord.model_validate(updated.model_dump()))
    write_jsonl(path, list(out))
    return result
