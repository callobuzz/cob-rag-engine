"""Greetings and very short queries skip query understanding (rewrites)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.retrieval.query_heuristics import (
    is_short_query,
    is_small_talk,
    skips_query_understanding,
)
from tests.unit.test_llm_provider_routing import _make_orchestrator


@pytest.mark.parametrize(
    "query",
    [
        "Hi",
        "hello",
        "Hello!",
        "hey there",
        "Hiii",
        "namaste",
        "नमस्ते",
        "good morning",
        "Good morning everyone, how are you doing today?",
        "thanks",
        "Thank you so much!",
        "ok",
        "okay thanks",
        "bye",
        "see you later",
        "how are you",
        "How are you doing today",
        "what's up",
        "who are you",
        "are you a bot?",
        "can you help me",
        "hi 👋",
    ],
)
def test_small_talk_skips(query: str) -> None:
    assert skips_query_understanding(query) is True


@pytest.mark.parametrize(
    "query",
    ["refund policy", "box sizes", "GST rate?", "BU-4471 price", "a b c", "  two words  "],
)
def test_very_short_queries_skip(query: str) -> None:
    assert is_short_query(query)
    assert skips_query_understanding(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "what is the refund policy for international orders",
        "hello, what is the refund policy for international orders",
        "Hi, my order is late. What can I do?",
        "How do I reset my password on the dashboard",
        "thanks, but how do I export my invoices to Tally",
        "can you help me find the cancellation charges for Pune",
        "who are you going to assign my ticket to",
        "compare the Standard plan with the Premium plan",
    ],
)
def test_real_questions_do_not_skip(query: str) -> None:
    assert skips_query_understanding(query) is False


def test_four_words_is_not_short() -> None:
    assert not is_short_query("what are the charges")


def test_a_long_unspaced_string_is_not_short() -> None:
    assert not is_short_query("refund-policy-for-international-orders-please")


def test_a_cjk_sentence_without_spaces_is_judged_by_length() -> None:
    assert not is_short_query(
        "请问国际订单的退款政策是什么我想了解详细的退货流程以及费用还有到账时间是多久"
    )


def test_small_talk_must_be_the_whole_message() -> None:
    assert is_small_talk("good morning")
    assert not is_small_talk("good morning, where is my order 4471 right now")


def test_empty_is_short() -> None:
    assert skips_query_understanding("") is True


# ----------------------------------------------------------- in the pipeline


@pytest.mark.asyncio
async def test_a_greeting_makes_no_llm_call_and_still_searches() -> None:
    orchestrator, llm = _make_orchestrator(has_credentials=True)
    llm.complete_json = AsyncMock(return_value=MagicMock(content='{"complexity": "simple"}'))

    result = await orchestrator.run_retrieval_pipeline(text="Hi", corpus_id=uuid.uuid4())

    llm.complete_json.assert_not_called()
    assert result.plan.variants == []
    assert result.processed_query == "Hi"


@pytest.mark.asyncio
async def test_a_real_question_still_runs_query_understanding() -> None:
    orchestrator, llm = _make_orchestrator(has_credentials=True)
    llm.complete_json = AsyncMock(
        return_value=MagicMock(content='{"complexity": "simple", "variants": ["x"]}')
    )

    await orchestrator.run_retrieval_pipeline(
        text="what is the refund policy for international orders", corpus_id=uuid.uuid4()
    )

    assert llm.complete_json.await_count > 0


@pytest.mark.asyncio
async def test_a_short_query_is_still_embedded_and_searched() -> None:
    orchestrator, _ = _make_orchestrator(has_credentials=True)

    await orchestrator.run_retrieval_pipeline(text="refund policy", corpus_id=uuid.uuid4())

    orchestrator._embedder.embed_texts.assert_awaited_once()
    assert orchestrator._embedder.embed_texts.await_args.args[0] == ["refund policy"]


@pytest.mark.asyncio
async def test_an_explicit_request_for_query_understanding_beats_the_short_query_skip() -> None:
    orchestrator, llm = _make_orchestrator(has_credentials=True)
    llm.complete_json = AsyncMock(
        return_value=MagicMock(content='{"complexity": "simple", "variants": ["x"]}')
    )

    await orchestrator.run_retrieval_pipeline(
        text="refund policy", corpus_id=uuid.uuid4(), enable_classification=True
    )

    assert llm.complete_json.await_count > 0


@pytest.mark.asyncio
async def test_an_explicit_hyde_request_on_a_short_query_still_builds_the_plan() -> None:
    orchestrator, llm = _make_orchestrator(has_credentials=True)
    llm.complete_json = AsyncMock(return_value=MagicMock(content='{"complexity": "simple"}'))

    await orchestrator.run_retrieval_pipeline(
        text="refund policy", corpus_id=uuid.uuid4(), enable_hyde=True
    )

    assert llm.complete_json.await_count > 0
