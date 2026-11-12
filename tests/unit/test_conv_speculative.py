"""The answer does not wait for a slow label (RAG_ASSISTANT_SPECULATIVE_ANSWER).

Retrieval already runs while the fast model labels the message. When retrieval finishes first
and its gate passes, the grounded answer starts too, as a business question; nothing it
produces leaves until the label agrees. A label that routes elsewhere, or that would compile a
different prompt, discards it: no sources and no answer text reach the visitor.
"""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    MetaEvent,
    SourcesEvent,
    StageEvent,
    TokenEvent,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm.models import LLMMessage, LLMResponse
from tests.unit.conversation_helpers import (
    FakeLLM,
    fake_orch,
    make_director,
    make_turn,
    run_events,
)

ANSWER = ["Home collection costs Rs 150 ", "within Pune city."]


def _label(label: str, confidence: float = 0.9, topic: str | None = None) -> dict[str, Any]:
    return {
        "label": label,
        "confidence": confidence,
        "language": "en",
        "topic": topic,
        "frustrated": False,
        "needs_human": False,
    }


class TimedLLM(FakeLLM):
    """A label call that takes `label_s`; records when each answer stream starts."""

    def __init__(self, *, label_s: float, **kw: Any) -> None:
        super().__init__(stream=ANSWER, **kw)
        self.label_s = label_s
        self.t0 = time.monotonic()
        self.label_done_at: float | None = None
        self.stream_started: list[float] = []

    async def complete_json(self, messages: list[LLMMessage], **kw: Any) -> LLMResponse:
        await asyncio.sleep(self.label_s)
        out = await super().complete_json(messages, **kw)
        self.label_done_at = time.monotonic() - self.t0
        return out

    async def complete_stream(self, messages: list[LLMMessage], **kw: Any) -> AsyncIterator[str]:
        self.stream_started.append(time.monotonic() - self.t0)
        async for piece in super().complete_stream(messages, **kw):
            yield piece


QUESTION = "how much does home collection cost in Kothrud for two people?"


def _director(llm: FakeLLM, *, speculate: bool = True) -> Any:
    d = make_director(llm, orch=fake_orch())
    d._deps.settings = Settings(_env_file=None, assistant_speculative_answer=speculate)
    return d


def _text(events: list[Any]) -> str:
    return "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_the_answer_starts_before_a_slow_label_and_streams_after_meta() -> None:
    llm = TimedLLM(label_s=0.3, json_replies=[_label("business_question")])
    events = await run_events(_director(llm), make_turn(QUESTION))
    assert llm.stream_started and llm.label_done_at is not None
    assert llm.stream_started[0] < llm.label_done_at  # speculated while labelling
    assert len(llm.stream_started) == 1  # and kept: no second generation
    kinds = [type(e) for e in events if not isinstance(e, StageEvent)]
    assert kinds.index(MetaEvent) < kinds.index(SourcesEvent) < kinds.index(TokenEvent)
    assert sum(isinstance(e, SourcesEvent) for e in events) == 1
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.text == "".join(ANSWER)
    assert done.sources and done.route.value == "grounded"


async def test_same_turn_as_without_speculation() -> None:
    fast = await run_events(
        _director(TimedLLM(label_s=0.2, json_replies=[_label("business_question")])),
        make_turn(QUESTION),
    )
    slow = await run_events(
        _director(
            TimedLLM(label_s=0.2, json_replies=[_label("business_question")]), speculate=False
        ),
        make_turn(QUESTION),
    )
    a, b = fast[-1], slow[-1]
    assert isinstance(a, DoneEvent) and isinstance(b, DoneEvent)
    assert (a.text, a.route, len(a.sources)) == (b.text, b.route, len(b.sources))
    stages = [e.stage for e in fast if isinstance(e, StageEvent)]
    assert stages == [e.stage for e in slow if isinstance(e, StageEvent)]


async def test_off_without_the_setting() -> None:
    llm = TimedLLM(label_s=0.2, json_replies=[_label("business_question")])
    await run_events(_director(llm, speculate=False), make_turn(QUESTION))
    assert llm.label_done_at is not None and llm.stream_started[0] >= llm.label_done_at


async def test_a_human_request_discards_it_without_leaking_sources() -> None:
    llm = TimedLLM(label_s=0.3, json_replies=[_label("human_request")])
    events = await run_events(_director(llm), make_turn(QUESTION))
    assert llm.stream_started  # it did speculate
    assert not any(isinstance(e, SourcesEvent) for e in events)
    assert "Rs 150" not in _text(events)
    done = events[-1]
    assert isinstance(done, DoneEvent) and not done.sources
    assert done.route.value != "grounded"


async def test_a_confident_off_topic_label_discards_it() -> None:
    llm = TimedLLM(label_s=0.3, json_replies=[_label("off_topic", 0.95)])
    events = await run_events(_director(llm), make_turn(QUESTION))
    assert not any(isinstance(e, SourcesEvent) for e in events)
    assert "Rs 150" not in _text(events)
    assert isinstance(events[-1], DoneEvent) and events[-1].route.value == "decline"


async def test_a_label_that_changes_the_prompt_answers_again_with_the_right_one() -> None:
    """A guidance rule for buying intent changes the compiled prompt: the speculative answer,
    written for a business question, is thrown away and the turn is answered afresh."""
    llm = TimedLLM(label_s=0.3, json_replies=[_label("buying_intent")])
    rule = {
        "id": "buy-next",
        "type": "sales",
        "when": {"label": "buying_intent"},
        "instruction": "End with how to book.",
    }
    turn = make_turn(QUESTION, debug=True, rules=[rule])
    events = await run_events(_director(llm), turn)
    assert len(llm.stream_started) == 2
    assert sum(isinstance(e, SourcesEvent) for e in events) == 1
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.text == "".join(ANSWER)
    assert done.trace is not None and "buy-next" in done.trace["rules_fired"]


async def test_no_speculation_when_the_gate_fails() -> None:
    llm = TimedLLM(label_s=0.2, json_replies=[_label("business_question")])
    await run_events(_director(llm), make_turn(QUESTION, scope={"min_retrieval_score": 0.9}))
    assert llm.stream_started == []  # no answer at all: the gate refused the passages


async def test_rules_decided_turns_never_speculate() -> None:
    llm = TimedLLM(label_s=0.2)
    events = await run_events(_director(llm), make_turn("hello"))
    assert llm.stream_started == [] and llm.label_done_at is None
    assert isinstance(events[-1], DoneEvent)
