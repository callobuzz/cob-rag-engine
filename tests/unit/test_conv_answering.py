"""C1: retrieval with the translated variant, the grounded answer through the fact check,
the explainer and the clarifying question."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from callobuzz_rag.api.stage_events import StageClock
from callobuzz_rag.conversation import answering
from callobuzz_rag.conversation.answering import (
    Acc,
    AnswerDeps,
    cancel_retrieval,
    clarify_question,
    explainer,
    grounded,
    start_retrieval,
)
from callobuzz_rag.conversation.compiler import CompileTurn
from callobuzz_rag.conversation.director import _PROBE_OVERRIDES
from callobuzz_rag.conversation.envelope import SourcesEvent, StageEvent, TokenEvent
from callobuzz_rag.conversation.types import LanguageInfo, OfficeState, Route, Script, TurnLabel
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import QueryError, RetrievalError
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.context_assembly import CitedChunk
from callobuzz_rag.retrieval.models import ChunkScoreBreakdown
from tests.unit.conversation_helpers import FakeLLM, make_config

CORPUS = uuid.uuid4()
CHUNK = uuid.uuid4()
EN = LanguageInfo(code="en", script=Script.LATIN, confidence=0.99)
HINGLISH = LanguageInfo(code="hi-Latn", script=Script.LATIN, confidence=0.9)


def _pipeline(dense: float = 0.72) -> SimpleNamespace:
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
    return SimpleNamespace(
        processed_query="q",
        assembled=SimpleNamespace(
            messages=[
                LLMMessage("system", "old"),
                LLMMessage("user", "CONTEXT: [1] ... QUESTION: q"),
            ],
            cited_chunks=[cc],
        ),
        score_breakdown={
            str(CHUNK): ChunkScoreBreakdown(fused_score=1.0, pipeline_rank=1, dense_score=dense)
        },
        stage_timings_ms={"embed": 5, "search_dense": 9, "rerank": 4},
    )


def _deps(llm: FakeLLM, orchestrator: MagicMock) -> tuple[AnswerDeps, MagicMock]:
    session = MagicMock()
    session.close = AsyncMock()
    session.invalidate = AsyncMock()
    deps = AnswerDeps(
        settings=Settings(_env_file=None),
        session_factory=lambda: session,
        orchestrator_factory=lambda s: orchestrator,
        retrieval_kwargs={"top_k": 20, "final_k": 5},
        llm=llm,
        model_name="m",
        translate_cache=None,
        payer="operator",
    )
    return deps, session


def _turn(route: Route = Route.GROUNDED) -> CompileTurn:
    return CompileTurn(
        label=TurnLabel.BUSINESS_QUESTION,
        route=route,
        language="en",
        script=Script.LATIN,
        topic_id=None,
        page_url=None,
        office=OfficeState.OPEN,
        lead_captured=False,
        one_off_instructions=None,
    )


def _orch(pipeline: SimpleNamespace | None = None, delay: float = 0.0) -> MagicMock:
    async def run(**kw: object) -> SimpleNamespace:
        await asyncio.sleep(delay)
        return pipeline or _pipeline()

    orch = MagicMock()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=run)
    return orch


async def _retrieve(deps: AnswerDeps, **kw: object) -> answering.Retrieved:
    args: dict[str, object] = {
        "corpus_id": CORPUS,
        "text": "price?",
        "info": EN,
        "config": make_config(),
        "compile_turn": _turn(),
        "applied_embedding": None,
    }
    args.update(kw)
    return await start_retrieval(deps, **args)  # type: ignore[arg-type]


async def test_retrieval_gate_and_sources() -> None:
    orch = _orch()
    deps, session = _deps(FakeLLM(), orch)
    cfg = make_config(scope={"min_retrieval_score": 0.5})
    retrieved = await _retrieve(deps, text="home collection price?", config=cfg)
    assert retrieved.gate and retrieved.gate.passed and retrieved.gate.source == "corpus"
    assert retrieved.sources[0]["file_name"] == "home-collection.pdf"
    assert retrieved.sources[0]["score_breakdown"]["dense_score"] == 0.72
    kw = orch.run_retrieval_pipeline.await_args.kwargs
    assert kw["extra_queries"] is None and kw["top_k"] == 20 and kw["response_language"] is None
    assert kw["on_stage"] is None
    session.close.assert_awaited()


async def test_stage_callback_reaches_the_pipeline() -> None:
    orch = _orch()
    deps, _ = _deps(FakeLLM(), orch)
    seen: list[str] = []
    await _retrieve(deps, on_stage=seen.append)
    assert orch.run_retrieval_pipeline.await_args.kwargs["on_stage"] == seen.append


async def test_timings_split_public_stages_from_engine_steps() -> None:
    deps, _ = _deps(FakeLLM(), _orch())
    retrieved = await _retrieve(deps)
    assert retrieved.timings == {"search": 14, "rerank": 4}  # public stages only
    assert retrieved.stage_timings["embed"] == 5 and "gate_threshold" not in retrieved.timings
    assert retrieved.processed_query == "q"


async def test_hinglish_adds_translated_query() -> None:
    orch = _orch()
    deps, _ = _deps(FakeLLM(), orch)
    with patch.object(
        answering,
        "translate_for_retrieval",
        AsyncMock(return_value="What is the home collection charge?"),
    ):
        retrieved = await _retrieve(deps, text="home collection kitne ka hai", info=HINGLISH)
    assert orch.run_retrieval_pipeline.await_args.kwargs["extra_queries"] == [
        "What is the home collection charge?"
    ]
    assert retrieved.queries[1] == "What is the home collection charge?"
    assert "translate" in retrieved.stage_timings and "understand" in retrieved.timings


async def test_translation_shares_the_turns_cost_ledger() -> None:
    llm = FakeLLM(json_replies=[{"query": "What is the home collection charge?"}])
    deps, _ = _deps(llm, _orch())
    await _retrieve(deps, text="home collection kitne ka hai", info=HINGLISH)
    assert llm.cost_ledger.summary().calls == 1


async def test_retrieval_failures_carry_a_code_not_the_message() -> None:
    for exc, code in (
        (QueryError("empty"), "query_invalid"),
        (RetrievalError("boom"), "retrieval_failed"),
        (ValueError("x"), "retrieval_failed"),
    ):
        orch = MagicMock()
        orch.run_retrieval_pipeline = AsyncMock(side_effect=exc)
        deps, session = _deps(FakeLLM(), orch)
        retrieved = await _retrieve(deps)
        assert retrieved.error_code == code and retrieved.gate is None
        session.close.assert_awaited()


async def test_cancel_invalidates_the_connection() -> None:
    orch = _orch(delay=5)
    deps, session = _deps(FakeLLM(), orch)
    task = start_retrieval(
        deps,
        corpus_id=CORPUS,
        text="q",
        info=EN,
        config=make_config(),
        compile_turn=_turn(),
        applied_embedding=None,
    )
    await asyncio.sleep(0.01)
    await cancel_retrieval(task)
    session.invalidate.assert_awaited()


async def _collect(gen) -> list[object]:  # type: ignore[no-untyped-def]
    return [e async for e in gen]


async def _ground(llm: FakeLLM, config=None, clock=None):  # type: ignore[no-untyped-def]
    config = config or make_config()
    deps, _ = _deps(llm, _orch())
    retrieved = await _retrieve(deps, config=config)
    acc = Acc(query_id=uuid.uuid4())
    events = await _collect(
        grounded(
            deps,
            config=config,
            compile_turn=_turn(),
            retrieved=retrieved,
            question="price?",
            history=[],
            acc=acc,
            clock=clock or StageClock(),
        )
    )
    return events, acc, llm


async def test_grounded_checks_claims_and_sends_sources_with_first_token() -> None:
    llm = FakeLLM(stream=["Home collection costs Rs 150 in Pune [1]. ", "Reports in 2 days."])
    events, acc, _ = await _ground(llm)
    kinds = [type(e).__name__ for e in events]
    assert kinds.index("SourcesEvent") < kinds.index("TokenEvent")
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert "Rs 150" in text and "2 days" not in text and acc.removed == 1
    assert acc.route == Route.GROUNDED and llm.cost_ledger.summary().calls == 1
    sent = llm.calls[0][1]
    assert sent[0].role == "system" and sent[0].content != "old"  # compiled persona prompt
    assert sent[-1].content.startswith("CONTEXT")  # grounded user message last


async def test_grounded_stage_goes_through_the_clock_once() -> None:
    clock = StageClock()
    events, acc, _ = await _ground(FakeLLM(stream=["Collection is Rs 150."]), clock=clock)
    stages = [e for e in events if isinstance(e, StageEvent)]
    assert [s.stage for s in stages] == ["write"]
    assert clock.advance("write") is None  # already current
    assert "write" in acc.timings and "gate_threshold" not in acc.timings


async def test_reasoning_answer_model_is_not_token_capped() -> None:
    llm = FakeLLM(stream=["Collection is Rs 150."])  # a model not known to be plain
    await _ground(llm)
    assert "max_tokens" not in llm.calls[0][2]


async def test_known_plain_answer_model_gets_the_voice_cap() -> None:
    llm = FakeLLM(stream=["Collection is Rs 150."], non_reasoning=True)
    await _ground(llm)
    assert llm.calls[0][2]["max_tokens"] > 0


async def test_grounded_all_removed_becomes_no_answer_without_sources() -> None:
    llm = FakeLLM(stream=["Same-day reports cost ₹1,500."])
    events, acc, _ = await _ground(llm)
    assert acc.route == Route.NO_ANSWER
    assert not [e for e in events if isinstance(e, SourcesEvent | TokenEvent)]


async def test_leading_whitespace_then_all_removed_sends_no_sources() -> None:
    llm = FakeLLM(stream=["\n", "Same-day reports cost ₹1,500."])
    events, acc, _ = await _ground(llm)
    assert acc.route == Route.NO_ANSWER
    assert not [e for e in events if isinstance(e, SourcesEvent | TokenEvent)]


async def test_leading_whitespace_is_held_until_the_first_real_piece() -> None:
    llm = FakeLLM(stream=["\n", "Collection is Rs 150."])
    events, _, _ = await _ground(llm)
    kinds = [type(e).__name__ for e in events if not isinstance(e, StageEvent)]
    assert kinds[0] == "SourcesEvent"
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert text.startswith("\nCollection")


async def test_a_generation_failure_propagates_to_the_director() -> None:
    class Boom(FakeLLM):
        async def complete_stream(self, messages, **kw):  # type: ignore[no-untyped-def]
            raise RuntimeError("provider down")
            yield ""

    try:
        await _ground(Boom())
    except RuntimeError as exc:
        assert "provider down" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected the failure to propagate")


async def test_explainer_prefix_disclaimer_and_no_business_specifics() -> None:
    llm = FakeLLM(stream=["A lipid profile measures cholesterol. ", "Ours costs ₹499."])
    deps, _ = _deps(llm, _orch())
    cfg = make_config(
        scope={"sensitive_profile": "medical", "disclaimers": {"en": "This is not medical advice."}}
    )
    acc = Acc(query_id=uuid.uuid4())
    events = await _collect(
        explainer(
            deps,
            config=cfg,
            compile_turn=_turn(Route.EXPLAINER),
            question="what is a lipid profile",
            history=[],
            acc=acc,
            clock=StageClock(),
        )
    )
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert text.startswith("General information: ") and "₹499" not in text
    assert text.rstrip().endswith("This is not medical advice.")


async def test_clarify_question() -> None:
    q, resp = await clarify_question(
        FakeLLM(text="Which test are you asking about?"), make_config(), "how much", "en"
    )
    assert q == "Which test are you asking about?" and resp is not None
    q, resp = await clarify_question(FakeLLM(credentials=False), make_config(), "how much", "en")
    assert q is None and resp is None


async def test_clarify_keeps_reasoning_effort_and_caps_only_a_plain_model() -> None:
    reasoning = FakeLLM(text="Which one?\nSecond line ignored.")
    q, _ = await clarify_question(reasoning, make_config(), "how much", "en")
    kw = reasoning.calls[0][2]
    assert q == "Which one?" and kw["reasoning_effort"] == "low" and "max_tokens" not in kw
    plain = FakeLLM(text="Which one?", non_reasoning=True)
    await clarify_question(plain, make_config(), "how much", "en")
    assert plain.calls[0][2]["max_tokens"] == 60


async def test_a_probe_overrides_retrieval_settings_and_makes_no_llm_call() -> None:
    llm = FakeLLM(json_replies=[{"query": "never asked"}])
    orch = _orch()
    deps, _ = _deps(llm, orch)
    deps.retrieval_kwargs.update({"enable_hyde": True, "enable_classification": True, "top_k": 7})
    await _retrieve(
        deps,
        text="home collection kitne ka hai",
        info=HINGLISH,
        retrieval_overrides=_PROBE_OVERRIDES,
        translate=False,
    )
    kw = orch.run_retrieval_pipeline.await_args.kwargs
    assert kw["enable_hybrid"] is False
    assert kw["enable_reranking"] is False and kw["enable_mmr"] is False
    assert kw["enable_hyde"] is False and kw["enable_classification"] is False
    assert kw["top_k"] == 7 and kw["extra_queries"] is None
    assert llm.calls == [] and llm.cost_ledger.calls == []


async def test_a_refusal_followed_by_nearby_facts_is_still_no_answer() -> None:
    llm = FakeLLM(
        stream=[
            "I don't have that information in our documents. ",
            "Home collection costs Rs 150 in Pune [1].",
        ]
    )
    events, acc, _ = await _ground(llm)
    assert acc.route == Route.NO_ANSWER
    assert not [e for e in events if isinstance(e, SourcesEvent | TokenEvent)]


async def test_an_answer_that_merely_mentions_missing_details_still_answers() -> None:
    llm = FakeLLM(stream=["Home collection costs Rs 150 in Pune [1]. I don't have more details."])
    _, acc, _ = await _ground(llm)
    assert acc.route == Route.GROUNDED


def test_refusal_state_reads_the_opening_sentence() -> None:
    forms = answering.refusal_forms(make_config())
    ok = answering.refusal_state
    assert ok("I don't have that information in our documents.", forms) == "refusal"
    assert ok("I dont have that information in our documents. We ship in Pune.", forms) == "refusal"
    assert ok("We ship in Pune. I don't have that information in our documents.", forms) == "no"
    assert ok("I don't have that information in our documents but Rs 150 applies", forms) == "no"


def test_the_constraints_forbid_substituting_related_facts() -> None:
    from callobuzz_rag.conversation.compiler import compile_prompt

    turn = CompileTurn(
        label=TurnLabel.BUSINESS_QUESTION,
        route=Route.GROUNDED,
        language="en",
        script=Script.LATIN,
        office=OfficeState.UNKNOWN,
    )
    constraints = compile_prompt(make_config(), turn).constraints
    assert "and nothing else" in constraints and "related" in constraints


def test_a_short_no_answer_message_does_not_turn_a_real_answer_into_a_refusal() -> None:
    forms = answering.refusal_forms(
        make_config(scope={"fallback": {"no_answer_message": {"en": "Sorry!"}}})
    )
    ok = answering.refusal_state
    assert ok("Sorry! We are closed on Sundays [1].", forms) == "no"
    assert ok("Sorry!", forms) == "refusal"


def test_a_refusal_then_a_continuation_is_judged_by_whether_it_addresses_the_question() -> None:
    forms = answering.refusal_forms(make_config())
    ok = answering.refusal_state
    msg = "I don't have that information in our documents."
    # Related but unasked facts: still the refusal.
    assert ok(f"{msg} Home collection costs Rs 150 [1].", forms, "price?") == "refusal"
    assert ok(f"{msg} We open at 9 am [1].", forms, "do you ship to Delhi?") == "refusal"
    # The continuation answers (part of) the question: the reply keeps its sources.
    q = "home collection price and the weather?"
    assert ok(f"{msg} Home collection costs Rs 150 [1].", forms, q) == "no"
    assert (
        ok(f"{msg}\nHome collection price is Rs 150 [1].", forms, "home collection price?") == "no"
    )
    # One shared word out of many question words is not enough.
    assert (
        ok(f"{msg} Home visits are free [1].", forms, "home collection price weather") == "refusal"
    )
    # No question known: the continuation never rescues the refusal.
    assert ok(f"{msg} Home collection costs Rs 150 [1].", forms) == "refusal"
    # The bare message is a refusal whatever the question.
    assert ok(msg, forms, "home collection price?") == "refusal"


async def test_a_length_stop_drops_the_cut_off_row_and_is_marked() -> None:
    from callobuzz_rag.llm.stream_stats import current_stream_stats

    class CutOff(FakeLLM):
        async def complete_stream(self, messages, **kw):  # type: ignore[no-untyped-def]
            async for delta in super().complete_stream(messages, **kw):
                yield delta
            stats = current_stream_stats()
            assert stats is not None
            stats.finish_reason = "length"

    llm = CutOff(
        stream=[
            "Charges [1]:\n\n| Service | Price |\n|---|---|\n",
            "| Home collection | Rs 150 |\n",
            "| Home collection in Pune | Rs 1",
        ]
    )
    events, acc, _ = await _ground(llm)
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert acc.truncated and acc.route == Route.GROUNDED
    assert text.rstrip().endswith("| Home collection | Rs 150 |")
    assert "in Pune" not in text and acc.removed == 0


async def test_a_normal_stop_is_not_marked_truncated() -> None:
    _, acc, _ = await _ground(FakeLLM(stream=["Collection is Rs 150."]))
    assert not acc.truncated


def test_the_disclosure_line_before_a_refusal_is_skipped() -> None:
    config = make_config()
    forms, intros = answering.refusal_forms(config), answering.intro_forms(config)
    ok = answering.refusal_state
    intro = "I'm Asha, the AI assistant of Shree Labs."
    refusal = "I don't have that information in our documents."
    assert ok(f"{intro}\n\n{refusal}", forms, "emi?", intros) == "refusal"
    assert ok(f"Hello! {intro} {refusal}", forms, "emi?", intros) == "refusal"
    assert ok(intro, forms, "emi?", intros) == "maybe"  # it may still refuse
    assert ok(f"{intro} Home collection costs Rs 150 [1].", forms, "price?", intros) == "no"
    hinglish = (
        "Main Asha hoon, Shree Labs ki AI assistant. Yeh jaankari hamare documents mein nahi hai."
    )
    assert ok(hinglish, forms, "EMI?", intros) == "refusal"


async def test_intro_then_refusal_is_the_no_answer_route_without_sources() -> None:
    intro = "I'm Asha, the AI assistant of Shree Labs.\n\n"
    llm = FakeLLM(stream=[intro, "I don't have that information ", "in our documents."])
    events, acc, _ = await _ground(llm)
    assert acc.route == Route.NO_ANSWER
    assert not [e for e in events if isinstance(e, SourcesEvent | TokenEvent)]


async def test_intro_alone_is_no_answer_and_intro_with_an_answer_keeps_both() -> None:
    _, alone, _ = await _ground(FakeLLM(stream=["I'm Asha, the AI assistant of Shree Labs."]))
    assert alone.route == Route.NO_ANSWER
    events, acc, _ = await _ground(
        FakeLLM(stream=["I'm Asha, the AI assistant of Shree Labs. ", "Collection is Rs 150 [1]."])
    )
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert acc.route == Route.GROUNDED and acc.removed == 0
    assert text.startswith("I'm Asha, the AI assistant of Shree Labs.") and "Rs 150" in text
