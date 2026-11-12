"""Handoff channel links (C5): wa.me, tel:, mailto:, pages, availability by office hours."""

from urllib.parse import parse_qs, urlsplit

import pytest

from callobuzz_rag.conversation.handoff.channels import (
    build_channels,
    mailto_url,
    prefill_text,
    tel_url,
    whatsapp_url,
)
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.types import OfficeState


def _config(channels: list[dict[str, object]] | None = None, leads: bool = True) -> AssistantConfig:
    return AssistantConfig.model_validate(
        {
            "identity": {
                "business_name": "Shree Labs",
                "card": {
                    "phone": "+919876543210",
                    "whatsapp": "+919812345678",
                    "email": "help@shree-labs.in",
                    "website": "https://shree-labs.in/contact",
                    "booking_url": "https://shree-labs.in/book",
                },
            },
            "leads": {"enabled": leads},
            "handoff": {
                "channels": channels
                if channels is not None
                else [
                    {"kind": "whatsapp"},
                    {"kind": "call", "available": "open_hours"},
                    {"kind": "callback", "available": "closed_hours"},
                    {"kind": "email"},
                    {"kind": "contact_page"},
                    {"kind": "booking"},
                ]
            },
        }
    )


def _kinds(cfg: AssistantConfig, office: OfficeState, language: str = "en") -> list[str]:
    out = build_channels(cfg, office, question="x", visitor_name=None, language=language)
    return [c.kind for c in out]


def test_whatsapp_url_digits_only_and_encoded() -> None:
    url = whatsapp_url("+91 98123-45678", "Hi, I'm Priya. Fasting & lipid profile? #1")
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "wa.me" and parts.path == "/919812345678"
    assert "%26" in parts.query and "%23" in parts.query and "%20" in parts.query
    assert parse_qs(parts.query)["text"] == ["Hi, I'm Priya. Fasting & lipid profile? #1"]


def test_prefill_text_with_and_without_name_and_truncated() -> None:
    assert prefill_text("Is home collection free?", "Priya") == (
        "Hi, I'm Priya. Is home collection free?"
    )
    assert prefill_text("Is home collection free?", None) == "Hi! Is home collection free?"
    long = prefill_text("word " * 200, None)
    assert len(long) <= 500 and not long.endswith(" ")


def test_tel_and_mailto() -> None:
    assert tel_url("+919876543210") == "tel:+919876543210"
    url = mailto_url(
        "help@shree-labs.in", "Question from your website", "Kya Sunday ko lab khula hai?"
    )
    assert url.startswith(
        "mailto:help@shree-labs.in?subject=Question%20from%20your%20website&body="
    )
    assert "Sunday" in url and " " not in url


def test_a_long_mail_body_is_cut() -> None:
    url = mailto_url("a@b.in", "s", "word " * 400)
    assert len(parse_qs(urlsplit(url).query)["body"][0]) <= 500


def test_open_hours_show_call_not_callback() -> None:
    out = build_channels(
        _config(), OfficeState.OPEN, question="Fees?", visitor_name=None, language="en"
    )
    assert [c.kind for c in out] == ["whatsapp", "call", "email", "contact_page", "booking"]
    assert out[1].url == "tel:+919876543210"
    assert out[0].label == "WhatsApp us"


def test_closed_hours_show_callback_not_call() -> None:
    out = build_channels(
        _config(), OfficeState.CLOSED, question="Fees?", visitor_name="Priya", language="hi"
    )
    kinds = [c.kind for c in out]
    assert "call" not in kinds and "callback" in kinds
    callback = next(c for c in out if c.kind == "callback")
    assert callback.url is None
    assert callback.label == "मुझे कॉल करें"


def test_unknown_office_counts_as_open() -> None:
    kinds = _kinds(_config(), OfficeState.UNKNOWN)
    assert "call" in kinds and "callback" not in kinds


def test_missing_values_and_disabled_channels_are_omitted() -> None:
    cfg = AssistantConfig.model_validate(
        {"handoff": {"channels": [{"kind": "whatsapp"}, {"kind": "email", "enabled": False}]}}
    )
    assert _kinds(cfg, OfficeState.OPEN) == []


def test_override_value_and_label() -> None:
    cfg = _config(
        [{"kind": "whatsapp", "value": "+917000000001", "label": {"en": "Chat with Rohan"}}]
    )
    (only,) = build_channels(cfg, OfficeState.OPEN, question="x", visitor_name=None, language="en")
    assert only.url is not None and only.url.startswith("https://wa.me/917000000001?text=")
    assert only.label == "Chat with Rohan"


def test_callback_needs_leads() -> None:
    assert _kinds(_config([{"kind": "callback"}], leads=False), OfficeState.CLOSED) == []


def test_hinglish_labels() -> None:
    (wa,) = build_channels(
        _config([{"kind": "whatsapp"}]),
        OfficeState.OPEN,
        question="x",
        visitor_name=None,
        language="hi-Latn",
    )
    assert wa.label == "WhatsApp karein"


@pytest.mark.parametrize(
    ("kind", "bad"),
    [
        ("whatsapp", "hello"),
        ("call", "12"),
        ("contact_page", "javascript:alert(1)"),
        ("booking", "ftp://x.in"),
        ("email", "not an email"),
    ],
)
def test_bad_override_values_omit_the_channel(kind: str, bad: str) -> None:
    cfg = AssistantConfig.model_validate({"handoff": {"channels": [{"kind": kind, "value": bad}]}})
    assert _kinds(cfg, OfficeState.OPEN) == []


def test_default_channels_are_pinned() -> None:
    cfg = AssistantConfig.model_validate(
        {
            "identity": {
                "card": {"phone": "+919876543210", "whatsapp": "+919812345678", "email": "a@b.in"}
            }
        }
    )
    assert _kinds(cfg, OfficeState.OPEN) == ["whatsapp", "call", "callback", "email"]
    assert _kinds(cfg, OfficeState.CLOSED) == ["whatsapp", "callback", "email"]


def test_ignore_availability_keeps_a_closed_call_channel() -> None:
    out = build_channels(
        _config(),
        OfficeState.CLOSED,
        question="x",
        visitor_name=None,
        language="en",
        ignore_availability=True,
    )
    kinds = [c.kind for c in out]
    assert "call" in kinds and "callback" in kinds
