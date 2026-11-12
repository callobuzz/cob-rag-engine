"""The lead offer: a form after the answer (default) or one question in the chat
(conversational mode, research recommendation F). Registered at import.

Only the website widget and WhatsApp (TV6) collect leads: the pre-turn hook ignores every other
channel and every preview turn, exactly like the triggers. On WhatsApp there is no form: the offer
is one sentence asking whether the team may contact the customer on the number they are writing
from, and the number is put on the lead only after a yes.
"""

import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from callobuzz_rag.conversation.leads.consent import consent_label, notice_for
from callobuzz_rag.conversation.leads.crypto import (
    LeadCryptoUnavailableError,
    lead_crypto_from_settings,
)
from callobuzz_rag.conversation.leads.triggers import lead_trigger
from callobuzz_rag.conversation.leads.validation import (
    CleanLead,
    LeadValidationError,
    normalize_email,
    normalize_phone,
)
from callobuzz_rag.conversation.offers import (
    OfferKind,
    PreTurnContext,
    PreTurnResult,
    registry,
)
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.conversation.types import Channel

if TYPE_CHECKING:
    from callobuzz_rag.api.schemas.assistant import LeadFormBlock

logger = logging.getLogger(__name__)

_TITLE = {
    "en": "Leave your number and we'll call you",
    "hi": "अपना नंबर दें, हम आपको कॉल करेंगे",
    "hi-Latn": "Apna number dijiye, hum aapko call karenge",
}
_SUBMIT = {"en": "Send", "hi": "भेजें", "hi-Latn": "Bhejein"}
_LABELS = {
    "name": {"en": "Your name", "hi": "आपका नाम", "hi-Latn": "Aapka naam"},
    "phone": {"en": "Mobile number", "hi": "मोबाइल नंबर", "hi-Latn": "Mobile number"},
    "email": {"en": "E-mail", "hi": "ई-मेल", "hi-Latn": "Email"},
}
_ASK = {
    "en": "If you'd like the team to call you, share your mobile number here.",
    "hi": "अगर आप चाहें कि टीम आपको कॉल करे, तो यहाँ अपना मोबाइल नंबर लिखें।",
    "hi-Latn": "Agar aap chahte hain ki team aapko call kare, to yahan apna mobile number likhiye.",
}
_ASK_EMAIL = {
    "en": "Could you also share your e-mail address?",
    "hi": "क्या आप अपना ई-मेल पता भी बता सकते हैं?",
    "hi-Latn": "Kya aap apna email address bhi bata sakte hain?",
}
_CONSENT_Q = {
    "en": "Thanks! {notice}\n\nDo you agree?",
    "hi": "धन्यवाद! {notice}\n\nक्या आप सहमत हैं?",
    "hi-Latn": "Shukriya! {notice}\n\nKya aap sahmat hain?",
}
# TV6 channel: asked on WhatsApp, where the customer's own number is already known
_ASK_OWN_NUMBER = {
    "en": "Would you like the team to contact you on this WhatsApp number? {notice}\n\n"
    "Reply YES to agree.",
    "hi": "क्या आप चाहते हैं कि टीम आपसे इसी WhatsApp नंबर पर संपर्क करे? {notice}\n\n"
    "सहमत होने के लिए हाँ लिखें।",
    "hi-Latn": "Kya aap chahte hain ki team aapse isi WhatsApp number par sampark kare? {notice}\n\n"
    "Sahmat hone ke liye haan likhiye.",
}
# /TV6 channel
_YES = {"yes, i agree", "yes", "हाँ, मैं सहमत हूँ", "हाँ", "haan, sahmat", "haan", "ha"}
_YES_LABEL = {"en": "Yes, I agree", "hi": "हाँ, मैं सहमत हूँ", "hi-Latn": "Haan, sahmat"}
_NO_LABEL = {"en": "No", "hi": "नहीं", "hi-Latn": "Nahi"}
_THANKS = {
    "en": "Thank you, we've passed your details to the team.",
    "hi": "धन्यवाद, आपकी जानकारी टीम को भेज दी गई है।",
    "hi-Latn": "Shukriya, aapki details team ko bhej di gayi hain.",
}
_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,16}\d)")
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE_MASK, _EMAIL_MASK = "[phone shared]", "[email shared]"
# A contact picked up in the chat waits this long for the visitor's yes; after that it is dropped
# (retention also clears what an abandoned chat leaves behind, `leads/retention.py`).
_CAPTURE_TTL = timedelta(hours=1)


def _l(table: dict[str, str], lang: str) -> str:
    return table.get(lang) or table["en"]


class LeadFormProvider:
    kind = OfferKind.LEAD_FORM

    def wanted(self, ctx: Any) -> str | None:
        return lead_trigger(ctx)

    async def build(self, ctx: Any, reason: str) -> "LeadFormBlock | str | None":
        """A form (default) or a one-line question (conversational mode). Forced mode (the
        no-answer fallback asked for a form) builds even when no trigger fired."""
        # Deferred: `api.schemas.assistant` imports this package while it loads (the package
        # registers itself from `conversation/__init__`), so a top-level import is a cycle.
        from callobuzz_rag.api.schemas.assistant import LeadFormBlock, LeadFormField

        leads, lang = ctx.config.leads, ctx.language
        if not leads.enabled or ctx.state.lead_id:
            return None
        if ctx.channel not in (Channel.WIDGET, Channel.WHATSAPP):  # TV6 channel
            return None
        if getattr(ctx.signals, "is_preview", False):
            return None
        reason = "asked" if reason == "forced" else reason
        ctx.state.lead_offers += 1
        ctx.state.lead_offer_reason = reason
        if ctx.channel is Channel.WHATSAPP:  # TV6 channel: no form; consent first, then the number
            ctx.state.lead_capture = {
                "awaiting_consent": True,
                "own_number": True,
                "at": ctx.signals.now.isoformat(),
            }
            return _l(_ASK_OWN_NUMBER, lang).replace("{notice}", notice_for(ctx.config, lang))
        if leads.mode == "conversational":
            return _l(_ASK, lang)
        fields: list[LeadFormField] = []
        for key, kind in (("name", "text"), ("phone", "tel"), ("email", "email")):
            setting = getattr(leads, key)
            if setting != "off":
                fields.append(
                    LeadFormField(
                        id=key,
                        label=_l(_LABELS[key], lang),
                        type=kind,
                        required=setting == "required",
                        options=[],
                    )
                )
        for spec in leads.custom_fields:
            fields.append(
                LeadFormField(
                    id=spec.id,
                    label=spec.label.get(lang) or spec.label.get("en") or spec.id,
                    type=spec.type,
                    required=spec.required,
                    options=list(spec.options),
                )
            )
        form_id = "lf_" + secrets.token_hex(8)
        # Each entry records the notice version shown with the form: a submit after the owner
        # republished the notice is refused, so a consent row never names wording not seen.
        shown = f"{form_id}:{leads.consent.notice_version}"
        ctx.state.lead_form_ids = [*ctx.state.lead_form_ids, shown][-5:]
        return LeadFormBlock(
            form_id=form_id,
            title=_l(_TITLE, lang),
            notice=notice_for(ctx.config, lang),
            consent_label=consent_label(lang),
            privacy_url=leads.consent.privacy_policy_url,
            fields=fields,
            submit_label=_l(_SUBMIT, lang),
            trigger=reason,
        )


LEAD_FORM_PROVIDER = LeadFormProvider()
registry.register(LEAD_FORM_PROVIDER)


async def _create_from_capture(pre: PreTurnContext, clean: CleanLead) -> uuid.UUID:
    """Create the lead from a conversational capture (own session, committed). The consent
    row has no client address: a pre-turn hook does not see one (the form route does)."""
    from callobuzz_rag.conversation.leads.service import NewLead, create_lead
    from callobuzz_rag.worker.conversation_tasks import queue_lead_notification

    crypto = lead_crypto_from_settings(pre.settings)
    async with pre.session_factory() as session:
        lead = await create_lead(
            session,
            crypto,
            pre.config,
            NewLead(
                corpus_id=pre.corpus_id,
                tenant_id=pre.tenant_id,
                conversation_id=pre.conversation_id,
                widget_key_id=pre.widget_key_id,
                channel=pre.channel.value,
                clean=clean,
                language=pre.language,
                page_url=pre.page_url,
                utm=pre.utm,
                trigger="conversational",
                source_label=None,
                tags=[],
                callback_window=None,
                sources_consulted=[],
                notice_text=notice_for(pre.config, pre.language),
                ip=None,
            ),
            now=pre.now,
        )
        await session.commit()
        lead_id = lead.id
    queue_lead_notification(lead_id)
    return lead_id


def _capture_stale(capture: dict[str, Any], now: datetime) -> bool:
    """A capture with no valid timestamp (written before one was kept) counts as stale."""
    try:
        return now - datetime.fromisoformat(str(capture["at"])) > _CAPTURE_TTL
    except (KeyError, ValueError, TypeError):
        return True


def _chips(lang: str) -> list[QuickReply]:
    return [
        QuickReply(label=_l(_YES_LABEL, lang), message=_l(_YES_LABEL, lang)),
        QuickReply(label=_l(_NO_LABEL, lang), message=_l(_NO_LABEL, lang)),
    ]


async def capture_contact_hook(pre: PreTurnContext) -> PreTurnResult | None:
    """Conversational mode: pick a phone or e-mail out of the visitor's message, keep it
    sealed in the conversation state, ask for consent, create the lead on a yes.

    Never logs the message or what was captured. Returns None (the turn goes on as a normal
    question) whenever it has nothing to do.
    """
    leads, state = pre.config.leads, pre.state
    whatsapp = pre.channel is Channel.WHATSAPP  # TV6 channel: any mode, there is no form to show
    if not leads.enabled or state.lead_id or not (whatsapp or leads.mode == "conversational"):
        return None
    if pre.channel not in (Channel.WIDGET, Channel.WHATSAPP) or pre.is_preview:
        return None
    capture = state.lead_capture or {}
    if capture and _capture_stale(capture, pre.now):
        state.lead_capture, capture = None, {}
    if state.lead_offers == 0 and not capture:
        return None
    try:
        crypto = lead_crypto_from_settings(pre.settings)
    except LeadCryptoUnavailableError:
        return None
    lang = pre.language
    if capture.get("awaiting_consent"):
        if pre.text.strip().lower() in _YES:
            if capture.get("own_number"):  # TV6 channel: the number they are writing from
                if not pre.contact_phone:
                    state.lead_capture = None
                    return None
                clean = CleanLead(name=None, phone=pre.contact_phone, email=None)
            else:
                data = crypto.decrypt_json(capture["sealed"])
                clean = CleanLead(name=None, phone=data.get("phone"), email=data.get("email"))
            state.lead_id = str(await _create_from_capture(pre, clean))
            state.lead_capture = None
            return PreTurnResult(stored_text=None, reply=_l(_THANKS, lang))
        state.lead_capture = None
        state.lead_dismissed = True
        return None
    stored, found = pre.text, {}
    for match in _PHONE_RE.finditer(pre.text):
        try:
            found["phone"] = normalize_phone(match.group())
        except LeadValidationError:
            continue
        stored = stored.replace(match.group(), _PHONE_MASK)
    for match in _EMAIL_RE.finditer(pre.text):
        try:
            found["email"] = normalize_email(match.group())
        except LeadValidationError:
            continue
        stored = stored.replace(match.group(), _EMAIL_MASK)
    if not found:
        return None
    previous = crypto.decrypt_json(capture["sealed"]) if capture.get("sealed") else {}
    merged = {**previous, **found}
    needs_phone = leads.phone == "required" and "phone" not in merged
    needs_email = leads.email == "required" and "email" not in merged
    state.lead_capture = {
        "sealed": crypto.encrypt_json(merged),
        "awaiting_consent": False,
        "at": pre.now.isoformat(),
    }
    if needs_phone or needs_email:
        return PreTurnResult(
            stored_text=stored, reply=_l(_ASK if needs_phone else _ASK_EMAIL, lang)
        )
    state.lead_capture["awaiting_consent"] = True
    reply = _l(_CONSENT_Q, lang).replace("{notice}", notice_for(pre.config, lang))
    return PreTurnResult(stored_text=stored, reply=reply, quick_replies=_chips(lang))


registry.register_pre_turn(capture_contact_hook)
