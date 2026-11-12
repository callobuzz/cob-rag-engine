"""Live progress for a streamed answer: the `stage` event and the timings in `done`.

Event shape follows the conversation layer's contract
(`docs/internal/plans/epics/conversation/README.md`, "reply envelope and SSE contract"):
`stage` is `{"stage": <name>, "elapsed_ms": int}` and `done.timings` is `{stage: ms}`. The
conversation layer adds stages (`check`, `verify`, ...) to the same event rather than replacing
it. Clients that do not know `stage` ignore it, like any unknown event.

Public stages, in the order they run: `understand` -> `search` -> `rerank` (only when a
re-ranker is on) -> `check` (the assistant's confidence check) -> `write` -> `verify` (the
assistant's fact check); `form` and `handoff` are sent when the assistant shows a form or card. The engine's finer pipeline timings (`embed`, `search_dense`,
`fuse`, ...) stay available as `done.stage_timings_ms`.
"""

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from callobuzz_rag.api.streaming import shielded, sse_event

STAGE_UNDERSTAND = "understand"
STAGE_SEARCH = "search"
STAGE_RERANK = "rerank"
STAGE_WRITE = "write"
# The conversation layer's stages (C1). `form` and `handoff` are not part of the ordered run.
STAGE_CHECK = "check"
STAGE_VERIFY = "verify"
STAGE_FORM = "form"
STAGE_HANDOFF = "handoff"
# PIP1 lane: the question is waiting for its turn (the engine is answering as many as it can).
# Sent once, outside the ordered run; the widget turns it into "One moment...".
STAGE_QUEUED = "queued"
# /PIP1 lane

_ORDER = (STAGE_UNDERSTAND, STAGE_SEARCH, STAGE_RERANK, STAGE_CHECK, STAGE_WRITE, STAGE_VERIFY)
_RAW_TO_PUBLIC = {"query_understanding": STAGE_UNDERSTAND, "rerank": STAGE_RERANK}


def public_stage(raw: str) -> str:
    """Map an engine pipeline stage to the stage a client is told about."""
    return _RAW_TO_PUBLIC.get(raw, STAGE_SEARCH)


def public_timings(raw: dict[str, int]) -> dict[str, int]:
    """Sum the engine's per-step timings into the public stages. A stage that did not run
    is absent, not zero ("re-ranking took 0 ms" differs from "re-ranking was off")."""
    out: dict[str, int] = {}
    for key, ms in raw.items():
        name = public_stage(key)
        out[name] = out.get(name, 0) + ms
    return out


class StageClock:
    """Starts on creation; turns stage starts into `stage` events, once per change."""

    def __init__(self) -> None:
        self._start = time.monotonic()
        self._current: str | None = None

    def elapsed_ms(self) -> int:
        return round((time.monotonic() - self._start) * 1000)

    def advance(self, stage: str) -> tuple[str, int] | None:
        """`(stage, elapsed_ms)` for entering `stage`, or None when it is current or behind.

        Progress only moves forward: the tidy-up steps that follow re-ranking are still
        timed as `search`, but the client is not sent back from "reranking" to "searching".
        """
        if stage == self._current:
            return None
        if (
            self._current in _ORDER
            and stage in _ORDER
            and _ORDER.index(stage) < _ORDER.index(self._current)
        ):
            return None
        self._current = stage
        return stage, self.elapsed_ms()

    def enter(self, stage: str) -> str | None:
        """The `stage` SSE event for entering `stage`, or None when it is current or behind."""
        moved = self.advance(stage)
        if moved is None:
            return None
        return sse_event("stage", {"stage": moved[0], "elapsed_ms": moved[1]})


async def events_until_done[T, E](
    task: "asyncio.Future[T]", queue: "asyncio.Queue[E]"
) -> AsyncIterator[E]:
    """Yield queued events as they arrive, until `task` has finished and the queue is drained.
    The events are whatever the producer queued: SSE strings, or the director's typed events.

    The caller reads `task.result()` afterwards (which raises the task's own error).
    """
    while True:
        getter = asyncio.ensure_future(queue.get())
        try:
            waiting: set[asyncio.Future[Any]] = {task, getter}
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if not getter.done():
                getter.cancel()
        if getter.done() and not getter.cancelled():
            yield getter.result()
            continue
        while not queue.empty():
            yield queue.get_nowait()
        if task.done():
            return


def stage_listener(clock: StageClock, queue: "asyncio.Queue[str]") -> Callable[[str], None]:
    """The `on_stage` callback for the retrieval pipeline: queue a `stage` event per change."""

    def listener(raw: str) -> None:
        event = clock.enter(public_stage(raw))
        if event is not None:
            queue.put_nowait(event)

    return listener


async def stop_search(task: "asyncio.Future[Any]") -> None:
    """Cancel a retrieval task that is still running and wait for it to unwind.

    Called when the client leaves mid-search. The task may be mid-query on the request's
    database session, so the session must not be closed until it has finished; the wait is
    shielded because the response itself is being cancelled at that moment.
    """
    if task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await shielded(asyncio.wait({task}))
