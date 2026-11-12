"""C1: the director end to end with fakes: event order, routes, strikes, PII, offers,
trace, persistence and metering."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.schemas.assistant import HandoffCardBlock
from callobuzz_rag.conversation import director as director_mod
from callobuzz_rag.conversation.director import (
    DirectorError,
    TurnRecord,
)
from callobuzz_rag.conversation.director import persist_turn as real_persist_turn
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    ErrorEvent,
    MetaEvent,
    SourcesEvent,
    StageEvent,
    TokenEvent,
    TraceEvent,
    UiEvent,
)
from callobuzz_rag.conversation.labeler import LabelResult
from callobuzz_rag.conversation.offers import OfferKind, OfferRegistry
from callobuzz_rag.conversation.pii import verhoeff_digit
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import Route, TurnLabel
from callobuzz_rag.core.exceptions import RetrievalError
from callobuzz_rag.llm.models import LLMMessage
from tests.unit.conversation_helpers import (
    NOW,
    FakeLLM,
    fake_orch,
    make_director,
    make_turn,
    run_events,
)

_orch, _director, _turn, _events = fake_orch, make_director, make_turn, run_events

BUSINESS = {
    "label": "business_question",
    "confidence": 0.9,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


def _text(events: list[Any]) -> str:
    return "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_rule_smalltalk_needs_no_model_and_is_free(_io: Any) -> None:
    llm = FakeLLM()
    events = await _events(
        _director(llm), _turn("namaste", voice={"greeting": {"hi": "नमस्ते! मैं आशा हूँ।"}})
    )
    # The first frame is the `understand` stage, sent before anything is decided.
    assert isinstance(events[0], StageEvent) and events[0].stage == "understand"
    assert isinstance(events[1], MetaEvent) and events[1].route == Route.SMALLTALK
    assert events[1].session == "ws_new"
    assert isinstance(events[-1], DoneEvent) and events[-1].route == Route.SMALLTALK
    assert llm.calls == []
    _io.meter.assert_not_awaited()  # no model call: nothing to meter, no question counted
    assert events[-1].usage["model_calls"] == 0


async def test_grounded_turn(_io: Any) -> None:
    llm = FakeLLM(
        json_replies=[{**BUSINESS, "topic": "home_collection"}],
        stream=["Home collection costs Rs 150 in Pune [1]. ", "Reports in 2 days."],
    )
    events = await _events(_director(llm), _turn("How much is home collection?"))
    kinds = [type(e).__name__ for e in events]
    assert kinds[:2] == ["StageEvent", "MetaEvent"] and kinds[-1] == "DoneEvent"
    assert kinds.index("SourcesEvent") < kinds.index("TokenEvent")
    stages = [e.stage for e in events if isinstance(e, StageEvent)]
    assert stages[0] == "understand" and "write" in stages
    assert stages == sorted(stages, key=["understand", "search", "rerank", "check", "write"].index)
    done = events[-1]
    assert done.route == Route.GROUNDED and done.claims_removed == 1
    assert "Rs 150" in done.text and "2 days" not in done.text
    rec = _io.persist.await_args.args[1]
    assert rec.route == Route.GROUNDED and rec.gate.passed and rec.sources
    kw = _io.meter.await_args.kwargs
    assert kw["requests"] == 1 and kw["payer"] == "byok" and kw["model"] == "answer-model"
    assert kw["cost"].calls == 2  # the label and the answer, one ledger
    assert kw["request_id"] == done.query_id  # BIL3: the answer is charged under the turn's id


async def test_done_keeps_the_playground_contract(_io: Any) -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 in Pune [1]."])
    done = await _director(llm).run(
        _turn("home collection price?", scope={"min_retrieval_score": 0.5})
    )
    assert done.context_texts == ["Home sample collection costs Rs 150 within Pune city."]
    assert done.gate_threshold == 0.5 and "gate_threshold" not in done.timings
    assert set(done.timings) <= {"understand", "search", "rerank", "check", "write", "verify"}
    assert "write" in done.timings
    assert {"safety", "language", "label", "embed"} <= set(done.stage_timings_ms)
    assert done.retrieval_ms is not None and done.processed_query == "q"
    assert done.usage["model_calls"] == 2 and done.usage["cost_known"] is True
    assert done.usage["cost_usd"] == pytest.approx(0.002)
    rec = _io.persist.await_args.args[1]
    assert rec.usage == done.usage  # the same dict reaches QueryLog.token_usage
    assert rec.timings["gate_threshold"] == 0.5 and "total" in rec.timings


async def test_gate_failure_is_no_answer_without_sources(_io: Any) -> None:
    llm = FakeLLM(json_replies=[BUSINESS], text="Which test do you mean?")
    events = await _events(
        _director(llm, _orch(dense=0.2)),
        _turn("price of the gold package?", scope={"min_retrieval_score": 0.5}),
    )
    assert not [e for e in events if isinstance(e, SourcesEvent)]
    done = events[-1]
    assert done.route == Route.NO_ANSWER
    assert "information" in done.text
    assert "Which test do you mean?" in done.text  # clarify once
    assert _io.persist.await_args.args[1].state.clarify_used
    assert _io.meter.await_args.kwargs["cost"].calls == 2  # the label and the clarifying question


async def test_second_no_answer_offers_handoff_through_registry(_io: Any) -> None:
    class Card:
        kind = OfferKind.HANDOFF_CARD

        def wanted(self, ctx: Any) -> str | None:
            return None

        async def build(self, ctx: Any, reason: str) -> HandoffCardBlock:
            return HandoffCardBlock(open_now=True, message="Talk to us", channels=[], reason=reason)

    reg = OfferRegistry()
    reg.register(Card())
    llm = FakeLLM(json_replies=[BUSINESS])
    events = await _events(
        _director(llm, _orch(dense=0.2), reg),
        _turn(
            "price of the gold package?",
            state=ConversationState(clarify_used=True),
            scope={"min_retrieval_score": 0.5},
        ),
    )
    ui = [e for e in events if isinstance(e, UiEvent)]
    assert ui and ui[0].block.reason == "forced"


async def test_off_topic_strikes_then_stop(_io: Any) -> None:
    reply = {**BUSINESS, "label": "off_topic", "confidence": 0.95}
    d = _director(FakeLLM(json_replies=[reply]))
    events = await _events(
        d, _turn("who will win the IPL?", state=ConversationState(off_topic_strikes=2))
    )
    done = events[-1]
    assert done.route == Route.DECLINE and "+919822012345" in done.text
    assert _io.persist.await_args.args[1].state.stopped


async def test_pii_is_masked_before_the_model_and_noticed(_io: Any) -> None:
    aadhaar = "23456789012" + verhoeff_digit("23456789012")
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150."])
    events = await _events(
        _director(llm), _turn(f"My aadhaar {aadhaar}, price of home collection?")
    )
    assert all(aadhaar not in m.content for _, msgs, _ in llm.calls for m in msgs)
    assert _text(events).startswith("For your safety")
    assert aadhaar not in _io.persist.await_args.args[1].masked


async def test_pii_notice_comes_after_sources_on_a_grounded_turn(_io: Any) -> None:
    aadhaar = "23456789012" + verhoeff_digit("23456789012")
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150."])
    events = await _events(
        _director(llm), _turn(f"My aadhaar {aadhaar}, price of home collection?")
    )
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("SourcesEvent") < kinds.index("TokenEvent")
    assert _text(events).startswith("For your safety") and "Rs 150" in _text(events)


async def test_human_request_without_handoff_provider_lists_contacts(_io: Any) -> None:
    events = await _events(_director(FakeLLM()), _turn("I want to talk to a human"))
    done = events[-1]
    assert done.route == Route.VERBATIM and "wa.me/919822012345" in done.text


async def test_trace_only_with_debug(_io: Any) -> None:
    llm = FakeLLM()
    assert not [e for e in await _events(_director(llm), _turn("hi")) if isinstance(e, TraceEvent)]
    trace = [
        e
        for e in await _events(_director(llm), _turn("hi", debug=True))
        if isinstance(e, TraceEvent)
    ]
    assert trace and trace[0].trace["label"]["source"] == "rules"
    assert "stage_timings_ms" in trace[0].trace


async def test_model_smalltalk_cancels_speculative_retrieval(_io: Any) -> None:
    orch = _orch()

    async def slow(**kw: Any) -> Any:
        await asyncio.sleep(5)

    orch.run_retrieval_pipeline = AsyncMock(side_effect=slow)
    llm = FakeLLM(
        json_replies=[{**BUSINESS, "label": "smalltalk"}],
        text="I'd love to chat! I can help with tests.",
    )
    events = await asyncio.wait_for(
        _events(_director(llm, orch), _turn("tell me something fun")), 2
    )
    assert events[-1].route == Route.SMALLTALK


async def test_generation_failure_is_an_error_event_and_meters_the_spend(_io: Any) -> None:
    class Broken(FakeLLM):
        async def complete_stream(self, messages: list[LLMMessage], **kw: Any):  # type: ignore[override]
            raise RuntimeError("provider down")
            yield ""  # pragma: no cover

    llm = Broken(json_replies=[BUSINESS])
    events = await _events(_director(llm), _turn("price?"))
    assert isinstance(events[-1], ErrorEvent) and "provider" not in events[-1].detail
    _io.persist.assert_not_awaited()
    kw = _io.meter.await_args.kwargs  # the label call was paid for
    assert kw["requests"] == 0 and kw["cost"].calls == 1 and kw["prompt_tokens"] == 0
    assert isinstance(kw["request_id"], uuid.UUID)  # BIL3: derived from the turn's id
    with pytest.raises(DirectorError):
        await _director(Broken(json_replies=[BUSINESS])).run(_turn("price?"))


async def test_retrieval_failure_carries_a_code_and_meters_the_spend(_io: Any) -> None:
    orch = MagicMock()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=RetrievalError("qdrant down"))
    llm = FakeLLM(json_replies=[BUSINESS])
    events = await _events(_director(llm, orch), _turn("price?"))
    err = events[-1]
    assert isinstance(err, ErrorEvent) and err.code == "retrieval_failed"
    assert "qdrant" not in err.detail
    _io.persist.assert_not_awaited()
    assert _io.meter.await_args.kwargs["requests"] == 0


async def test_run_returns_done(_io: Any) -> None:
    done = await _director(FakeLLM()).run(_turn("thanks"))
    assert isinstance(done, DoneEvent) and done.label == TurnLabel.SMALLTALK and done.text


async def test_plan_routes_without_answering(_io: Any) -> None:
    llm = FakeLLM(json_replies=[{**BUSINESS, "confidence": 0.8}])
    plan = await _director(llm, _orch(dense=0.2)).plan(
        _turn("price of the gold package?", scope={"min_retrieval_score": 0.5})
    )
    assert plan.label == TurnLabel.BUSINESS_QUESTION and plan.label_confidence == 0.8
    assert plan.route == Route.NO_ANSWER and plan.gate is not None and not plan.gate.passed
    assert [c for c in llm.calls if c[0] != "json"] == []  # no answer-model call
    _io.persist.assert_not_awaited()
    _io.meter.assert_not_awaited()


async def test_plan_and_run_agree_on_smalltalk(_io: Any) -> None:
    d = _director(FakeLLM())
    plan = await d.plan(_turn("namaste"))
    done = await d.run(_turn("namaste"))
    assert plan.route == done.route == Route.SMALLTALK and plan.gate is None


async def test_pre_turn_hook_reply_ends_the_turn_as_lead(_io: Any) -> None:
    from callobuzz_rag.conversation.offers import PreTurnContext, PreTurnResult

    seen: list[PreTurnContext] = []

    async def capture(ctx: PreTurnContext) -> PreTurnResult | None:
        seen.append(ctx)
        return PreTurnResult(stored_text="[phone shared]", reply="Thanks! We will call you soon.")

    reg = OfferRegistry()
    reg.register_pre_turn(capture)
    llm = FakeLLM()
    events = await _events(_director(llm, registry=reg), _turn("my number is 98220 12345"))
    assert seen and seen[0].text == "my number is 98220 12345"
    done = events[-1]
    assert done.route == Route.LEAD and done.text == "Thanks! We will call you soon."
    assert llm.calls == []
    assert _io.persist.await_args.args[1].masked == "[phone shared]"


async def test_string_offer_is_spoken_after_the_answer(_io: Any) -> None:
    class AskPhone:
        kind = OfferKind.LEAD_FORM

        def wanted(self, ctx: Any) -> str | None:
            return "buying_intent" if ctx.signals.label == TurnLabel.BUYING_INTENT else None

        async def build(self, ctx: Any, reason: str) -> str:
            assert ctx.question and ctx.llm is not None and ctx.probe is not None
            return "Could you share your phone number?"

    reg = OfferRegistry()
    reg.register(AskPhone())
    llm = FakeLLM(stream=["Home collection costs Rs 150 in Pune [1]."])
    events = await _events(_director(llm, registry=reg), _turn("I want to book home collection"))
    tokens = [e.content for e in events if isinstance(e, TokenEvent)]
    assert tokens[-1] == "\n\nCould you share your phone number?"
    assert events[-1].text.endswith("Could you share your phone number?")


async def test_probe_uses_no_stage_events_and_the_same_ledger(_io: Any) -> None:
    probed: list[bool] = []

    class Prober:
        kind = OfferKind.CTA

        def wanted(self, ctx: Any) -> str | None:
            return "probe"

        async def build(self, ctx: Any, reason: str) -> None:
            probed.append(await ctx.probe("what about Sunday?"))
            return None

    reg = OfferRegistry()
    reg.register(Prober())
    events = await _events(_director(FakeLLM(), registry=reg), _turn("thanks"))
    assert probed == [True]
    assert [e.stage for e in events if isinstance(e, StageEvent)] == ["understand"]


async def test_signals_are_complete(_io: Any) -> None:
    captured: list[Any] = []

    class Spy:
        kind = OfferKind.CTA

        def wanted(self, ctx: Any) -> str | None:
            captured.append(ctx.signals)
            return None

        async def build(self, ctx: Any, reason: str) -> None:
            return None

    reg = OfferRegistry()
    reg.register(Spy())
    await _events(_director(FakeLLM(), registry=reg), _turn("please call me back"))
    s = captured[0]
    assert s.asked_for_contact and s.abuse_level == 0 and s.now == NOW and not s.is_preview


class _Session:
    """An async-context session with just what persist_turn touches."""

    def __init__(self, conversation: Any) -> None:
        self.added: list[Any] = []
        self.conversation = conversation
        self.committed = False

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, model: Any, ident: Any, **_kw: Any) -> Any:
        return self.conversation

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    def add_all(self, objs: list[Any]) -> None:
        self.added.extend(objs)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self) -> None:
        self.committed = True


async def test_persist_orders_the_question_before_its_answer_and_keeps_the_cost() -> None:
    conv = SimpleNamespace(
        id=uuid.uuid4(), message_count=0, total_tokens=0, title=None, assistant_state=None
    )
    session = _Session(conv)
    turn = _turn("hi")
    turn.conversation_id = conv.id
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost_usd": 0.002}
    rec = TurnRecord(
        turn=turn,
        query_id=uuid.uuid4(),
        masked="hi",
        label=LabelResult(TurnLabel.SMALLTALK, "rules", 1.0),
        language="en",
        script="latin",
        route=Route.SMALLTALK,
        topic_id=None,
        gate=None,
        answer="Hello!",
        sources=[],
        claims_removed=0,
        claims_flagged=0,
        rules_fired=[],
        offers=[],
        latency_ms=5,
        timings={"total": 5},
        model=None,
        usage=usage,
        state=ConversationState(),
    )
    with patch.object(director_mod, "record_turn", AsyncMock()):
        user_id, reply_id = await real_persist_turn(lambda: session, rec)
    assert user_id and reply_id and session.committed
    user, reply = (m for m in session.added if hasattr(m, "role"))
    assert user.role == "user" and reply.role == "assistant"
    assert user.created_at < reply.created_at  # never tied inside one transaction
    log = next(o for o in session.added if hasattr(o, "token_usage"))
    assert log.token_usage == usage and log.query_text == "hi"
    assert conv.message_count == 2 and conv.total_tokens == 15 and conv.title == "hi"


async def test_persist_keeps_what_the_event_route_wrote_during_the_turn() -> None:
    """A CTA dismissed (and chips tapped) between the turn's load and save survives it."""
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        message_count=0,
        total_tokens=0,
        title=None,
        assistant_state={"cta_declined": ["whatsapp"], "chip_clicks": 2, "cta_clicks": 1},
    )
    session = _Session(conv)
    turn = _turn("hi")
    turn.conversation_id = conv.id
    state = ConversationState()
    state.record_cta_shown("whatsapp")
    rec = TurnRecord(
        turn=turn,
        query_id=uuid.uuid4(),
        masked="hi",
        label=LabelResult(TurnLabel.SMALLTALK, "rules", 1.0),
        language="en",
        script="latin",
        route=Route.SMALLTALK,
        topic_id=None,
        gate=None,
        answer="Hello!",
        sources=[],
        claims_removed=0,
        claims_flagged=0,
        rules_fired=[],
        offers=[],
        latency_ms=5,
        timings={"total": 5},
        model=None,
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        state=state,
    )
    with patch.object(director_mod, "record_turn", AsyncMock()):
        await real_persist_turn(lambda: session, rec)
    saved = conv.assistant_state
    assert saved["cta_declined"] == ["whatsapp"] and saved["cta_shown"] == ["whatsapp"]
    assert saved["chip_clicks"] == 2 and saved["cta_clicks"] == 1


async def test_meta_names_the_label_source_for_a_debug_turn_only(_io: Any) -> None:
    llm = FakeLLM()
    debug = await _events(_director(llm), _turn("hi", debug=True))
    plain = await _events(_director(llm), _turn("hi"))
    assert next(e for e in debug if isinstance(e, MetaEvent)).label_source == "rules"
    assert next(e for e in plain if isinstance(e, MetaEvent)).label_source is None


async def test_a_shared_provider_meters_each_turn_by_its_own_calls(_io: Any) -> None:
    llm = FakeLLM(
        json_replies=[BUSINESS, BUSINESS],
        stream=["Home collection costs Rs 150 in Pune [1]."],
    )
    d = _director(llm)
    await _events(d, _turn("How much is home collection?"))
    first = _io.meter.await_args.kwargs["cost"].calls
    await _events(d, _turn("How much is home collection?"))
    second = _io.meter.await_args.kwargs
    assert second["cost"].calls == first  # not first + its own
    assert second["requests"] == 1


async def test_a_free_turn_after_a_paid_one_is_not_counted_as_a_question(_io: Any) -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 in Pune [1]."])
    d = _director(llm)
    await _events(d, _turn("How much is home collection?"))
    _io.meter.reset_mock()
    await _events(d, _turn("hello"))  # small talk by rule: no model call
    _io.meter.assert_not_awaited()


async def test_a_turn_that_must_not_persist_is_metered_but_not_stored(_io: Any) -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 in Pune [1]."])
    turn = _turn("How much is home collection?")
    turn.persist = False
    events = await _events(_director(llm), turn)
    assert events[-1].message_id is None
    _io.persist.assert_not_awaited()
    assert _io.meter.await_args.kwargs["requests"] == 1


async def test_a_follow_up_call_on_a_free_turn_adds_cost_but_no_question(_io: Any) -> None:
    llm = FakeLLM(json_replies=[{"questions": ["a?"]}])

    class Followup:
        kind = OfferKind.QUICK_REPLIES

        def wanted(self, ctx: Any) -> str | None:
            return "answer"

        async def build(self, ctx: Any, reason: str) -> list[Any]:
            await ctx.llm.complete_json([LLMMessage(role="user", content="x")])
            return []

    reg = OfferRegistry()
    reg.register(Followup())
    await _events(_director(llm, registry=reg), _turn("hello"))  # small talk by rule: free
    kw = _io.meter.await_args.kwargs
    assert kw["requests"] == 0 and kw["cost"].calls == 1


# TV4 value
async def test_the_turn_record_carries_whether_the_business_was_open(_io: Any) -> None:
    from datetime import UTC, datetime

    from callobuzz_rag.conversation.types import OfficeState

    open_turn = _turn("hello")  # Monday 10:30 IST, the card is open 07:00-20:00
    await _events(_director(FakeLLM()), open_turn)
    assert _io.persist.await_args.args[1].office is OfficeState.OPEN

    closed_turn = _turn("hello")
    closed_turn.now = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)  # 23:30 IST
    await _events(_director(FakeLLM()), closed_turn)
    assert _io.persist.await_args.args[1].office is OfficeState.CLOSED

    no_hours = _turn("hello")
    no_hours.runtime.config.identity.card.hours = []
    await _events(_director(FakeLLM()), no_hours)
    assert _io.persist.await_args.args[1].office is OfficeState.UNKNOWN


@pytest.mark.parametrize(
    ("office", "expected"), [("open", True), ("closed", False), ("unknown", None)]
)
async def test_persist_turn_stores_office_open(office: str, expected: bool | None) -> None:
    from contextlib import asynccontextmanager

    from callobuzz_rag.conversation.types import Channel, OfficeState

    rec = SimpleNamespace(
        turn=SimpleNamespace(
            conversation_id=None,
            corpus_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            channel=Channel.WIDGET,
            runtime=SimpleNamespace(version=3),
            is_preview=False,
        ),
        query_id=uuid.uuid4(),
        masked="hi",
        answer="hello",
        sources=[],
        usage={},
        latency_ms=1,
        model=None,
        label=SimpleNamespace(
            label=SimpleNamespace(value="small_talk"), source="rules", confidence=1.0
        ),
        language="en",
        script="latin",
        route=Route.SMALLTALK,
        topic_id=None,
        gate=None,
        claims_removed=0,
        claims_flagged=0,
        rules_fired=[],
        offers=[],
        timings={},
        office=OfficeState(office),
    )
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    with patch.object(director_mod, "record_turn", AsyncMock()) as record:
        await real_persist_turn(factory, rec)  # type: ignore[arg-type]
    assert record.await_args.kwargs["office_open"] is expected


# /TV4 value


# TV2 reference
class _CardSpy:
    kind = OfferKind.HANDOFF_CARD

    def __init__(self, wanted: str | None = "explicit_request") -> None:
        self._wanted = wanted
        self.seen: list[Any] = []

    def wanted(self, ctx: Any) -> str | None:
        return self._wanted

    async def build(self, ctx: Any, reason: str) -> HandoffCardBlock:
        self.seen.append(ctx)
        return HandoffCardBlock(
            open_now=True, message="Talk to us", channels=[], reason=reason, ref_code=ctx.ref_code
        )


def _with_conversation(turn: Any, **changes: Any) -> Any:
    from dataclasses import replace

    return replace(turn, conversation_id=uuid.uuid4(), **changes)


async def test_a_card_reserves_the_reference_once_and_passes_it(_io: Any) -> None:
    spy = _CardSpy()
    reg = OfferRegistry()
    reg.register(spy)
    reserve = AsyncMock(return_value="SD-4F7K")
    with patch("callobuzz_rag.conversation.handoff.reference.reserve_ref", reserve):
        events = await _events(
            _director(FakeLLM(json_replies=[BUSINESS], stream=["Rs 150."]), _orch(), reg),
            _with_conversation(_turn("price of home collection?")),
        )
    assert reserve.await_count == 1
    assert spy.seen[0].ref_code == "SD-4F7K"
    ui = [e for e in events if isinstance(e, UiEvent)]
    assert ui and ui[0].block.ref_code == "SD-4F7K"


@pytest.mark.parametrize("case", ["preview", "stateless", "no_card"])
async def test_no_reference_when_no_card_can_be_shown(_io: Any, case: str) -> None:
    spy = _CardSpy(wanted=None if case == "no_card" else "explicit_request")
    reg = OfferRegistry()
    reg.register(spy)
    turn = _turn("price of home collection?")
    if case == "preview":
        turn = _with_conversation(turn, is_preview=True)
    elif case == "no_card":
        turn = _with_conversation(turn)
    reserve = AsyncMock(return_value="SD-4F7K")
    with patch("callobuzz_rag.conversation.handoff.reference.reserve_ref", reserve):
        await _events(
            _director(FakeLLM(json_replies=[BUSINESS], stream=["Rs 150."]), _orch(), reg), turn
        )
    reserve.assert_not_awaited()


async def test_recent_questions_keep_the_last_three_masked(_io: Any) -> None:
    aadhaar = "23456789012" + verhoeff_digit("23456789012")
    state = ConversationState(recent_questions=["q1", "q2", "q3"])
    await _events(
        _director(FakeLLM(json_replies=[BUSINESS], stream=["Rs 150."])),
        _turn(f"My aadhaar {aadhaar}, price of home collection?", state=state),
    )
    assert len(state.recent_questions) == 3
    assert state.recent_questions[:2] == ["q2", "q3"]
    assert aadhaar not in state.recent_questions[-1]
    assert "[Aadhaar hidden]" in state.recent_questions[-1]


# /TV2 reference
