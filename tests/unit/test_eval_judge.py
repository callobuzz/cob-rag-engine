"""Owner criteria are judged with a score and a short reason; bad replies fail safe."""

import json
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.evaluation.judge import Criterion, explain_failure, judge_criteria
from callobuzz_rag.llm.models import LLMResponse


def provider(reply: str, *, non_reasoning: bool = True) -> AsyncMock:
    p = AsyncMock()
    p.complete_json = AsyncMock(return_value=LLMResponse(content=reply, model="judge"))
    p.complete = AsyncMock(return_value=LLMResponse(content=reply, model="judge"))
    p.is_known_non_reasoning = MagicMock(return_value=non_reasoning)
    p.fast_call_options = MagicMock(return_value={"model": None})
    return p


CRITERIA = [
    Criterion("mentions-home-fee", "Must mention the home-collection fee (₹150 within Pune)", 0.7)
]


async def test_scores_and_reasons() -> None:
    reply = json.dumps(
        {"criteria": [{"name": "mentions-home-fee", "score": 1, "reason": "States ₹150 for Pune."}]}
    )
    p = provider(reply)
    out = await judge_criteria(
        p, "Is home collection free?", "It costs ₹150 in Pune.", ["Home collection ₹150"], CRITERIA
    )
    assert out == {"mentions-home-fee": (1.0, "States ₹150 for Pune.")}
    prompt = p.complete_json.await_args.args[0][0].content
    assert "Must mention the home-collection fee" in prompt and "Is home collection free?" in prompt


async def test_unparseable_reply_gives_no_score() -> None:
    out = await judge_criteria(provider("not json"), "q", "a", [], CRITERIA)
    assert out == {"mentions-home-fee": (None, "The judge's reply could not be read.")}


async def test_scores_are_clamped() -> None:
    reply = json.dumps({"criteria": [{"name": "mentions-home-fee", "score": 7, "reason": "x"}]})
    out = await judge_criteria(provider(reply), "q", "a", [], CRITERIA)
    assert out["mentions-home-fee"][0] == 1.0


async def test_a_non_numeric_score_is_unscored() -> None:
    reply = json.dumps({"criteria": [{"name": "mentions-home-fee", "score": None, "reason": "x"}]})
    out = await judge_criteria(provider(reply), "q", "a", [], CRITERIA)
    assert out["mentions-home-fee"][0] is None


async def test_explain_failure_is_one_short_sentence() -> None:
    p = provider("The answer says collection is free, but the documents say ₹150.\nExtra line")
    reason = await explain_failure(
        p, "Is home collection free?", "Yes, free.", "₹150 within Pune", {"answer_correctness": 0.2}
    )
    assert reason == "The answer says collection is free, but the documents say ₹150."
    assert p.complete.await_args.kwargs["max_tokens"] == 80


async def test_a_reasoning_judge_gets_no_token_cap() -> None:
    p = provider("Wrong fee.", non_reasoning=False)
    assert await explain_failure(p, "q", "a", None, {"faithfulness": 0.1}) == "Wrong fee."
    assert "max_tokens" not in p.complete.await_args.kwargs


async def test_a_long_reply_is_truncated_in_code() -> None:
    p = provider("x" * 900, non_reasoning=False)
    assert len(await explain_failure(p, "q", "a", None, {"faithfulness": 0.1})) == 300
