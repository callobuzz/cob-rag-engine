"""C1: rules first, then a cached fast-model label with a strict JSON schema."""

import uuid

from callobuzz_rag.conversation.labeler import Labeler, label_cache_key
from callobuzz_rag.conversation.types import TurnLabel
from callobuzz_rag.llm.models import LLMMessage
from tests.unit.conversation_helpers import FakeCache, FakeLLM, make_config

CORPUS = uuid.uuid4()


def _labeler(llm: FakeLLM, cache: FakeCache | None = None, timeout_s: float = 1.5) -> Labeler:
    return Labeler(llm=llm, cache=cache, timeout_s=timeout_s, ttl_s=3600)


async def test_rules_decide_without_a_model_call() -> None:
    llm = FakeLLM()
    result = _labeler(llm).by_rules("namaste")
    assert result is not None and result.label == TurnLabel.SMALLTALK
    assert result.source == "rules" and result.intent == "greeting"
    assert llm.calls == []


async def test_model_label_parsed_and_cached() -> None:
    llm = FakeLLM(
        json_replies=[
            {
                "label": "domain_explainer",
                "confidence": 0.82,
                "language": "en",
                "topic": "tests",
                "frustrated": False,
                "needs_human": False,
            }
        ]
    )
    cache = FakeCache()
    lab = _labeler(llm, cache)
    first = await lab.by_model(
        "What does a thyroid test measure?",
        config=make_config(),
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    assert first.label == TurnLabel.DOMAIN_EXPLAINER and first.source == "model"
    assert first.topic_id == "tests" and first.confidence == 0.82
    second = await lab.by_model(
        "what does a thyroid test measure",
        config=make_config(),
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    assert second.source == "cache" and len(llm.calls) == 1
    assert cache.ttls[label_cache_key(CORPUS, "3", "What does a thyroid test measure?")] == 3600


async def test_prompt_uses_fast_options_topics_and_sanitised_text() -> None:
    llm = FakeLLM(
        json_replies=[
            {
                "label": "business_question",
                "confidence": 0.9,
                "language": "hi-Latn",
                "topic": None,
                "frustrated": False,
                "needs_human": False,
            }
        ]
    )
    await _labeler(llm).by_model(
        "home collection kitne ka hai‮",
        config=make_config(),
        corpus_id=CORPUS,
        scope="3",
        history=[],
    )
    kind, messages, kw = llm.calls[0]
    assert kind == "json" and "timeout" in kw
    prompt = messages[-1].content
    assert "Home collection" in prompt and "Shree Labs" in prompt
    assert "‮" not in prompt


async def test_unknown_topic_is_dropped() -> None:
    llm = FakeLLM(
        json_replies=[
            {
                "label": "business_question",
                "confidence": 0.9,
                "language": "en",
                "topic": "made_up",
                "frustrated": False,
                "needs_human": False,
            }
        ]
    )
    out = await _labeler(llm).by_model(
        "price?", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    assert out.topic_id is None


async def test_invalid_json_falls_back_to_business_question() -> None:
    llm = FakeLLM(json_replies=["not json", ["a list"], {"label": "weather"}])
    lab = _labeler(llm)
    for _ in range(3):
        out = await lab.by_model(
            "something", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
        )
        assert out.label == TurnLabel.BUSINESS_QUESTION
        assert out.source == "fallback" and out.confidence == 0.0


async def test_timeout_falls_back_fast() -> None:
    llm = FakeLLM(json_replies=[{"label": "off_topic", "confidence": 1}], delay_s=0.5)
    out = await _labeler(llm, timeout_s=0.05).by_model(
        "who won the match", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    assert out.label == TurnLabel.BUSINESS_QUESTION and out.source == "fallback"


async def test_no_credentials_means_no_call() -> None:
    llm = FakeLLM(credentials=False)
    out = await _labeler(llm).by_model(
        "anything", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    assert out.source == "fallback" and llm.calls == []


async def test_short_follow_ups_are_not_cached() -> None:
    reply = {
        "label": "business_question",
        "confidence": 0.9,
        "language": "en",
        "topic": None,
        "frustrated": False,
        "needs_human": False,
    }
    llm = FakeLLM(json_replies=[reply, reply])
    cache = FakeCache()
    lab = _labeler(llm, cache)
    history = [LLMMessage("user", "home collection price?"), LLMMessage("assistant", "₹150.")]
    await lab.by_model(
        "and on Sunday?", config=make_config(), corpus_id=CORPUS, scope="3", history=history
    )
    assert cache.data == {}
    assert "home collection price?" in llm.calls[0][1][-1].content


async def test_reasoning_fast_model_keeps_effort_and_gets_no_token_cap() -> None:
    reply = {
        "label": "business_question",
        "confidence": 0.9,
        "language": "en",
        "topic": None,
        "frustrated": False,
        "needs_human": False,
    }
    llm = FakeLLM(json_replies=[reply])
    await _labeler(llm, timeout_s=1.5).by_model(
        "price?", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    kw = llm.calls[0][2]
    assert kw["reasoning_effort"] == "low" and "max_tokens" not in kw
    assert kw["timeout"] == 1.5


async def test_cost_is_on_the_providers_ledger() -> None:
    reply = {
        "label": "business_question",
        "confidence": 0.9,
        "language": "en",
        "topic": None,
        "frustrated": False,
        "needs_human": False,
    }
    llm = FakeLLM(json_replies=[reply])
    await _labeler(llm).by_model(
        "price?", config=make_config(), corpus_id=CORPUS, scope="3", history=[]
    )
    assert llm.cost_ledger.summary().calls == 1


def test_label_cache_key_is_versioned_with_the_prompt() -> None:
    import uuid

    from callobuzz_rag.conversation.labeler import label_cache_key

    key = label_cache_key(uuid.UUID(int=1), "1", "Hello")
    assert key.startswith("alabel:v3:")


def test_the_prompt_separates_buying_from_asking_and_acknowledging_from_small_talk() -> None:
    from callobuzz_rag.conversation.labeler import LABEL_PROMPT

    assert "asked only to know is business_question" in LABEL_PROMPT
    assert "never smalltalk" in LABEL_PROMPT


def test_the_static_instructions_come_first_so_providers_can_cache_them() -> None:
    """Automatic prompt caching (OpenAI, Gemini, DeepSeek) reuses only an identical prefix of
    1,024+ tokens: the fixed instructions lead, then the business, then the conversation and
    the message, which change on every turn."""
    import tiktoken

    shop = make_config()
    other = make_config(identity={"business_name": "Vastra Silk House"})
    history = [LLMMessage(role="user", content="do you collect at home?")]
    a = Labeler._prompt("how much is a thyroid test?", shop, [])
    b = Labeler._prompt("kal subah aa sakte ho?", shop, history)
    c = Labeler._prompt("how much is a thyroid test?", other, [])

    def shared(x: str, y: str) -> str:
        n = 0
        while n < min(len(x), len(y)) and x[n] == y[n]:
            n += 1
        return x[:n]

    instructions = shared(a, c)
    assert "Labels:" in instructions and "Respond with ONLY this JSON" in instructions
    assert "Shree Labs" not in instructions and "thyroid" not in instructions
    same_business = shared(a, b)
    assert "Shree Labs" in same_business and "Home collection" in same_business
    assert same_business.rstrip().endswith("Recent conversation (oldest first, may be empty):")
    assert a.index("<<<how much is a thyroid test?>>>") > a.index("Recent conversation")
    assert a.rstrip().endswith("Respond with the JSON only.")
    tokens = len(tiktoken.get_encoding("o200k_base").encode(same_business))
    assert tokens >= 1024, tokens
