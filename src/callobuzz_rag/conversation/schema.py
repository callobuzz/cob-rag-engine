"""AssistantConfig: the persona and behaviour of one knowledge base (design section 4).

Schema v1 is complete for C1-C9 (README "The AssistantConfig schema"). Every model forbids
unknown fields. Stored JSON carries `schema_version`; `load_config` upgrades older stored
configs through `MIGRATIONS` and refuses newer ones. A later change that adds fields bumps
`CURRENT_SCHEMA_VERSION` and adds a migration function with a test.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, PrivateAttr, field_validator, model_validator

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.conversation.types import LangKey, TurnLabel
from callobuzz_rag.core.exceptions import AssistantSchemaTooNewError
from callobuzz_rag.core.schemas import StrictRequest

CURRENT_SCHEMA_VERSION = 1
MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {}

TemplateKey = Literal[
    "clinic",
    "store",
    "coaching",
    "saas",
    "real_estate",
    "government",
    "restaurant",
    "generic_support",
]
LocalizedText = dict[LangKey, str]
Slug = Field(..., min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _Strict(StrictRequest):
    """One definition of "refuse unknown fields" for every assistant model."""


def _bounded(values: LocalizedText, limit: int) -> LocalizedText:
    for lang, text in values.items():
        if len(text) > limit:
            raise ValueError(f"{lang}: at most {limit} characters")
    return values


def _https_or_http(value: str | None, *, https_only: bool = False) -> str | None:
    if value is None or value == "":
        return None
    lowered = value.strip().lower()
    allowed = ("https://",) if https_only else ("https://", "http://")
    if not lowered.startswith(allowed) or len(value) > 2048:
        raise ValueError("must be an https:// address" if https_only else "must be a web address")
    return value.strip()


def normalize_phone(value: str | None) -> str | None:
    """Indian mobiles / landlines to E.164 (+91...); other countries need a leading +."""
    if value is None or value.strip() == "":
        return None
    raw = value.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and not digits.startswith("91"):
        if 8 <= len(digits) <= 15:
            return "+" + digits
        raise ValueError("not a valid international phone number")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    if len(digits) == 10 and digits[0] in "12345":  # landline with STD code, e.g. 020 ...
        return "+91" + digits
    raise ValueError("not a valid Indian phone number")


class DayHours(_Strict):
    day: Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    open: str
    close: str

    @model_validator(mode="after")
    def _order(self) -> Self:
        if not _HHMM.match(self.open) or not _HHMM.match(self.close):
            raise ValueError("times are HH:MM (24 hours)")
        # close < open is an overnight range (18:00 to 02:00); equal times mean nothing.
        if self.close == self.open:
            raise ValueError("close must differ from open")
        return self


class Holiday(_Strict):
    date: date
    name: str = Field(..., min_length=1, max_length=80)


class BusinessCard(_Strict):
    timezone: str = "Asia/Kolkata"
    hours: list[DayHours] = Field(default_factory=list, max_length=21)
    holidays: list[Holiday] = Field(default_factory=list, max_length=60)
    address: str | None = Field(None, max_length=300)
    phone: str | None = None
    whatsapp: str | None = None
    email: ValidatedEmail | None = None
    website: str | None = None
    booking_url: str | None = None
    service_areas: list[str] = Field(default_factory=list, max_length=20)
    payment_info: str | None = Field(None, max_length=200)
    refund_info: str | None = Field(None, max_length=200)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("phone", "whatsapp")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("website", "booking_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _https_or_http(value)

    @field_validator("service_areas")
    @classmethod
    def _areas(cls, values: list[str]) -> list[str]:
        if any(len(v) > 80 for v in values):
            raise ValueError("each service area is at most 80 characters")
        return values


_DISCLOSURE: LocalizedText = {
    "en": "I'm {assistant_name}, the AI assistant of {business_name}.",
    "hi": "मैं {assistant_name} हूँ, {business_name} की AI सहायक।",
    "hi-Latn": "Main {assistant_name} hoon, {business_name} ki AI assistant.",
}


def _copy(text: LocalizedText) -> Callable[[], LocalizedText]:
    return lambda: dict(text)


class Identity(_Strict):
    assistant_name: str = Field("Asha", min_length=1, max_length=40)
    avatar_url: str | None = None
    business_name: str = Field("", max_length=120)
    business_type: TemplateKey = "generic_support"
    business_profile: str = Field("", max_length=500)
    audience: str = Field("", max_length=200)
    ai_disclosure: bool = True
    ai_disclosure_text: LocalizedText = Field(default_factory=_copy(_DISCLOSURE))
    card: BusinessCard = Field(default_factory=BusinessCard)

    @field_validator("avatar_url")
    @classmethod
    def _avatar(cls, value: str | None) -> str | None:
        # BIZ4 avatar: the engine's own uploaded picture is a path, not an https address.
        from callobuzz_rag.assets.avatars import is_avatar_path

        if value is not None and is_avatar_path(value.strip()):
            return value.strip()
        return _https_or_http(value, https_only=True)
        # /BIZ4 avatar

    @field_validator("ai_disclosure_text")
    @classmethod
    def _disc(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 200)


class FormatPrefs(_Strict):
    lists: Literal["prefer", "allow", "avoid"] = "prefer"
    tables: Literal["when_comparing", "allow", "never"] = "when_comparing"
    headings: Literal["never", "allow"] = "never"
    custom: str = Field("", max_length=200)


class TermPair(_Strict):
    use: str = Field(..., min_length=1, max_length=60)
    instead_of: str = Field(..., min_length=1, max_length=60)


_GREETING: LocalizedText = {
    "en": "Hi! I'm {assistant_name}. How can I help you today?",
    "hi": "नमस्ते! मैं {assistant_name} हूँ। मैं आपकी क्या मदद कर सकती हूँ?",
    "hi-Latn": "Namaste! Main {assistant_name} hoon. Main aapki kya madad kar sakti hoon?",
}
_CLOSING: LocalizedText = {
    "en": "Glad I could help. Anything else?",
    "hi": "मदद करके खुशी हुई। और कुछ?",
    "hi-Latn": "Madad karke khushi hui. Aur kuch?",
}


class Voice(_Strict):
    tone_preset: Literal["friendly", "professional", "warm", "matter_of_fact", "playful"] = (
        "friendly"
    )
    custom_tone: str = Field("", max_length=300)
    formality: Literal["formal", "neutral", "casual"] = "neutral"
    hindi_pronoun: Literal["aap", "tum"] = "aap"
    response_length: Literal["short", "medium", "detailed"] = "medium"
    format: FormatPrefs = Field(default_factory=FormatPrefs)
    emoji: Literal["none", "light", "frequent"] = "none"
    preferred_terms: list[TermPair] = Field(default_factory=list, max_length=30)
    banned_phrases: list[str] = Field(default_factory=list, max_length=30)
    voice_samples: list[str] = Field(default_factory=list, max_length=5)
    greeting: LocalizedText = Field(default_factory=_copy(_GREETING))
    closing_line: LocalizedText = Field(default_factory=_copy(_CLOSING))

    @field_validator("banned_phrases")
    @classmethod
    def _banned(cls, values: list[str]) -> list[str]:
        if any(len(v) > 80 or not v.strip() for v in values):
            raise ValueError("each banned phrase is 1 to 80 characters")
        return values

    @field_validator("voice_samples")
    @classmethod
    def _samples(cls, values: list[str]) -> list[str]:
        if any(len(v) > 500 for v in values):
            raise ValueError("each voice sample is at most 500 characters")
        return values

    @field_validator("greeting")
    @classmethod
    def _greet(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 300)

    @field_validator("closing_line")
    @classmethod
    def _close(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 200)


def _default_langs() -> list[LangKey]:
    return ["en", "hi", "hi-Latn"]


class LanguagePolicy(_Strict):
    primary: LangKey = "en"
    supported: list[LangKey] = Field(default_factory=_default_langs, min_length=1)
    auto_detect: bool = True
    reply_policy: Literal["mirror", "fixed"] = "mirror"
    script_policy: Literal["mirror", "native", "roman"] = "mirror"
    documents_language: LangKey = "en"
    translate_queries: bool = True


class QuickReply(_Strict):
    label: str = Field(..., min_length=1, max_length=20)
    message: str = Field(..., min_length=1, max_length=200)


class Topic(_Strict):
    id: str = Slug
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    examples: list[str] = Field(default_factory=list, max_length=10)
    verbatim_answer: LocalizedText | None = None
    quick_replies: list[QuickReply] = Field(default_factory=list, max_length=4)

    @field_validator("examples")
    @classmethod
    def _ex(cls, values: list[str]) -> list[str]:
        if any(len(v) > 200 for v in values):
            raise ValueError("each example is at most 200 characters")
        return values

    @field_validator("verbatim_answer")
    @classmethod
    def _verbatim(cls, value: LocalizedText | None) -> LocalizedText | None:
        return None if value is None else _bounded(value, 2000)


class BlockedTopic(_Strict):
    id: str = Slug
    name: str = Field(..., min_length=1, max_length=60)
    description: str = Field("", max_length=300)
    action: Literal["decline", "handoff"] = "decline"
    message: LocalizedText | None = None

    @field_validator("message")
    @classmethod
    def _msg(cls, value: LocalizedText | None) -> LocalizedText | None:
        return None if value is None else _bounded(value, 300)


_NO_ANSWER: LocalizedText = {
    "en": "I don't have that information in our documents.",
    "hi": "यह जानकारी हमारे दस्तावेज़ों में नहीं है।",
    "hi-Latn": "Yeh jaankari hamare documents mein nahi hai.",
}
_SOFTENER: LocalizedText = {
    "en": "For exact details on that, please check with us directly.",
    "hi": "इसकी सटीक जानकारी के लिए कृपया हमसे सीधे पूछें।",
    "hi-Latn": "Iski sahi jaankari ke liye please humse seedha poochiye.",
}


def _default_actions() -> list[Literal["clarify", "handoff", "lead"]]:
    return ["clarify", "handoff", "lead"]


class Fallback(_Strict):
    no_answer_message: LocalizedText = Field(default_factory=_copy(_NO_ANSWER))
    actions: list[Literal["clarify", "handoff", "lead"]] = Field(default_factory=_default_actions)
    softener: LocalizedText = Field(default_factory=_copy(_SOFTENER))

    @field_validator("no_answer_message")
    @classmethod
    def _na(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 400)

    @field_validator("softener")
    @classmethod
    def _soft(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 300)


class Scope(_Strict):
    scope_mode: Literal["strict_docs", "docs_plus_domain", "open_with_guardrails"] = (
        "docs_plus_domain"
    )
    topics: list[Topic] = Field(default_factory=list, max_length=40)
    blocked_topics: list[BlockedTopic] = Field(default_factory=list, max_length=40)
    sensitive_profile: Literal[
        "none", "medical", "legal", "financial", "education", "government"
    ] = "none"
    disclaimers: LocalizedText = Field(default_factory=dict)
    fallback: Fallback = Field(default_factory=Fallback)
    min_retrieval_score: float | None = Field(None, ge=0.0, le=1.0)
    claim_check: Literal["enforce", "warn", "off"] = "enforce"
    max_off_topic_strikes: int = Field(3, ge=1, le=10)

    @field_validator("disclaimers")
    @classmethod
    def _disc(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 400)

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        ids = [t.id for t in self.topics] + [b.id for b in self.blocked_topics]
        if len(ids) != len(set(ids)):
            raise ValueError("topic and blocked-topic ids must be unique")
        return self


class RuleWhen(_Strict):
    topic: str | None = None
    label: TurnLabel | None = None
    language: LangKey | None = None
    page_url_glob: str | None = Field(None, max_length=200)
    office_hours: Literal["open", "closed"] | None = None
    lead_status: Literal["none", "captured"] | None = None


class GuidanceRule(_Strict):
    id: str = Slug
    type: Literal["communication", "context", "clarification", "escalation", "sales", "compliance"]
    when: RuleWhen | None = None
    instruction: str = Field(..., min_length=1, max_length=500)
    enabled: bool = True
    priority: int = Field(50, ge=0, le=100)
    source: Literal["owner", "template", "setup"] = "owner"


class CtaPolicy(_Strict):
    max_per_answer: Literal[0, 1] = 1
    buying_intent_cta: Literal[
        "lead_form", "whatsapp", "call", "booking", "handoff", "link", "none"
    ] = "lead_form"
    link_url: str | None = None
    link_label: LocalizedText | None = None

    @field_validator("link_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _https_or_http(value)

    @field_validator("link_label")
    @classmethod
    def _label(cls, value: LocalizedText | None) -> LocalizedText | None:
        return None if value is None else _bounded(value, 40)


class ProactiveGreeting(_Strict):
    id: str = Slug
    page_url_glob: str = Field(..., min_length=1, max_length=200)
    delay_seconds: int = Field(15, ge=0, le=300)
    message: LocalizedText
    frequency: Literal["once_per_session", "once_per_day", "always"] = "once_per_session"
    enabled: bool = True

    @field_validator("message")
    @classmethod
    def _msg(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 200)


class ConversationSettings(_Strict):
    starters: list[QuickReply] = Field(default_factory=list, max_length=4)
    ai_followups: bool = True
    cta: CtaPolicy = Field(default_factory=CtaPolicy)
    proactive_greetings: list[ProactiveGreeting] = Field(default_factory=list, max_length=10)


class LeadTriggers(_Strict):
    buying_intent: bool = True
    no_answer: bool = True
    after_n_messages: int | None = Field(None, ge=1, le=50)
    topics: list[str] = Field(default_factory=list)
    page_url_globs: list[str] = Field(default_factory=list, max_length=10)
    human_request: bool = False
    out_of_hours: bool = True


class LeadField(_Strict):
    id: str = Slug
    label: LocalizedText
    type: Literal["text", "select", "date", "number"]
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=20)
    max_length: int = Field(120, ge=1, le=200)

    @model_validator(mode="after")
    def _options(self) -> Self:
        if self.type == "select" and not self.options:
            raise ValueError("a select field needs options")
        if any(len(o) > 60 for o in self.options):
            raise ValueError("each option is at most 60 characters")
        _bounded(self.label, 60)
        return self


# TV2 reference: `_NOTICE_V1` is the frozen first wording (stored configs may still hold it);
# `_NOTICE` is v2, which also says that a summary and a reference code go with the details.
_NOTICE_V1: LocalizedText = {
    "en": (
        "**Before you share your details.** {business_name} will use your name and phone number "
        "(and e-mail, if you give it) for this purpose: {purpose} They are seen only by "
        "{business_name}'s team, stored securely, and deleted after {retention_days} days. You can withdraw your consent "
        "and have them deleted at any time with the link we show after you submit, or by writing "
        "to {contact}. You can also complain to the Data Protection Board of India.{privacy_link}"
    ),
    "hi": (
        "**अपनी जानकारी देने से पहले।** {business_name} आपका नाम और फ़ोन नंबर (और ई-मेल, अगर आप दें) "
        "केवल इस उद्देश्य से उपयोग करेगा: {purpose} इन्हें केवल {business_name} की टीम देखती है, ये "
        "सुरक्षित रखे जाते हैं और {retention_days} दिनों के बाद मिटा दिए जाते हैं। आप जमा करने के बाद "
        "दिखने वाले लिंक से, या {contact} को लिखकर, कभी भी अपनी सहमति वापस ले सकते हैं और जानकारी "
        "मिटवा सकते हैं। आप भारत के डेटा संरक्षण बोर्ड से शिकायत भी कर सकते हैं।{privacy_link}"
    ),
    "hi-Latn": (
        "**Apni details dene se pehle.** {business_name} aapka naam aur phone number (aur email, "
        "agar aap dein) sirf is liye use karega: {purpose} Inhe sirf {business_name} ki team "
        "dekhti hai, yeh surakshit rakhe jaate hain aur {retention_days} din ke baad delete ho "
        "jaate hain. Submit karne ke baad dikhne wale link se, ya {contact} ko likh kar, aap kabhi "
        "bhi apni sahmati wapas le sakte hain aur details delete karwa sakte hain. "
        "Aap Data Protection Board of India se shikayat bhi kar sakte hain.{privacy_link}"
    ),
}
_NOTICE_SHARED: LocalizedText = {
    "en": (
        "To save you repeating yourself, a short summary of this chat and a reference code go "
        "to {business_name}'s team with your details."
    ),
    "hi": (
        "आपको बार-बार बताना न पड़े, इसलिए इस चैट का एक छोटा सारांश और एक संदर्भ कोड आपकी जानकारी के "
        "साथ {business_name} की टीम को जाता है।"
    ),
    "hi-Latn": (
        "Aapko baar-baar batana na pade, isliye is chat ka ek chhota summary aur ek reference code "
        "aapki details ke saath {business_name} ki team ko jaata hai."
    ),
}
_NOTICE_SPLIT: LocalizedText = {
    "en": " You can withdraw your consent",
    "hi": " आप जमा करने के बाद",
    "hi-Latn": " Submit karne ke baad",
}


def _notice_v2() -> LocalizedText:
    out: LocalizedText = {}
    for lang, text in _NOTICE_V1.items():
        head, sep, tail = text.partition(_NOTICE_SPLIT[lang])
        if not sep:  # the frozen text changed shape: fail at import, not in front of a visitor
            raise RuntimeError(f"consent notice v1 has no split point for {lang}")
        out[lang] = f"{head} {_NOTICE_SHARED[lang]}{sep}{tail}"
    return out


_NOTICE: LocalizedText = _notice_v2()
NOTICE_VERSION = 2
# /TV2 reference
_CONFIRM: LocalizedText = {
    "en": "Thank you! Our team will contact you soon.",
    "hi": "धन्यवाद! हमारी टीम जल्द ही आपसे संपर्क करेगी।",
    "hi-Latn": "Dhanyavaad! Hamari team jaldi aapse sampark karegi.",
}


class LeadConsent(_Strict):
    notice: LocalizedText = Field(default_factory=_copy(_NOTICE))
    purpose: str = Field("To contact you about your enquiry.", min_length=1, max_length=300)
    retention_days: int = Field(365, ge=30, le=3650)
    privacy_policy_url: str | None = None
    notice_version: int = Field(NOTICE_VERSION, ge=1)  # TV2 reference: v2 is the default wording

    @field_validator("notice")
    @classmethod
    def _notice(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 1500)

    @field_validator("privacy_policy_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return _https_or_http(value)


class Condition(_Strict):
    field: str = Field(..., min_length=1, max_length=40)
    op: Literal["eq", "neq", "in", "gte", "lte", "present"]
    value: str | float | list[str] | None = None


class QualificationRule(_Strict):
    status: Literal["qualified", "partial"]
    conditions: list[Condition] = Field(..., min_length=1, max_length=5)


class Notify(_Strict):
    emails: list[ValidatedEmail] = Field(default_factory=list, max_length=5)
    webhook: bool = True
    daily_digest: bool = False


class Leads(_Strict):
    enabled: bool = True
    mode: Literal["form", "conversational"] = "form"
    triggers: LeadTriggers = Field(default_factory=LeadTriggers)
    name: Literal["required", "optional", "off"] = "optional"
    phone: Literal["required", "optional", "off"] = "required"
    email: Literal["required", "optional", "off"] = "optional"
    custom_fields: list[LeadField] = Field(default_factory=list, max_length=6)
    consent: LeadConsent = Field(default_factory=LeadConsent)
    qualification: list[QualificationRule] = Field(default_factory=list, max_length=10)
    notify: Notify = Field(default_factory=Notify)
    confirmation_message: LocalizedText = Field(default_factory=_copy(_CONFIRM))

    @model_validator(mode="after")
    def _contact(self) -> Self:
        if "required" not in (self.phone, self.email):
            raise ValueError("leads need a required phone or email field")
        if any(f.id in ("name", "phone", "email") for f in self.custom_fields):
            raise ValueError("custom field ids name, phone and email are reserved")
        _bounded(self.confirmation_message, 300)
        return self


class HandoffChannel(_Strict):
    kind: Literal["whatsapp", "call", "callback", "email", "contact_page", "booking"]
    enabled: bool = True
    label: LocalizedText | None = None
    value: str | None = Field(None, max_length=2048)
    available: Literal["always", "open_hours", "closed_hours"] = "always"

    @field_validator("label")
    @classmethod
    def _label(cls, value: LocalizedText | None) -> LocalizedText | None:
        return None if value is None else _bounded(value, 40)


def _default_keywords() -> list[str]:
    return ["refund", "cancel", "complaint", "शिकायत", "wapas", "refund chahiye"]


class HandoffTriggers(_Strict):
    explicit_request: bool = True
    frustration: bool = True
    unresolved_turns: int | None = Field(3, ge=1, le=20)
    low_confidence_streak: int | None = Field(2, ge=1, le=10)
    blocked_topics: bool = True
    keywords: list[str] = Field(default_factory=_default_keywords, max_length=30)
    out_of_hours: bool = False
    # TV1 human: offer a person after this many turns the assistant could not answer (0 = never
    # offer by itself). A defaulted field: a stored config without it reads 1, no version bump.
    offer_after_misses: int = Field(1, ge=0, le=5)
    # /TV1 human


_OPEN: LocalizedText = {
    "en": "You can reach our team here:",
    "hi": "आप हमारी टीम से यहाँ संपर्क कर सकते हैं:",
    "hi-Latn": "Aap hamari team se yahan baat kar sakte hain:",
}
_CLOSED: LocalizedText = {
    "en": "We're closed right now. Leave a message and we'll get back to you when we open.",
    "hi": "अभी हम बंद हैं। संदेश छोड़ दीजिए, खुलते ही हम आपसे संपर्क करेंगे।",
    "hi-Latn": "Abhi hum band hain. Message chhod dijiye, khulte hi hum aapse sampark karenge.",
}


DEFAULT_PAUSED: LocalizedText = {
    "en": "We can't answer questions here right now, but our team will be happy to help you "
    "directly:",
    "hi": "हम अभी यहाँ सवालों के जवाब नहीं दे सकते, लेकिन हमारी टीम सीधे आपकी मदद करेगी:",
    "hi-Latn": "Hum abhi yahan sawaalon ke jawab nahin de sakte, lekin hamari team seedhe aapki "
    "madad karegi:",
}


# PIP1 lane
DEFAULT_BUSY: LocalizedText = {
    "en": "We're answering a lot of questions right now. Please ask again in a minute, or "
    "reach our team directly:",
    "hi": "अभी हम बहुत सारे सवालों के जवाब दे रहे हैं। कृपया एक मिनट बाद फिर पूछें, या सीधे हमारी टीम से बात करें:",
    "hi-Latn": "Abhi hum bahut saare sawaalon ke jawab de rahe hain. Kripya ek minute baad "
    "phir poochhiye, ya seedhe hamari team se baat kijiye:",
}
# /PIP1 lane


class HandoffMessages(_Strict):
    open: LocalizedText = Field(default_factory=_copy(_OPEN))
    closed: LocalizedText = Field(default_factory=_copy(_CLOSED))
    # Shown to visitors while the workspace's answers are paused for balance (billing BIL4);
    # a stored config without it reads this default.
    paused: LocalizedText = Field(default_factory=_copy(DEFAULT_PAUSED))
    # PIP1 lane: shown when the engine is so busy that this question waited too long for its
    # turn; a stored config without it reads this default.
    busy: LocalizedText = Field(default_factory=_copy(DEFAULT_BUSY))
    # /PIP1 lane

    @field_validator("open", "closed", "paused", "busy")
    @classmethod
    def _msg(cls, value: LocalizedText) -> LocalizedText:
        return _bounded(value, 300)


def _default_channels() -> list[HandoffChannel]:
    return [
        HandoffChannel(kind="whatsapp"),
        HandoffChannel(kind="call", available="open_hours"),
        HandoffChannel(kind="callback"),
        HandoffChannel(kind="email"),
    ]


class Handoff(_Strict):
    enabled: bool = True
    channels: list[HandoffChannel] = Field(default_factory=_default_channels, max_length=6)
    triggers: HandoffTriggers = Field(default_factory=HandoffTriggers)
    messages: HandoffMessages = Field(default_factory=HandoffMessages)
    notify: Notify = Field(default_factory=Notify)
    # TV1 human: the "Talk to a person" button in the widget header. The owner may hide it only
    # where the deployer allows it (engine setting `trust.allow_hide_human_button`).
    show_human_button: bool = True
    # /TV1 human


class Advanced(_Strict):
    # Legacy corpora.prompt_config.system_prompt allows 10,000 characters and constraints
    # 5,000, and the migration carries both over unchanged, so this must hold them.
    extra_instructions: str | None = Field(None, max_length=15_000)


class AssistantConfig(_Strict):
    schema_version: Literal[1] = 1
    enabled: bool = True
    identity: Identity = Field(default_factory=Identity)
    voice: Voice = Field(default_factory=Voice)
    language: LanguagePolicy = Field(default_factory=LanguagePolicy)
    scope: Scope = Field(default_factory=Scope)
    rules: list[GuidanceRule] = Field(default_factory=list, max_length=100)
    conversation: ConversationSettings = Field(default_factory=ConversationSettings)
    leads: Leads = Field(default_factory=Leads)
    handoff: Handoff = Field(default_factory=Handoff)
    advanced: Advanced = Field(default_factory=Advanced)

    # P3 legal: a private attribute, so a request body can never set it and it is never stored or
    # serialised. `get_runtime_config` sets it from the deployer's published privacy notice
    # (configured RAG_PUBLIC_URL only) for the consent notice's link.
    _deployer_privacy_url: str | None = PrivateAttr(default=None)

    @property
    def deployer_privacy_url(self) -> str | None:
        return self._deployer_privacy_url

    def with_deployer_privacy_url(self, url: str | None) -> "AssistantConfig":
        self._deployer_privacy_url = url
        return self

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> Self:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("guidance rule ids must be unique")
        return self


def load_config(raw: dict[str, Any]) -> AssistantConfig:
    """Validate stored JSON, upgrading older schema versions; refuse newer ones."""
    data = dict(raw)
    version = int(data.get("schema_version", CURRENT_SCHEMA_VERSION))
    if version > CURRENT_SCHEMA_VERSION:
        raise AssistantSchemaTooNewError(
            f"assistant config schema_version {version} is newer than {CURRENT_SCHEMA_VERSION}"
        )
    while version < CURRENT_SCHEMA_VERSION:
        data = MIGRATIONS[version](data)
        version += 1
        data["schema_version"] = version
    return AssistantConfig.model_validate(data)


def localized(text: Mapping[Any, str], lang: str) -> str:
    """`text[lang]`, else English, else the first value, else ""."""
    if text.get(lang):
        return text[lang]
    if text.get("en"):
        return text["en"]
    return next((v for v in text.values() if v), "")


def fill_persona(text: str, config: AssistantConfig) -> str:
    """Fill `{assistant_name}` and `{business_name}` only (owner text may contain braces)."""
    business = config.identity.business_name or "our business"
    return text.replace("{assistant_name}", config.identity.assistant_name).replace(
        "{business_name}", business
    )
