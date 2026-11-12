"""Thinking time and reasoning tokens of a streamed answer, and the stage helpers."""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from callobuzz_rag.api.stage_events import (
    StageClock,
    events_until_done,
    public_stage,
    public_timings,
    stage_listener,
)
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.llm.stream_stats import collect_stream_stats, current_stream_stats


def _chunk(content=None, reasoning=None, usage=None, choices=True):
    delta = SimpleNamespace(content=content, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)] if choices else [], usage=usage)


def _provider() -> LiteLLMProvider:
    settings = MagicMock()
    settings.llm_model = "m"
    settings.llm_api_key.get_secret_value.return_value = "k"
    settings.llm_api_base = None
    settings.llm_temperature = 0.1
    settings.llm_max_tokens = 100
    settings.llm_fast_model = ""
    settings.llm_timeout_seconds = 5.0
    settings.llm_provider = None
    settings.llm_reasoning_effort = None
    return LiteLLMProvider(settings)


async def _run(chunks) -> tuple[list[str], object]:
    async def fake(*_a, **_k):
        for c in chunks:
            await asyncio.sleep(0.005)
            yield c

    with patch("litellm.acompletion", side_effect=fake), collect_stream_stats() as stats:
        out = [c async for c in _provider().complete_stream([LLMMessage(role="user", content="q")])]
    return out, stats


async def test_reasoning_deltas_and_usage_are_recorded() -> None:
    usage = SimpleNamespace(completion_tokens_details=SimpleNamespace(reasoning_tokens=77))
    out, stats = await _run(
        [
            _chunk(reasoning="hmm"),
            _chunk(reasoning="so"),
            _chunk(content="Yes"),
            _chunk(content="."),
            _chunk(choices=False, usage=usage),  # a usage-only final chunk has no choices
        ]
    )
    assert out == ["Yes", "."]
    assert stats.reasoning_tokens == 77
    assert stats.first_reasoning_at is not None
    assert stats.thinking_ms is not None and stats.thinking_ms >= 8


async def test_a_plain_model_has_no_thinking_time() -> None:
    out, stats = await _run([_chunk(content="Hi"), _chunk(content="!")])
    assert out == ["Hi", "!"]
    assert stats.first_content_at is not None
    assert stats.thinking_ms is None  # the wait for a first token is not "thinking"
    assert stats.reasoning_tokens == 0


async def test_no_stats_installed_costs_nothing() -> None:
    assert current_stream_stats() is None

    async def fake(*_a, **_k):
        yield _chunk(content="x")

    with patch("litellm.acompletion", side_effect=fake):
        out = [c async for c in _provider().complete_stream([LLMMessage(role="user", content="q")])]
    assert out == ["x"]


def test_public_stage_mapping_and_sums() -> None:
    assert public_stage("query_understanding") == "understand"
    assert public_stage("rerank") == "rerank"
    assert public_stage("embed") == "search"
    assert public_timings({"query_understanding": 5, "embed": 2, "fuse": 3, "assemble": 1}) == {
        "understand": 5,
        "search": 6,
    }  # no rerank key: re-ranking was off, which differs from 0 ms


def test_stage_clock_only_moves_forward_and_never_repeats() -> None:
    clock = StageClock()
    names = []
    for stage in ("understand", "search", "search", "rerank", "search", "write"):
        ev = clock.enter(stage)
        if ev:
            names.append(json.loads(ev.split("data: ")[1])["stage"])
    assert names == ["understand", "search", "rerank", "write"]


async def test_events_stream_while_the_task_is_still_running() -> None:
    clock = StageClock()
    queue: asyncio.Queue[str] = asyncio.Queue()
    listen = stage_listener(clock, queue)
    gate = asyncio.Event()

    async def work() -> str:
        listen("query_understanding")
        await gate.wait()  # the pipeline is still busy; the client must already have the event
        listen("embed")
        return "done"

    task = asyncio.ensure_future(work())
    seen: list[str] = []
    async for ev in events_until_done(task, queue):
        seen.append(json.loads(ev.split("data: ")[1])["stage"])
        if len(seen) == 1:
            assert not task.done()
            gate.set()
    assert seen == ["understand", "search"]
    assert task.result() == "done"


async def test_conversation_stream_reports_stages_and_timings() -> None:
    from unittest.mock import AsyncMock

    from callobuzz_rag.api.services.streaming_service import stream_message_response

    corpus_id = uuid.uuid4()
    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(
        return_value=SimpleNamespace(corpus_id=corpus_id, message_count=0, total_tokens=0)
    )
    searcher = MagicMock()
    searcher.search = AsyncMock(return_value=[SimpleNamespace(text="Open 9 to 6")])

    async def tokens(_msgs):
        yield "We open "
        yield "at 9."

    llm = MagicMock()
    llm.complete_stream = MagicMock(side_effect=tokens)

    with patch(
        "callobuzz_rag.memory.context_builder.ConversationContextBuilder.build_context",
        AsyncMock(return_value=[{"role": "user", "content": "hours?"}]),
    ):
        events = [
            e
            async for e in stream_message_response(
                conversation_id=uuid.uuid4(),
                corpus_id=corpus_id,
                content="hours?",
                session_factory=MagicMock(return_value=session),
                llm_provider=llm,
                make_searcher=lambda _s: searcher,
            )
        ]

    parsed = [(e.split("\n")[0][7:], json.loads(e.split("data: ")[1])) for e in events]
    assert [d["stage"] for n, d in parsed if n == "stage"] == ["understand", "search", "write"]
    done = parsed[-1][1]
    assert parsed[-1][0] == "done"
    assert set(done["timings"]) == {"understand", "search", "write"}
    assert done["latency_ms"] >= 0
    assert "thinking_ms" not in done  # the model reported no thinking


async def test_thinking_time_counts_the_wait_before_the_stream_opens() -> None:
    """A provider that holds the response until reasoning is done delays `acompletion`
    itself; that wait is thinking time, so the clock is not restarted once it returns."""
    usage = SimpleNamespace(completion_tokens_details=SimpleNamespace(reasoning_tokens=5))

    async def fake(*_a, **_k):
        await asyncio.sleep(0.06)  # headers arrive only after the model has thought

        async def body():
            yield _chunk(content="Yes")
            yield _chunk(choices=False, usage=usage)

        return body()

    with patch("litellm.acompletion", side_effect=fake), collect_stream_stats() as stats:
        _ = [c async for c in _provider().complete_stream([LLMMessage(role="user", content="q")])]
    assert stats.thinking_ms is not None and stats.thinking_ms >= 50
