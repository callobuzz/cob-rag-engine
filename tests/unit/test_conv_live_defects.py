"""Three defects seen live against a real model, each reproduced here with fakes:

1. "namaste, kaise ho?" was a business question: small talk only matched one phrase.
2. "Who is the prime minister of India?" was a business question: the label call outlived
   its 1.5 s budget on a reasoning model and the fail-safe label won.
3. A refusal written by the answer model released the sources frame as if it had answered.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.conversation import director as director_mod
from callobuzz_rag.conversation.answering import AnswerDeps
from callobuzz_rag.conversation.director import Director, TurnInput
from callobuzz_rag.conversation.envelope import DoneEvent, SourcesEvent, TokenEvent
from callobuzz_rag.conversation.labeler import LABEL_PROMPT, Labeler
from callobuzz_rag.conversation.offers import OfferRegistry
from callobuzz_rag.conversation.routing import decide_route, needs_retrieval
from callobuzz_rag.conversation.rules import rule_labeler
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.types import Channel, Route, TurnLabel
from callobuzz_rag.core.config import Settings
from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.context_assembly import CitedChunk
from callobuzz_rag.retrieval.models import ChunkScoreBreakdown
from tests.unit.conversation_helpers import FakeLLM, make_config

CORPUS = uuid.uuid4()
CHUNK = uuid.uuid4()
NOW = datetime(2026, 10, 5, 5, 0, tzinfo=UTC)
NO_ANSWER_EN = "I don't have that information in our documents."
OFF_TOPIC = {
    "label": "off_topic",
    "confidence": 0.99,
    "language": "en",
    "topic": None,
    "frustrated": False,
    "needs_human": False,
}
BUSINESS = {**OFF_TOPIC, "label": "business_question", "confidence": 0.9}


# ---- 1. small talk made of several phrases --------------------------------------------


@pytest.mark.parametrize(
    ("text", "intent", "lang"),
    [
        ("namaste, kaise ho?", "how_are_you", "hi-Latn"),
        ("Namaste kaise ho aap", "how_are_you", "hi-Latn"),
        ("namaskar, kya haal hai?", "how_are_you", "hi-Latn"),
        ("नमस्ते, आप कैसे हैं?", "how_are_you", "hi"),
        ("Hi, how are you?", "how_are_you", "en"),
        ("hello! how are you doing today", "how_are_you", "en"),
        ("namaste, aap kaun ho?", "who_are_you", "hi-Latn"),
        ("hi, who are you?", "who_are_you", "en"),
        ("kaise ho aap", "how_are_you", "hi-Latn"),
        ("kya haal chaal hai", "how_are_you", "hi-Latn"),
        ("dhanyavaad", "thanks", "hi-Latn"),
        ("shukriya ji", "thanks", "hi-Latn"),
        ("बहुत धन्यवाद जी", "thanks", "hi"),
        ("theek hai, dhanyavaad", "thanks", "hi-Latn"),
        ("thank you, bye", "bye", "en"),
        ("आप कौन हो", "who_are_you", "hi"),
    ],
)
def test_smalltalk_made_of_several_phrases(text: str, intent: str, lang: str) -> None:
    out = rule_labeler().classify(text)
    assert out.label == TurnLabel.SMALLTALK, text
    assert (out.intent, out.lang) == (intent, lang)


@pytest.mark.parametrize(
    "text",
    [
        "namaste, home collection kitne ka hai?",
        "hi, how are you priced for home collection?",
        "kaise ho aap, timings kya hain",
        "theek hai but what is the price",
        "hello what are your timings",
    ],
)
def test_a_real_question_after_the_greeting_is_not_smalltalk(text: str) -> None:
    assert rule_labeler().classify(text).label is None


@pytest.mark.parametrize("text", ["theek hai", "ठीक है"])
def test_a_bare_acknowledgement_is_other_without_a_model_call(text: str) -> None:
    # It may be a "yes" to the last offer, so it is never small talk (fixtures: other).
    assert rule_labeler().classify(text).label == TurnLabel.OTHER


async def test_the_director_answers_a_hinglish_greeting_without_a_model_or_retrieval() -> None:
    llm = FakeLLM()
    orch = _orch()
    events = await _events(_director(llm, orch), _turn("namaste, kaise ho?"))
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.label == TurnLabel.SMALLTALK and done.route == Route.SMALLTALK
    assert llm.calls == [] and not [e for e in events if isinstance(e, SourcesEvent)]
    assert "Main theek hoon" in done.text
    orch.run_retrieval_pipeline.assert_not_awaited()


# ---- 2. off-topic questions reach the model, which gets time to answer ----------------


def _labeler(llm: FakeLLM) -> Labeler:
    s = Settings(_env_file=None)
    return Labeler(llm=llm, cache=None, timeout_s=s.assistant_label_timeout_ms / 1000, ttl_s=60)


async def test_the_default_label_budget_outlasts_a_reasoning_model_call() -> None:
    # Measured live: gpt-oss-120b through OpenRouter at low effort answers in 1.5-2 s.
    llm = FakeLLM(json_replies=[OFF_TOPIC], delay_s=1.8)
    out = await _labeler(llm).by_model(
        "Who is the prime minister of India?",
        config=make_config(),
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    assert out.label == TurnLabel.OFF_TOPIC and out.source == "model"


async def test_an_off_topic_label_declines_with_a_pivot_and_needs_no_documents() -> None:
    config = make_config()
    out = await _labeler(FakeLLM(json_replies=[OFF_TOPIC])).by_model(
        "Who is the prime minister of India?",
        config=config,
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    assert needs_retrieval(out, config) is False
    decision = decide_route(
        label=out, gate=None, config=config, state=ConversationState(), has_handoff=False
    )
    assert decision.route == Route.DECLINE and decision.strike == "off_topic"


async def test_the_director_declines_off_topic_with_no_sources_and_one_strike() -> None:
    llm = FakeLLM(json_replies=[OFF_TOPIC])
    events = await _events(_director(llm), _turn("Who is the prime minister of India?"))
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.label == TurnLabel.OFF_TOPIC and done.route == Route.DECLINE
    assert not [e for e in events if isinstance(e, SourcesEvent)] and done.sources == []
    assert "I can help with" in done.text and "Home collection" in done.text
    assert not [c for c in llm.calls if c[0] == "stream"]  # no answer-model call


async def test_a_truncated_reply_with_a_clear_label_is_still_used() -> None:
    # Seen live: the model cut its JSON short, which used to mean "business question".
    cut = '{\n  "label": "off_topic",\n  "confidence": "0.99",\n  "language": "en", "top'
    out = await _labeler(FakeLLM(json_replies=[cut])).by_model(
        "Who won the cricket world cup?",
        config=make_config(),
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    assert out.label == TurnLabel.OFF_TOPIC and out.source == "model"
    assert out.confidence == pytest.approx(0.99)


async def test_garbage_is_still_the_business_fallback() -> None:
    out = await _labeler(FakeLLM(json_replies=["I think this is off topic"])).by_model(
        "hmm", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    assert out.label == TurnLabel.BUSINESS_QUESTION and out.source == "fallback"


def test_the_prompt_carries_the_scope_mode_and_does_not_let_doubt_swallow_off_topic() -> None:
    prompt = Labeler._prompt(
        "who is the prime minister?",
        make_config(scope={"scope_mode": "strict_docs"}),
        [],
    )
    assert "strict_docs" in prompt and "Shree Labs" in prompt and "Home collection" in prompt
    assert "never off_topic" in LABEL_PROMPT.lower()
    assert "general knowledge" in LABEL_PROMPT.lower()


def test_without_a_fast_model_the_main_model_labels_at_low_effort() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="openai/gpt-oss-120b",
        llm_fast_model="",
        llm_provider="openrouter",
        llm_api_key="k",
    )
    provider = LiteLLMProvider(settings)
    options = provider.fast_call_options()
    assert options["model"] == "openai/gpt-oss-120b"
    assert options["thinking"] == "off" and "max_tokens" not in options
    # gpt-oss cannot switch thinking off: its floor, low, is what the call runs at.
    assert provider.thinking_effective("openai/gpt-oss-120b", "off") == "low"


# ---- 3. a refusal is not an answer: no sources ----------------------------------------


def _orch(dense: float = 0.72) -> MagicMock:
    cc = CitedChunk(
        citation_index=1,
        chunk_id=CHUNK,
        text="Home sample collection costs Rs 150 within Pune city.",
        file_name="home-collection.pdf",
        page=1,
        score=0.9,
        token_count=12,
        section="Charges",
    )
    pipeline = SimpleNamespace(
        processed_query="q",
        assembled=SimpleNamespace(
            messages=[LLMMessage("system", "s"), LLMMessage("user", "CONTEXT q")],
            cited_chunks=[cc],
        ),
        score_breakdown={
            str(CHUNK): ChunkScoreBreakdown(fused_score=1.0, pipeline_rank=1, dense_score=dense)
        },
        stage_timings_ms={"embed": 4},
    )
    orch = MagicMock()
    orch.run_retrieval_pipeline = AsyncMock(return_value=pipeline)
    return orch


def _director(llm: FakeLLM, orch: MagicMock | None = None) -> Director:
    session = MagicMock()
    session.close = AsyncMock()
    session.commit = AsyncMock()
    session.invalidate = AsyncMock()
    deps = AnswerDeps(
        settings=Settings(_env_file=None),
        session_factory=lambda: session,
        orchestrator_factory=lambda s: orch or _orch(),
        retrieval_kwargs={},
        llm=llm,
        model_name="answer-model",
        translate_cache=None,
        payer="byok",
    )
    return Director(deps, labeler=_labeler(llm), registry=OfferRegistry())


def _turn(text: str, **cfg: Any) -> TurnInput:
    return TurnInput(
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        applied_embedding=None,
        runtime=RuntimeConfig(
            config=make_config(**cfg), version=3, draft_revision=None, enabled=True
        ),
        text=text,
        channel=Channel.WIDGET,
        usage_source="widget:k",
        conversation_id=None,
        state=ConversationState(),
        history=[],
        page_url=None,
        session_token="ws_new",
        debug=False,
        is_preview=False,
        simulate=None,
        one_off_instructions=None,
        now=NOW,
    )


@pytest.fixture(autouse=True)
def _io() -> Any:
    with (
        patch.object(director_mod, "persist_turn", AsyncMock(return_value=(None, None))),
        patch.object(director_mod, "record_usage_new_session", AsyncMock()),
    ):
        yield


async def _events(d: Director, turn: TurnInput) -> list[Any]:
    return [e async for e in d.run_stream(turn)]


@pytest.mark.parametrize(
    "stream",
    [
        [NO_ANSWER_EN],
        ["I don't have that ", "information in our documents."],
        ["I dont have that information in our documents"],
        ["  ", NO_ANSWER_EN, "\n"],
    ],
)
async def test_a_refusal_from_the_model_sends_no_sources(stream: list[str]) -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=stream, text="Which test do you mean?")
    events = await _events(_director(llm), _turn("What is the weather like?"))
    assert not [e for e in events if isinstance(e, SourcesEvent)]
    done = events[-1]
    assert isinstance(done, DoneEvent)
    assert done.route == Route.NO_ANSWER and done.sources == []
    assert done.text.count("information in our documents") == 1


async def test_a_hindi_refusal_from_the_model_sends_no_sources() -> None:
    llm = FakeLLM(
        json_replies=[{**BUSINESS, "language": "hi-Latn"}],
        stream=["Yeh jaankari hamare documents mein nahi hai."],
        text="x",
    )
    events = await _events(_director(llm), _turn("India ke pradhan mantri kaun hain?"))
    assert not [e for e in events if isinstance(e, SourcesEvent)]
    assert events[-1].route == Route.NO_ANSWER and events[-1].sources == []


async def test_an_answer_that_merely_starts_like_a_refusal_keeps_its_sources() -> None:
    llm = FakeLLM(
        json_replies=[BUSINESS],
        stream=[NO_ANSWER_EN + " ", "Home collection costs Rs 150 in Pune [1]."],
    )
    events = await _events(_director(llm), _turn("home collection price and the weather?"))
    kinds = [type(e).__name__ for e in events]
    assert "SourcesEvent" in kinds and kinds.index("SourcesEvent") < kinds.index("TokenEvent")
    assert events[-1].route == Route.GROUNDED and events[-1].sources


async def test_a_normal_answer_still_streams_with_sources_first() -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=["Home collection costs Rs 150 in Pune [1]."])
    events = await _events(_director(llm), _turn("home collection price?"))
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("SourcesEvent") < kinds.index("TokenEvent")
    assert events[-1].route == Route.GROUNDED and len(events[-1].sources) == 1


async def test_the_refusal_is_spoken_once() -> None:
    llm = FakeLLM(json_replies=[BUSINESS], stream=[NO_ANSWER_EN], text="Which one?")
    events = await _events(_director(llm), _turn("what is the weather?"))
    spoken = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert spoken == events[-1].text
