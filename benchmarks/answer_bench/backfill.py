"""Fill what a saved run is missing from OpenRouter's generation records (free, no model calls).

The record for a generation appears late, sometimes minutes after the stream ended, so an answer
saved with `host: null` is normal. `python -m benchmarks.answers hosts --run <name>` (or
`--all-smoke`) looks each such answer up again and fills `host` / `host_source`, the cost when
usage had none, and the native reasoning token count. It is idempotent: complete records are not
looked up and an unchanged file is not rewritten.
"""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from benchmarks.answer_bench.generate import apply_meta, needs_meta, replace_records
from benchmarks.answer_bench.hosts import HostLookup
from benchmarks.answer_bench.records import AnswerRecord, read_jsonl

CONCURRENCY = 4


@dataclass
class BackfillResult:
    updated: int = 0
    unchanged: int = 0  # records that needed nothing
    unresolved: int = 0  # looked up, nothing (or not everything) published yet


async def backfill_run(run_dir: Path, lookup: HostLookup) -> BackfillResult:
    path = run_dir / "answers.jsonl"
    answers = read_jsonl(path, AnswerRecord)
    result = BackfillResult()
    todo = [r for r in answers if needs_meta(r)]
    result.unchanged = len(answers) - len(todo)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(rec: AnswerRecord) -> tuple[AnswerRecord, AnswerRecord | None]:
        async with sem:
            assert rec.generation_id is not None
            meta = await lookup.lookup(rec.generation_id)
        return rec, (None if meta.empty else apply_meta(rec, meta, lookup.source))

    updates: dict[tuple[str, str, str], AnswerRecord] = {}
    for rec, new in await asyncio.gather(*(one(r) for r in todo)):
        if new is not None and new is not rec:
            updates[rec.key] = new
            result.updated += 1
        if new is None or needs_meta(new):
            result.unresolved += 1
    if updates:
        replace_records(path, updates)
    return result
