"""Markdown to WhatsApp: markup, limits, buttons, the handoff card (TV6)."""

from types import SimpleNamespace

import pytest

from callobuzz_rag.api.schemas.assistant import (
    Cta,
    HandoffCardBlock,
    HandoffChannelOut,
    LeadFormBlock,
)
from callobuzz_rag.channels.whatsapp.render import (
    TEXT_LIMIT,
    quick_reply_message,
    render_done,
    split_messages,
    to_whatsapp,
)
from callobuzz_rag.conversation.schema import QuickReply


def done(text: str = "", **kw):
    base = {"text": text, "language": "en", "ui": None, "cta": None, "quick_replies": []}
    return SimpleNamespace(**{**base, **kw})


# --------------------------------------------------------------- markup


def test_bold_and_citations() -> None:
    assert to_whatsapp("**Root canal**: Rs 6,000 [1]") == "*Root canal*: Rs 6,000"
    assert to_whatsapp("It costs **Rs 600** [1][2].") == "It costs *Rs 600*."


def test_headings_become_bold_lines() -> None:
    assert to_whatsapp("## Prices\nRoot canal: Rs 6,000\n\n### Hours\n9 to 6") == (
        "*Prices*\nRoot canal: Rs 6,000\n\n*Hours*\n9 to 6"
    )


def test_links_show_their_address() -> None:
    assert to_whatsapp("[Book](https://shreedental.in/book)") == "Book: https://shreedental.in/book"
    assert to_whatsapp("See [https://a.in](https://a.in)") == "See https://a.in"


def test_lists_use_bullets_and_numbers_stay() -> None:
    assert to_whatsapp("- one\n- two\n* three") == "• one\n• two\n• three"
    assert to_whatsapp("1. first\n2. second") == "1. first\n2. second"


def test_single_star_italics_become_underscores_and_stars_in_prose_survive() -> None:
    assert to_whatsapp("this is *important* really") == "this is _important_ really"
    assert to_whatsapp("2 * 3 = 6") == "2 * 3 = 6"


def test_a_rule_line_and_extra_blank_lines_go() -> None:
    assert to_whatsapp("a\n\n\n\n---\n\nb") == "a\n\nb"


def test_crlf_and_empty_input() -> None:
    assert to_whatsapp("a\r\nb") == "a\nb"
    assert to_whatsapp("") == ""


# --------------------------------------------------------------- splitting


def test_short_text_is_one_message() -> None:
    assert split_messages("hello") == ["hello"]
    assert split_messages("   ") == []


def test_long_text_splits_on_paragraph_boundaries() -> None:
    para = "word " * 300  # 1,500 characters
    text = "\n\n".join([para.strip()] * 6)  # ~9,000 characters
    parts = split_messages(text)
    assert 2 <= len(parts) <= 3
    assert all(len(p) <= TEXT_LIMIT for p in parts)
    assert parts[0].count("\n\n") >= 1  # whole paragraphs, not cut mid-way
    assert not parts[0].endswith("wor")


def test_more_than_three_messages_is_trimmed_with_an_ellipsis() -> None:
    text = "\n\n".join(["x" * 3000] * 8)
    parts = split_messages(text)
    assert len(parts) == 3 and parts[-1].endswith("…")
    assert all(len(p) <= TEXT_LIMIT for p in parts)


def test_a_single_huge_paragraph_is_cut_at_spaces() -> None:
    parts = split_messages("lorem ipsum " * 1000)
    assert len(parts) >= 2 and all(len(p) <= TEXT_LIMIT for p in parts)
    assert all(not p.startswith(" ") for p in parts)


# --------------------------------------------------------------- the reply


def test_a_plain_answer() -> None:
    out = render_done(done("**Yes**, we are open [1]."), business_name="Shree Dental")
    assert out.texts == ["*Yes*, we are open."] and out.buttons == []


def test_quick_replies_up_to_three_short_ones_become_buttons() -> None:
    replies = [
        QuickReply(label="Prices", message="What are your prices?"),
        QuickReply(label="Hours", message="When are you open?"),
    ]
    out = render_done(done("We treat teeth.", quick_replies=replies), business_name="B")
    assert out.texts == [] and out.button_body == "We treat teeth."
    assert [t for _i, t in out.buttons] == ["Prices", "Hours"]
    assert quick_reply_message(out.buttons[0][0]) == "What are your prices?"
    assert quick_reply_message("optin_yes") is None


def test_more_than_three_or_a_long_title_becomes_a_numbered_list() -> None:
    four = [QuickReply(label=f"Option {i}", message=f"m{i}") for i in range(4)]
    out = render_done(done("Pick.", quick_replies=four), business_name="B")
    assert out.buttons == [] and "1. Option 0" in out.texts[-1] and "4. Option 3" in out.texts[-1]
    long_title = [QuickReply(label="x" * 20, message="a"), QuickReply(label="y" * 20, message="b")]
    assert render_done(done("Pick.", quick_replies=long_title), business_name="B").buttons != []
    too_long = [QuickReply(label="Short", message="a")]
    object.__setattr__(too_long[0], "label", "z" * 21)  # a label the schema would refuse
    assert render_done(done("Pick.", quick_replies=too_long), business_name="B").buttons == []


def test_a_long_answer_keeps_buttons_with_a_prompt() -> None:
    replies = [QuickReply(label="Prices", message="p")]
    out = render_done(done("a" * 2000, quick_replies=replies), business_name="B")
    assert out.button_body == "Choose one:" and len(out.texts) == 1


def test_a_handoff_card_has_the_person_line_the_reference_and_no_whatsapp_link() -> None:
    card = HandoffCardBlock(
        open_now=True,
        message="Our team is happy to help.",
        channels=[
            HandoffChannelOut(
                kind="whatsapp", label="WhatsApp us", url="https://wa.me/919800000000"
            ),
            HandoffChannelOut(kind="call", label="Call us", url="tel:+919822012345"),
            HandoffChannelOut(kind="email", label="E-mail", url="mailto:hello@shreedental.in"),
            HandoffChannelOut(kind="booking", label="Book", url="https://shreedental.in/book"),
            HandoffChannelOut(kind="callback", label="Call me back", url=None),
        ],
        reason="explicit_request",
        ref_code="SD-4F7K",
    )
    out = render_done(done("", ui=card), business_name="Shree Dental")
    text = "\n".join(out.texts)
    assert "A person from Shree Dental will reply here." in text
    assert "Your reference is SD-4F7K." in text
    assert "Call: +91 98220 12345" in text
    assert "E-mail: hello@shreedental.in" in text
    assert "Book: https://shreedental.in/book" in text
    assert "wa.me" not in text and "WhatsApp us" not in text and "back" not in text


def test_the_reference_is_not_said_twice() -> None:
    card = HandoffCardBlock(
        open_now=True,
        message="Please call us.\n\nYour reference: **SD-4F7K** — quote it if you call.",
        channels=[],
        reason="explicit_request",
        ref_code="SD-4F7K",
    )
    text = "\n".join(render_done(done("", ui=card), business_name="B").texts)
    assert text.count("SD-4F7K") == 1


def test_a_lead_form_is_never_rendered() -> None:
    form = LeadFormBlock(
        form_id="lf_0123456789abcdef",
        title="Leave your number",
        notice="n",
        consent_label="c",
        fields=[],
        submit_label="Send",
        trigger="asked",
    )
    out = render_done(done("Here is the answer.", ui=form), business_name="B")
    assert out.texts == ["Here is the answer."]


def test_a_cta_link_is_a_line() -> None:
    cta = Cta(type="booking", label="Book a visit", url="https://shreedental.in/book")
    out = render_done(done("See you.", cta=cta), business_name="B")
    assert out.texts == ["See you.\n\nBook a visit: https://shreedental.in/book"]


@pytest.mark.parametrize("lang", ["hi", "hi-Latn"])
def test_the_person_line_follows_the_language(lang: str) -> None:
    card = HandoffCardBlock(open_now=True, message="x", channels=[], reason="explicit_request")
    text = render_done(done("", ui=card, language=lang), business_name="Shree").texts[0]
    assert "Shree" in text and "person from" not in text


def test_an_empty_turn_is_empty() -> None:
    assert render_done(done(""), business_name="B").empty
