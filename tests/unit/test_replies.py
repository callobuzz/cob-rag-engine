"""Quick replies and CTAs (conversation C6)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.conversation.replies import (
    MAX_CHIPS,
    ai_followups,
    chip_label,
    decline_pivot_chips,
    filter_followups,
    generate_followups,
    merge_quick_replies,
    topic_keyword_match,
    topic_quick_replies,
)
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.llm.models import LLMResponse
from tests.unit.replies_helpers import replies_config as _config


def _llm(payload: object, *, delay: float = 0.0) -> MagicMock:
    llm = MagicMock()
    llm.has_credentials = True
    llm.fast_call_options.return_value = {"model": "fast"}

    async def complete_json(messages, **kwargs):
        await asyncio.sleep(delay)
        return LLMResponse(content=json.dumps(payload), model="fast")

    llm.complete_json = AsyncMock(side_effect=complete_json)
    return llm


def test_chip_label_trims_and_cuts_on_a_word() -> None:
    assert chip_label("  Fasting   rules ") == "Fasting rules"
    cut = chip_label("When will my thyroid report be ready")
    assert len(cut) <= 20 and cut.endswith("…") and " " not in cut[-2:]


def test_topic_quick_replies_for_the_turns_topic() -> None:
    cfg = _config()
    assert [q.label for q in topic_quick_replies(cfg, "tests")] == ["Fasting rules", "Report time"]
    assert topic_quick_replies(cfg, "home") == []
    assert topic_quick_replies(cfg, None) == []
    assert topic_quick_replies(cfg, "unknown") == []


def test_decline_pivot_uses_topic_names_and_first_example() -> None:
    chips = decline_pivot_chips(_config())
    assert chips[0] == QuickReply(
        label="Blood tests", message="Do I need to fast for a lipid profile?"
    )
    assert chips[1] == QuickReply(label="Home collection", message="Tell me about Home collection")


def test_merge_dedupes_on_message_and_caps_at_four() -> None:
    a = [QuickReply(label="A", message="Which tests need fasting?")]
    b = [
        QuickReply(label="A2", message="which tests need FASTING? "),
        QuickReply(label="B", message="b?"),
        QuickReply(label="C", message="c?"),
        QuickReply(label="D", message="d?"),
        QuickReply(label="E", message="e?"),
    ]
    merged = merge_quick_replies(a, b)
    assert [q.label for q in merged] == ["A", "B", "C", "D"]
    assert len(merged) == MAX_CHIPS


def test_topic_keyword_match_uses_names_and_examples() -> None:
    cfg = _config()
    assert topic_keyword_match("Is home collection free?", cfg)
    assert topic_keyword_match("lipid profile fasting hours", cfg)
    assert not topic_keyword_match("Who won the cricket match?", cfg)


async def test_generate_followups_parses_three_questions() -> None:
    llm = _llm({"questions": ["Which tests need fasting?", "Can I drink water?", "Price?", "x"]})
    out = await generate_followups(
        llm,
        question="Fasting for lipid?",
        answer="Fast 10-12 hours.",
        sections=["Lipid profile"],
        topics=["Blood tests"],
        language="en",
    )
    assert out == ["Which tests need fasting?", "Can I drink water?", "Price?"]
    prompt = llm.complete_json.await_args.args[0][0].content
    assert "Blood tests" in prompt and "Lipid profile" in prompt


async def test_generate_followups_bad_json_is_empty() -> None:
    llm = _llm(["not", "an", "object"])
    out = await generate_followups(
        llm, question="q", answer="a", sections=[], topics=[], language="en"
    )
    assert out == []


async def test_filter_keeps_topic_matches_without_probing_and_probes_the_rest() -> None:
    probe = AsyncMock(side_effect=lambda q: q == "Do you test vitamin D?")
    kept = await filter_followups(
        ["Is home collection free?", "Do you test vitamin D?", "Who won the match?"],
        _config(),
        probe,
    )
    assert kept == ["Is home collection free?", "Do you test vitamin D?"]
    assert [c.args[0] for c in probe.await_args_list] == [
        "Do you test vitamin D?",
        "Who won the match?",
    ]


async def test_filter_probes_at_most_three() -> None:
    probe = AsyncMock(return_value=True)
    await filter_followups([f"zebra question {i}?" for i in range(6)], _config(), probe)
    assert probe.await_count == 3


async def test_filter_a_raising_probe_counts_as_failed() -> None:
    probe = AsyncMock(side_effect=RuntimeError("qdrant down"))
    assert await filter_followups(["zebra question?"], _config(), probe) == []


async def test_ai_followups_respects_the_budget() -> None:
    llm = _llm({"questions": ["Is home collection free?"]}, delay=2.0)
    out = await ai_followups(
        llm,
        None,
        config=_config(),
        question="q",
        answer="a",
        sections=[],
        language="en",
        budget_s=0.05,
    )
    assert [q.label for q in out] == ["Blood tests", "Home collection"]  # topic fallback


async def test_ai_followups_off_or_no_credentials() -> None:
    cfg = _config()
    cfg.conversation.ai_followups = False
    off = await ai_followups(
        _llm({"questions": ["Is home collection free?"]}),
        None,
        config=cfg,
        question="q",
        answer="a",
        sections=[],
        language="en",
    )
    assert off == []
    llm = _llm({"questions": ["Is home collection free?"]})
    llm.has_credentials = False
    none = await ai_followups(
        llm, None, config=_config(), question="q", answer="a", sections=[], language="en"
    )
    assert none == []


async def test_ai_followups_returns_chips() -> None:
    llm = _llm({"questions": ["Is home collection free?"]})
    out = await ai_followups(
        llm, None, config=_config(), question="q", answer="a", sections=[], language="en"
    )
    assert [q.message for q in out] == ["Is home collection free?"]
    assert len(out[0].label) <= 20


async def test_a_slow_model_falls_back_to_topic_chips() -> None:
    llm = _llm({"questions": ["Is home collection free?"]}, delay=2.0)
    out = await ai_followups(
        llm,
        None,
        config=_config(),
        question="Do I need to fast?",
        answer="a",
        sections=[],
        language="en",
        topic_id="tests",
        budget_s=0.05,
    )
    # chips always appear; the topic just asked about is not offered back
    assert [q.label for q in out] == ["Home collection"]
    assert out[0].message == "Tell me about Home collection"


async def test_filtered_out_candidates_fall_back_to_topic_chips() -> None:
    llm = _llm({"questions": ["Who won the match?"]})
    probe = AsyncMock(return_value=False)
    out = await ai_followups(
        llm, probe, config=_config(), question="q", answer="a", sections=[], language="en"
    )
    assert out and all(topic_keyword_match(q.message, _config()) for q in out)


async def test_a_slow_probe_keeps_the_candidates_that_already_passed() -> None:
    llm = _llm({"questions": ["Is home collection free?", "Who won the match?"]})

    async def slow(_text: str) -> bool:
        await asyncio.sleep(2)
        return True

    out = await ai_followups(
        llm, slow, config=_config(), question="q", answer="a", sections=[], language="en",
        budget_s=0.2,
    )  # fmt: skip
    assert [q.message for q in out] == ["Is home collection free?"]


async def test_fallback_chips_respect_the_off_switch_and_missing_topics() -> None:
    cfg = _config()
    cfg.conversation.ai_followups = False
    off = await ai_followups(
        _llm({}, delay=2.0), None, config=cfg, question="q", answer="a", sections=[],
        language="en", budget_s=0.05,
    )  # fmt: skip
    assert off == []
    bare = _config()
    bare.scope.topics = []
    none = await ai_followups(
        _llm({}, delay=2.0), None, config=bare, question="q", answer="a", sections=[],
        language="en", budget_s=0.05,
    )  # fmt: skip
    assert none == []


def test_fallback_followups_speak_the_visitors_language() -> None:
    from callobuzz_rag.conversation.replies import fallback_followups

    cfg = _config()
    chips = fallback_followups(cfg, language="hi-Latn")
    assert any(c.message == "Home collection ke baare mein batayein" for c in chips)
    hindi = fallback_followups(cfg, language="hi")
    assert any(c.message == "Home collection के बारे में बताइए" for c in hindi)


def _bare_config():
    cfg = _config()
    cfg.scope.topics = []
    return cfg


_SOURCES = [
    {"file_name": "pricing-plans.pdf", "section": "Refund policy"},
    {"file_name": "pricing-plans.pdf", "section": "refund policy"},
    {"file_name": "faq.md", "section": "Opening hours and holidays in Pune"},
    {"file_name": "shipping_rules.docx", "section": ""},
]


def test_source_followups_one_chip_per_distinct_section_or_title() -> None:
    from callobuzz_rag.conversation.replies import source_followups

    chips = source_followups(_SOURCES, question="What is the price?")
    assert [c.message for c in chips] == [
        "Tell me about Refund policy",
        "Tell me about Opening hours and holidays in Pune",
        "Tell me about shipping rules",
    ]
    assert all(len(c.label) <= 20 for c in chips)
    assert chips[0].label == "Refund policy"


def test_source_followups_skip_what_was_just_asked_and_speak_the_language() -> None:
    from callobuzz_rag.conversation.replies import source_followups

    chips = source_followups(_SOURCES, question="Tell me the refund policy", language="hi-Latn")
    assert "Refund policy ke baare mein batayein" not in [c.message for c in chips]
    assert chips[0].message == "Opening hours and holidays in Pune ke baare mein batayein"
    assert source_followups([], question="q") == []
    assert source_followups([{"section": "  "}], question="q") == []


def test_source_followups_skip_a_heading_whose_main_noun_was_just_asked() -> None:
    from callobuzz_rag.conversation.replies import source_followups

    sources = [
        {"section": "Shipping"},
        {"section": "Returns and exchanges"},
        {"section": "Opening hours and holidays in Pune"},
        {"section": "Payment methods"},
    ]
    # Synonym: delivery and shipping are the same thing to a visitor.
    asked = [c.message for c in source_followups(sources, question="How long does delivery take?")]
    assert "Tell me about Shipping" not in asked
    assert "Tell me about Payment methods" in asked
    # Stem: "returned" is the heading's "returns".
    asked = [c.message for c in source_followups(sources, question="Can sarees be returned?")]
    assert "Tell me about Returns and exchanges" not in asked
    assert "Tell me about Shipping" in asked
    # One shared minor word of a long heading does not hide it.
    asked = [c.message for c in source_followups(sources, question="Do you have a branch in Pune?")]
    assert "Tell me about Opening hours and holidays in Pune" in asked
    # Hinglish question, English heading.
    asked = [c.message for c in source_followups(sources, question="delivery kitne din mein hogi?")]
    assert "Tell me about Shipping" not in asked


async def test_a_slow_model_with_no_topics_builds_chips_from_the_sources() -> None:
    llm = _llm({"questions": ["x"]}, delay=2.0)
    out = await ai_followups(
        llm, None, config=_bare_config(), question="price?", answer="a",
        sections=[], language="en", budget_s=0.05, sources=_SOURCES,
    )  # fmt: skip
    assert out[0].message == "Tell me about Refund policy"
    assert len(out) == 3


async def test_source_chips_respect_the_off_switch_and_configured_topics_win() -> None:
    cfg = _bare_config()
    cfg.conversation.ai_followups = False
    off = await ai_followups(
        _llm({}, delay=2.0), None, config=cfg, question="q", answer="a", sections=[],
        language="en", budget_s=0.05, sources=_SOURCES,
    )  # fmt: skip
    assert off == []
    topical = await ai_followups(
        _llm({}, delay=2.0), None, config=_config(), question="q", answer="a", sections=[],
        language="en", budget_s=0.05, sources=_SOURCES,
    )  # fmt: skip
    assert topical[0].label == "Blood tests"
