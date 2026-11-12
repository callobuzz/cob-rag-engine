"""The ways to reach a person: links built from the business card, filtered by office hours.

WhatsApp is click-to-chat only (`https://wa.me/<digits>?text=...`, research 5.1): the number
in international format with digits only, the prefilled text percent-encoded. A channel with
no usable value (no override, nothing on the card, or a value that does not validate) is left
out rather than shown broken.
"""

import re
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import TypeAdapter, ValidationError

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.conversation.schema import (
    AssistantConfig,
    HandoffChannel,
    _https_or_http,
    localized,
    normalize_phone,
)
from callobuzz_rag.conversation.types import OfficeState

if TYPE_CHECKING:
    from callobuzz_rag.api.schemas.assistant import HandoffChannelOut

_PREFILL_MAX = 500
_EMAIL_SUBJECT = "Question from your website"
_EMAIL = TypeAdapter(ValidatedEmail)

CHANNEL_LABELS: dict[str, dict[str, str]] = {
    "whatsapp": {
        "en": "WhatsApp us",
        "hi": "\u0935\u094d\u0939\u093e\u091f\u094d\u0938\u0910\u092a \u0915\u0930\u0947\u0902",
        "hi-Latn": "WhatsApp karein",
    },
    "call": {
        "en": "Call us",
        "hi": "\u0915\u0949\u0932 \u0915\u0930\u0947\u0902",
        "hi-Latn": "Call karein",
    },
    "callback": {
        "en": "Call me back",
        "hi": "\u092e\u0941\u091d\u0947 \u0915\u0949\u0932 \u0915\u0930\u0947\u0902",
        "hi-Latn": "Mujhe call karein",
    },
    "email": {
        "en": "Email us",
        "hi": "\u0908\u092e\u0947\u0932 \u0915\u0930\u0947\u0902",
        "hi-Latn": "Email karein",
    },
    "contact_page": {
        "en": "Contact page",
        "hi": "\u0938\u0902\u092a\u0930\u094d\u0915 \u092a\u0947\u091c",
        "hi-Latn": "Contact page",
    },
    "booking": {
        "en": "Book a time",
        "hi": "\u0938\u092e\u092f \u092c\u0941\u0915 \u0915\u0930\u0947\u0902",
        "hi-Latn": "Time book karein",
    },
}


def _cut(text: str) -> str:
    """At most 500 characters, on a word boundary."""
    text = " ".join(text.split())
    if len(text) <= _PREFILL_MAX:
        return text
    cut = text[:_PREFILL_MAX]
    return cut[: cut.rfind(" ")].rstrip() if " " in cut else cut


def prefill_text(question: str, name: str | None) -> str:
    """`Hi, I'm {name}. {question}` (or `Hi! {question}`), at most 500 chars on a word boundary."""
    if name and name.strip():
        text = f"Hi, I'm {name.strip()}. {question.strip()}"
    else:
        text = f"Hi! {question.strip()}"
    return _cut(text)


def whatsapp_url(number_e164: str, text: str) -> str:
    digits = re.sub(r"\D", "", number_e164)
    return f"https://wa.me/{digits}?text={quote(text, safe='')}"


def tel_url(number_e164: str) -> str:
    return "tel:+" + re.sub(r"\D", "", number_e164)


def mailto_url(email: str, subject: str, body: str) -> str:
    return f"mailto:{email}?subject={quote(subject, safe='')}&body={quote(_cut(body), safe='')}"


def _phone(value: str | None) -> str | None:
    try:
        return normalize_phone(value)
    except ValueError:
        return None


def _address(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _EMAIL.validate_python(value)
    except ValidationError:
        return None


def _web(value: str | None) -> str | None:
    try:
        return _https_or_http(value)
    except ValueError:
        return None


def _available(available: str, office: OfficeState) -> bool:
    is_open = office is not OfficeState.CLOSED  # UNKNOWN counts as open
    if available == "open_hours":
        return is_open
    if available == "closed_hours":
        return not is_open
    return True


def channel_url(
    channel: HandoffChannel,
    config: AssistantConfig,
    *,
    question: str,
    visitor_name: str | None,
    prefill: str | None = None,  # TV2 reference: the WhatsApp text, replacing the default
) -> str | None:
    """The link of one channel, or None when it has no usable value (callback has no link:
    the widget opens the lead form)."""
    card = config.identity.card
    kind = channel.kind
    if kind == "whatsapp":
        number = _phone(channel.value or card.whatsapp)
        text = prefill if prefill else prefill_text(question, visitor_name)
        return whatsapp_url(number, text) if number else None
    if kind == "call":
        number = _phone(channel.value or card.phone)
        return tel_url(number) if number else None
    if kind == "email":
        address = _address(channel.value or card.email)
        return mailto_url(address, _EMAIL_SUBJECT, question) if address else None
    if kind == "contact_page":
        return _web(channel.value or card.website)
    if kind == "booking":
        return _web(channel.value or card.booking_url)
    return None


def build_channels(
    config: AssistantConfig,
    office: OfficeState,
    *,
    question: str,
    visitor_name: str | None,
    language: str,
    ignore_availability: bool = False,
    prefill: str | None = None,  # TV2 reference
) -> "list[HandoffChannelOut]":
    """The enabled, available channels in the configured order, each with its link.

    `ignore_availability` is for recording a click or callback: availability by office hours
    decides what is offered, not whether a tap is accepted a minute after closing."""
    # Deferred: `api.schemas.assistant` imports the conversation package while it loads.
    from callobuzz_rag.api.schemas.assistant import HandoffChannelOut

    out: list[HandoffChannelOut] = []
    for channel in config.handoff.channels:
        if not channel.enabled:
            continue
        if not ignore_availability and not _available(channel.available, office):
            continue
        label = (
            localized(channel.label, language)
            if channel.label
            else CHANNEL_LABELS[channel.kind].get(language) or CHANNEL_LABELS[channel.kind]["en"]
        ) or CHANNEL_LABELS[channel.kind]["en"]
        if channel.kind == "callback":
            # Opens the lead form; needs leads (contact data, consent, encryption).
            if config.leads.enabled:
                out.append(HandoffChannelOut(kind="callback", label=label, url=None))
            continue
        url = channel_url(
            channel, config, question=question, visitor_name=visitor_name, prefill=prefill
        )
        if url is None:
            continue
        out.append(HandoffChannelOut(kind=channel.kind, label=label, url=url))
    return out
