"""The handoff card offered by the director (C1 offer registry).

`wanted` names the trigger that fired; `build` returns the card with the channels available
right now. The widget notifies the business when the visitor taps a channel; API surfaces,
which cannot report a tap, notify when an explicit request is offered (worker task).

A card is a Markdown message plus links. No model call is made here, so it costs nothing.
"""

import logging
from typing import TYPE_CHECKING, Any

from callobuzz_rag.conversation.handoff.channels import build_channels
from callobuzz_rag.conversation.handoff.office_hours import opens_again_line
from callobuzz_rag.conversation.handoff.triggers import (
    HandoffReason,
    forced_reason,
    handoff_reason,
)
from callobuzz_rag.conversation.offers import OfferContext, OfferKind, registry
from callobuzz_rag.conversation.schema import AssistantConfig, localized
from callobuzz_rag.conversation.smalltalk import contact_line
from callobuzz_rag.conversation.types import Channel, OfficeState

if TYPE_CHECKING:
    from callobuzz_rag.api.schemas.assistant import HandoffCardBlock, LeadFormBlock

logger = logging.getLogger(__name__)

# The windows a visitor can pick for a callback, in the business's time zone ("HH:MM-HH:MM").
CALLBACK_WINDOWS = ("09:00-12:00", "12:00-15:00", "15:00-18:00", "18:00-21:00")
_CALLBACK_TITLE = {
    "en": "Request a call back",
    "hi": "कॉल बैक का अनुरोध करें",
    "hi-Latn": "Call back ke liye request karein",
}
_CALLBACK_DAY = {
    "en": "Preferred day",
    "hi": "पसंदीदा दिन",
    "hi-Latn": "Pasandeeda din",
}
_CALLBACK_TIME = {
    "en": "Preferred time",
    "hi": "पसंदीदा समय",
    "hi-Latn": "Pasandeeda samay",
}


# TV1 human: said when a visitor asks for a person and the business card has nothing to offer.
NO_CHANNEL_TEXT = {
    "en": "I can't connect you from here, sorry. Please contact {business_name} directly.",
    "hi": "माफ़ कीजिए, मैं यहाँ से आपको किसी से नहीं जोड़ सकता। कृपया सीधे {business_name} से संपर्क करें।",
    "hi-Latn": "Maaf kijiye, main yahan se aapko kisi se nahi jod sakta. Kripya seedhe "
    "{business_name} se sampark karein.",
}
# Said instead of the owner's closed message when nothing can take a message (the contact line
# and the opening time follow): "leave a message" would have nowhere to go.
CLOSED_SHORT = {
    "en": "We're closed right now.",
    "hi": "हम अभी बंद हैं।",
    "hi-Latn": "Hum abhi band hain.",
}
# Reasons that must always produce a card, even with no channel (never a dead end).
_ALWAYS_A_PATH = {"explicit_request", "loop", "misses"}


def has_any_path(config: AssistantConfig, office: OfficeState, lang: str, question: str) -> bool:
    """True when something can be shown to a visitor who asks for a person: a channel, or the
    contact line made from the business card."""
    channels = build_channels(config, office, question=question, visitor_name=None, language=lang)
    # a callback needs the widget's form, which not every surface can show: it is no path here
    return any(c.kind != "callback" for c in channels) or bool(contact_line(config, lang))


def closed_opener(
    config: AssistantConfig, office: OfficeState, lang: str, question: str
) -> str | None:
    """The short closed line when it is closed and no channel is available now (the visitor gets
    the contact line instead), else None: the owner's own message stands."""
    if office is not OfficeState.CLOSED or not contact_line(config, lang):
        return None
    channels = build_channels(config, office, question=question, visitor_name=None, language=lang)
    return None if channels else (CLOSED_SHORT.get(lang) or CLOSED_SHORT["en"])


def has_human_path(config: AssistantConfig) -> bool:
    """True when a visitor who asks for a person can be given something at any hour: handoff is
    on and a channel is usable, or the business card has a contact line."""
    if contact_line(config, config.language.primary):
        return True
    if not config.handoff.enabled:
        return False
    channels = build_channels(
        config,
        OfficeState.UNKNOWN,
        question="",
        visitor_name=None,
        language=config.language.primary,
        ignore_availability=True,
    )
    return any(c.kind != "callback" for c in channels)


def no_channel_text(config: AssistantConfig, lang: str) -> str:
    template = NO_CHANNEL_TEXT.get(lang) or NO_CHANNEL_TEXT["en"]
    name = config.identity.business_name or "the business"
    return template.replace("{business_name}", name)


def _enqueue_offered(payload: dict[str, Any]) -> None:
    """Queue the notification for a card offered on an API surface. A few seconds' delay: the
    task reads the conversation's messages, which the turn writes just after the offer."""
    from callobuzz_rag.worker.conversation_tasks import record_offered_handoff_task

    try:
        record_offered_handoff_task.apply_async(
            kwargs={"payload": payload}, queue="periodic", countdown=3
        )
    except Exception:
        logger.warning("Could not queue the handoff notification", exc_info=True)


# TV2 reference: said on a card that offers a call, so the visitor can quote the code.
_REF_LINE = {
    "en": "Your reference: **{ref}** — quote it if you call.",
    "hi": "आपका संदर्भ: **{ref}** — कॉल करते समय यह बताएँ।",
    "hi-Latn": "Aapka reference: **{ref}** — call karte samay yeh bataiye.",
}


def _prefill_for(ctx: OfferContext) -> str | None:
    """`Ref SD-4F7K — <last questions>` for the WhatsApp link; None without a reference (the
    old text is then used). The current question counts; a request for a person says nothing."""
    from callobuzz_rag.conversation.handoff.prefill import whatsapp_prefill
    from callobuzz_rag.conversation.human import is_human_request

    if not ctx.ref_code:
        return None
    questions = [q for q in [*ctx.state.recent_questions, ctx.question] if q.strip()]
    questions = [q for q in questions if not is_human_request(q)]
    return whatsapp_prefill(ctx.ref_code, questions, ctx.language)


def _pick(table: dict[str, str], lang: str) -> str:
    return table.get(lang) or table["en"]


async def _callback_form(ctx: OfferContext) -> "LeadFormBlock | None":
    """C4's lead form in its callback variant: one more day-and-window pair. None where no
    form can be taken (not the widget, leads off, a lead already left, conversational mode)."""
    from callobuzz_rag.api.schemas.assistant import LeadFormBlock, LeadFormField
    from callobuzz_rag.conversation.leads.provider import LEAD_FORM_PROVIDER

    offers_before = ctx.state.lead_offers
    built = await LEAD_FORM_PROVIDER.build(ctx, "callback")
    # A callback form is not the one lead-form offer a conversation gets.
    ctx.state.lead_offers = offers_before
    if not isinstance(built, LeadFormBlock):
        return None
    lang = ctx.language
    extra = [
        LeadFormField(
            id="callback_date", label=_pick(_CALLBACK_DAY, lang), type="date", required=True
        ),
        LeadFormField(
            id="callback_window",
            label=_pick(_CALLBACK_TIME, lang),
            type="select",
            required=True,
            options=list(CALLBACK_WINDOWS),
        ),
    ]
    return built.model_copy(
        update={
            "title": _pick(_CALLBACK_TITLE, lang),
            "fields": [*built.fields, *extra],
            "callback": True,
        }
    )


def _notify_offered(ctx: OfferContext, reason: str, office: OfficeState) -> None:
    """Tell the owner a visitor was offered a person. Off the website widget a person is notified
    only for an explicit request, but on WhatsApp the card promises that someone will reply in
    the chat, so every reason that shows the card notifies (the chat row is the way to reach them)."""
    whatsapp = ctx.channel is Channel.WHATSAPP
    if ctx.channel is Channel.WIDGET or ctx.signals.is_preview:
        return
    if not whatsapp and reason != HandoffReason.EXPLICIT_REQUEST.value:
        return
    _enqueue_offered(
        {
            "corpus_id": str(ctx.corpus_id),
            "conversation_id": str(ctx.conversation_id) if ctx.conversation_id else None,
            "reason": reason,
            "channel": "whatsapp_chat" if whatsapp else "offered",  # TV6 channel
            "language": ctx.language,
            "office": office.value,
            "question": ctx.signals.text,
            "page_url": ctx.signals.page_url,
            "sources": [str(s["file_name"]) for s in ctx.cited_sources if s.get("file_name")][:10],
        }
    )


class HandoffCardProvider:
    kind = OfferKind.HANDOFF_CARD

    def wanted(self, ctx: OfferContext) -> str | None:
        reason = handoff_reason(ctx.config, ctx.signals, ctx.state)
        return reason.value if reason is not None else None

    async def build(self, ctx: OfferContext, reason: str) -> "HandoffCardBlock | None":
        from callobuzz_rag.api.schemas.assistant import HandoffCardBlock

        config, signals = ctx.config, ctx.signals
        if reason == "forced":
            reason = forced_reason(signals).value
        office = signals.office
        channels = build_channels(
            config,
            office,
            question=signals.text,
            visitor_name=None,
            language=ctx.language,
            prefill=_prefill_for(ctx),  # TV2 reference
        )
        callback_form = None
        if any(c.kind == "callback" for c in channels):
            callback_form = await _callback_form(ctx)
            if callback_form is None:
                channels = [c for c in channels if c.kind != "callback"]
        open_now = office is not OfficeState.CLOSED
        handoff = config.handoff
        if not channels:
            if reason not in _ALWAYS_A_PATH:
                return None
            # TV1 human: a visitor who asked for a person always gets an answer. The contact
            # line is the card's message; with nothing at all the director says so in its reply.
            contact = contact_line(config, ctx.language)
            if not contact:
                return None
            if not open_now:  # TV1: a closed business says when it opens, never just a number
                again = opens_again_line(config.identity.card, signals.now, ctx.language)
                if again:
                    contact = f"{contact}\n\n{again}"
            if ctx.ref_code:  # TV2 reference: the visitor is told to phone or write, so show it
                ref_line = _pick(_REF_LINE, ctx.language).replace("{ref}", ctx.ref_code)
                contact = f"{contact}\n\n{ref_line}"
            ctx.state.record_handoff_offer(reason)
            if ctx.channel is Channel.WHATSAPP:  # the card promises a reply in this chat
                _notify_offered(ctx, reason, office)
            return HandoffCardBlock(
                open_now=open_now,
                message=contact,
                channels=[],
                reason=reason,
                ref_code=ctx.ref_code,  # TV2 reference
            )
        text = localized(
            handoff.messages.open if open_now else handoff.messages.closed, ctx.language
        )
        if not open_now:
            line = opens_again_line(config.identity.card, signals.now, ctx.language)
            if line:
                text = f"{text} {line}".strip()
        if ctx.ref_code and any(c.kind in ("call", "callback") for c in channels):  # TV2 reference
            text = f"{text}\n\n" + _pick(_REF_LINE, ctx.language).replace("{ref}", ctx.ref_code)
        ctx.state.record_handoff_offer(reason)
        _notify_offered(ctx, reason, office)
        return HandoffCardBlock(
            open_now=open_now,
            message=text,
            channels=channels,
            reason=reason,
            callback_form=callback_form,
            ref_code=ctx.ref_code,  # TV2 reference
        )


provider = HandoffCardProvider()
registry.register(provider)
