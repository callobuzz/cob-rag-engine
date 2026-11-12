from callobuzz_rag.conversation.smalltalk import (
    abuse_warning,
    contact_line,
    decline_reply,
    instant_reply,
    localized,
    model_smalltalk,
    stopped_reply,
    wa_link,
)
from tests.unit.conversation_helpers import FakeLLM, make_config


def test_localized_falls_back_to_english() -> None:
    assert localized({"en": "Hello", "hi": "नमस्ते"}, "hi") == "नमस्ते"
    assert localized({"en": "Hello"}, "ta") == "Hello"
    assert localized(None, "en", "x") == "x"


def test_greeting_uses_the_configured_greeting() -> None:
    cfg = make_config(
        voice={
            "greeting": {
                "en": "Hi! I'm Asha from Shree Labs.",
                "hi-Latn": "Namaste! Main Asha hoon.",
            }
        }
    )
    assert instant_reply("greeting", cfg, "en") == "Hi! I'm Asha from Shree Labs."
    assert instant_reply("greeting", cfg, "hi-Latn") == "Namaste! Main Asha hoon."


def test_are_you_a_bot_always_discloses() -> None:
    cfg = make_config(identity={"ai_disclosure": False})
    reply = instant_reply("are_you_bot", cfg, "en")
    assert "Asha" in reply and "Shree Labs" in reply and "AI" in reply
    assert "Tests and preparation" in reply  # what it helps with


def test_thanks_and_bye_use_closing_line() -> None:
    cfg = make_config(voice={"closing_line": {"en": "Take care!"}})
    assert instant_reply("thanks", cfg, "en") == "Take care!"
    assert instant_reply("bye", cfg, "en") == "Take care!"


def test_how_are_you_then_what_it_helps_with() -> None:
    reply = instant_reply("how_are_you", make_config(), "hi-Latn")
    assert reply.startswith("Main theek hoon") and "Home collection" in reply


def test_decline_pivots_to_topics() -> None:
    text = decline_reply(make_config(), "en")
    assert "Tests and preparation" in text and "Home collection" in text


def test_stopped_and_abuse_lines() -> None:
    assert "+919822012345" in stopped_reply(make_config(), "en")
    assert abuse_warning("hi") and abuse_warning("en") != abuse_warning("hi")


def test_contact_line_lists_card_contacts() -> None:
    line = contact_line(make_config(), "en")
    assert "help@shree-labs.in" in line and "wa.me/919822012345" in line


def test_wa_link_encodes() -> None:
    assert wa_link("+91 98220 12345", "Hi & bye #1") == (
        "https://wa.me/919822012345?text=Hi%20%26%20bye%20%231"
    )


async def test_model_smalltalk_is_short_and_steers_back() -> None:
    llm = FakeLLM(
        text="Ha, I wish I could watch cricket! I can help with tests and home collection."
    )
    text, resp = await model_smalltalk(llm, make_config(), "do you like cricket", "en", [])
    assert text.startswith("Ha") and resp is not None
    prompt = llm.calls[0][1][0].content
    assert "1-2 sentences" in prompt and "Tests and preparation" in prompt
    kw = llm.calls[0][2]
    assert kw["reasoning_effort"] == "low" and "max_tokens" not in kw  # reasoning model: no cap


async def test_non_reasoning_fast_model_is_capped_at_120_tokens() -> None:
    llm = FakeLLM(text="Ha!", non_reasoning=True)
    await model_smalltalk(llm, make_config(), "do you like cricket", "en", [])
    kw = llm.calls[0][2]
    assert kw["max_tokens"] == 120 and "reasoning_effort" not in kw


async def test_long_model_reply_is_cut_to_two_sentences() -> None:
    llm = FakeLLM(text="One. Two! Three? Four.\nSecond paragraph.")
    text, _ = await model_smalltalk(llm, make_config(), "chat", "en", [])
    assert text == "One. Two!"


async def test_model_smalltalk_without_credentials_uses_greeting() -> None:
    text, resp = await model_smalltalk(FakeLLM(credentials=False), make_config(), "sup", "en", [])
    assert resp is None and text
