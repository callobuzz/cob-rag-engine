"""Guided sales replies (conversation C6): suggestion chips and one call to action.

Chips come from the owner first (per-topic quick replies, starters), then from the fast
model (AI follow-ups), and an AI follow-up is kept only when the knowledge base can answer
it: it names an allow-list topic, or a dense probe for it passes the retrieval gate. A
call to action is at most one per answer and never one the visitor already dismissed.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Final

from callobuzz_rag.conversation.handoff.channels import build_channels, channel_url
from callobuzz_rag.conversation.offers import OfferContext, OfferKind, OfferRegistry, registry
from callobuzz_rag.conversation.schema import (
    AssistantConfig,
    HandoffChannel,
    QuickReply,
    localized,
)
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal
from callobuzz_rag.text.tokenize import tokenize

if TYPE_CHECKING:
    from callobuzz_rag.api.schemas.assistant import Cta

logger = logging.getLogger(__name__)

MAX_CHIPS: Final = 4
MAX_CHIP_LABEL: Final = 20
MAX_CHIP_MESSAGE: Final = 200
FOLLOWUP_BUDGET_S: Final = 1.5
MAX_PROBES: Final = 3
ProbeFn = Callable[[str], Awaitable[bool]]

_STOP = frozenset(
    {
        "a", "an", "and", "are", "can", "do", "does", "for", "from", "how", "i", "in", "is",
        "it", "me", "my", "of", "on", "or", "the", "to", "we", "what", "when", "where",
        "which", "who", "why", "will", "with", "you", "your", "hai", "kya", "ka", "ki", "ke",
        "ko", "mein", "se", "aur",
    }
)  # fmt: skip

FOLLOWUP_PROMPT = """You suggest follow-up questions a website visitor might tap next.

The visitor asked: {question}
The assistant answered: {answer}
The answer used these document sections: {sections}
The assistant may only talk about these topics: {topics}

Write exactly 3 short follow-up questions (each under 60 characters) in the language "{language}",
each about one of the topics above, each answerable from documents like those sections, none
repeating the visitor's question. No numbering.

Respond with ONLY this JSON (no other text):
{{"questions": ["...", "...", "..."]}}"""


def chip_label(text: str) -> str:
    """A chip label: whitespace collapsed, at most 20 characters, cut on a word."""
    clean = " ".join(text.split())
    if len(clean) <= MAX_CHIP_LABEL:
        return clean
    head = clean[: MAX_CHIP_LABEL - 1]
    if " " in head:
        head = head[: head.rfind(" ")]
    return head.rstrip(" ,.;:-") + "…"


def _norm(message: str) -> str:
    return re.sub(r"\s+", " ", message.strip().lower())


def topic_quick_replies(config: AssistantConfig, topic_id: str | None) -> list[QuickReply]:
    """The owner's quick replies of the topic this turn was about."""
    if topic_id is None:
        return []
    for topic in config.scope.topics:
        if topic.id == topic_id:
            return list(topic.quick_replies)
    return []


def decline_pivot_chips(config: AssistantConfig) -> list[QuickReply]:
    """ "I can help with ..." chips after an off-topic decline: one per allow-list topic."""
    chips: list[QuickReply] = []
    for topic in config.scope.topics[:MAX_CHIPS]:
        message = topic.examples[0] if topic.examples else f"Tell me about {topic.name}"
        chips.append(QuickReply(label=chip_label(topic.name), message=message[:MAX_CHIP_MESSAGE]))
    return chips


def merge_quick_replies(*groups: Sequence[QuickReply], limit: int = MAX_CHIPS) -> list[QuickReply]:
    """Groups in priority order, de-duplicated on the message, at most `limit`."""
    seen: set[str] = set()
    out: list[QuickReply] = []
    for group in groups:
        for reply in group:
            key = _norm(reply.message)
            if key in seen:
                continue
            seen.add(key)
            out.append(reply)
            if len(out) == limit:
                return out
    return out


def _keywords(text: str) -> set[str]:
    return {w for w in tokenize(text) if w not in _STOP and len(w) > 2}


# Words a visitor uses for the same thing a heading names (one concept per group). Light on
# purpose: it only decides whether a "Tell me about <heading>" chip repeats the question.
_CONCEPTS: Final = {
    word: group[0]
    for group in (
        ("deliver", "delivery", "ship", "dispatch", "courier", "डिलीवरी"),
        ("return", "refund", "exchange", "wapas", "वापसी", "रिफंड"),
        ("price", "pricing", "cost", "fee", "charge", "rate", "कीमत", "शुल्क", "फीस"),
        ("hour", "timing", "opening", "open", "schedule", "समय"),
        ("location", "address", "direction", "पता"),
        ("booking", "book", "appointment", "reservation", "reserve", "बुकिंग"),
        ("payment", "pay", "upi", "emi", "भुगतान"),
        ("contact", "phone", "whatsapp", "email"),
    )
    for word in group
}


def _stem(word: str) -> str:
    """Plural and -ing/-ed endings off ("returns", "returned", "shipping" -> "return", "ship")."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    for suffix in ("ing", "ed"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            base = word[: -len(suffix)]
            return base[:-1] if len(base) > 2 and base[-1] == base[-2] else base
    if len(word) > 4 and word.endswith(("ses", "xes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _concepts(text: str) -> set[str]:
    return {_CONCEPTS.get(_stem(w), _stem(w)) for w in _keywords(text)}


def _repeats_question(title: str, asked: set[str]) -> bool:
    """The heading's main subject is what the visitor just asked about: a short heading
    sharing a concept with the question, or a longer one sharing at least half of its."""
    heading = _concepts(title)
    shared = heading & asked
    return bool(shared) and (len(heading) <= 2 or 2 * len(shared) >= len(heading))


def topic_keyword_match(question: str, config: AssistantConfig) -> bool:
    """True when the question shares a content word with an allow-list topic."""
    words = _keywords(question)
    if not words:
        return False
    for topic in config.scope.topics:
        vocab = _keywords(" ".join([topic.name, topic.description, *topic.examples]))
        if words & vocab:
            return True
    return False


def _as_json_object(content: str) -> dict[str, Any]:
    parsed = json.loads(content)
    if not isinstance(parsed, dict):
        raise TypeError(f"expected a JSON object, got {type(parsed).__name__}")
    return parsed


async def generate_followups(
    llm: LLMProvider,
    *,
    question: str,
    answer: str,
    sections: list[str],
    topics: list[str],
    language: str,
) -> list[str]:
    """Three candidate follow-ups from the fast model; [] on any bad reply.

    The call goes through the director's own provider so it lands in the turn's cost
    ledger. No `max_tokens`: the fast model may be a reasoning model.
    """
    prompt = FOLLOWUP_PROMPT.format(
        question=sanitize_for_prompt_literal(question)[:500],
        answer=sanitize_for_prompt_literal(answer)[:1500],
        sections=", ".join(s for s in sections if s)[:500] or "(none)",
        topics=", ".join(topics)[:800] or "(none)",
        language=language,
    )
    try:
        response = await llm.complete_json(
            [LLMMessage(role="user", content=prompt)], **llm.fast_call_options()
        )
        raw = _as_json_object(response.content).get("questions", [])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.info("Follow-up suggestions skipped: bad JSON (%s)", exc)
        return []
    if not isinstance(raw, list):
        return []
    out = [" ".join(str(q).split())[:MAX_CHIP_MESSAGE] for q in raw if str(q).strip()]
    return out[:3]


async def filter_followups(
    candidates: list[str],
    config: AssistantConfig,
    probe: ProbeFn | None,
    timeout_s: float | None = None,
) -> list[str]:
    """Keep a candidate that names an allow-list topic, or whose dense probe passes the gate.

    `timeout_s` bounds the probes only: when they run out of time the candidates that already
    named a topic are kept (they need no probe), never nothing.
    """
    keyword_ok = [c for c in candidates if topic_keyword_match(c, config)]
    rest = [c for c in candidates if c not in keyword_ok][:MAX_PROBES]
    probed: set[str] = set()
    if probe is not None and rest and (timeout_s is None or timeout_s > 0):
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(probe(c) for c in rest), return_exceptions=True), timeout_s
            )
            probed = {c for c, ok in zip(rest, results, strict=True) if ok is True}
        except TimeoutError:
            logger.info("Follow-up probes ran out of time; keeping topic-named candidates")
    return [c for c in candidates if c in keyword_ok or c in probed]


_TELL_ME_ABOUT: Final[dict[str, str]] = {
    "en": "Tell me about {name}",
    "hi": "{name} के बारे में बताइए",
    "hi-Latn": "{name} ke baare mein batayein",
}


def fallback_followups(
    config: AssistantConfig,
    *,
    topic_id: str | None = None,
    question: str = "",
    language: str = "en",
) -> list[QuickReply]:
    """Deterministic topic chips when the model is too slow or nothing it wrote passed the
    allow-list: one per other topic, built from the owner's own topic text. No model call."""
    asked = _keywords(question)
    chips: list[QuickReply] = []
    for topic in config.scope.topics:
        if topic.id == topic_id or (asked and asked & _keywords(topic.name)):
            continue
        message = (
            topic.examples[0]
            if topic.examples
            else _TELL_ME_ABOUT.get(language, _TELL_ME_ABOUT["en"]).format(name=topic.name)
        )
        chips.append(QuickReply(label=chip_label(topic.name), message=message[:MAX_CHIP_MESSAGE]))
        if len(chips) == MAX_PROBES:
            break
    return chips


def _source_title(source: dict[str, Any]) -> str:
    """The cited section heading, else the document's file name made readable."""
    heading = " ".join(str(source.get("section") or "").split())
    if heading:
        return heading
    stem = str(source.get("file_name") or "").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def source_followups(
    sources: Sequence[dict[str, Any]], *, question: str = "", language: str = "en"
) -> list[QuickReply]:
    """Deterministic chips from the answer's own sources, for a knowledge base with no topics.

    One chip per distinct section heading (else document title), phrased "Tell me about ...".
    The heading came from a document the knowledge base holds, so it is answerable by
    construction. A heading whose subject the visitor just asked about is skipped, by stem
    and a small synonym list ("How long does delivery take?" gets no "Shipping" chip).
    No model call.
    """
    asked = _concepts(question)
    template = _TELL_ME_ABOUT.get(language, _TELL_ME_ABOUT["en"])
    seen: set[str] = set()
    chips: list[QuickReply] = []
    for source in sources:
        title = _source_title(source)
        key = title.lower()
        if not title or key in seen or _repeats_question(title, asked):
            continue
        seen.add(key)
        message = template.format(name=title)[:MAX_CHIP_MESSAGE]
        chips.append(QuickReply(label=chip_label(title), message=message))
        if len(chips) == MAX_PROBES:
            break
    return chips


async def ai_followups(
    llm: LLMProvider | None,
    probe: ProbeFn | None,
    *,
    config: AssistantConfig,
    question: str,
    answer: str,
    sections: list[str],
    language: str,
    topic_id: str | None = None,
    budget_s: float = FOLLOWUP_BUDGET_S,
    sources: Sequence[dict[str, Any]] = (),
) -> list[QuickReply]:
    """Allow-list-safe AI follow-up chips within the latency budget. Never raises.

    Light path: one fast-model call at low effort (`fast_call_options`) and probes that run
    no HyDE, classification, translation, re-rank or MMR. When the model or the probes
    cannot finish in `budget_s`, or nothing it wrote passes the allow-list, deterministic
    topic chips take over, and with no topics configured chips built from the answer's own
    `sources`, so chips appear on every grounded answer (unless the owner turned follow-ups off).

    A call cancelled by the budget is unmetered (the provider records cost on completion);
    that is one small fast-model call and is accepted.
    """
    if not config.conversation.ai_followups or llm is None or not llm.has_credentials:
        return []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_s
    kept: list[str] = []
    try:
        candidates = await asyncio.wait_for(
            generate_followups(
                llm,
                question=question,
                answer=answer,
                sections=sections,
                topics=[t.name for t in config.scope.topics],
                language=language,
            ),
            timeout=budget_s,
        )
        kept = await filter_followups(
            candidates, config, probe, timeout_s=max(deadline - loop.time(), 0.0)
        )
    except Exception as exc:  # timeout, provider error: fall back to topic chips
        logger.info("Follow-up suggestions fell back to topics: %s", type(exc).__name__)
    if kept:
        return [QuickReply(label=chip_label(q), message=q) for q in kept]
    fallback = fallback_followups(config, topic_id=topic_id, question=question, language=language)
    return fallback or source_followups(sources, question=question, language=language)


# ---- call to action ------------------------------------------------------------------------

CTA_LABELS: Final[dict[str, dict[str, str]]] = {
    "whatsapp": {
        "en": "Chat on WhatsApp",
        "hi": "WhatsApp पर बात करें",
        "hi-Latn": "WhatsApp par baat karein",
    },
    "call": {
        "en": "Call us",
        "hi": "कॉल करें",
        "hi-Latn": "Call karein",
    },
    "booking": {
        "en": "Book a slot",
        "hi": "समय बुक करें",
        "hi-Latn": "Slot book karein",
    },
    "handoff": {
        "en": "Talk to a person",
        "hi": "किसी व्यक्ति से बात करें",
        "hi-Latn": "Kisi insaan se baat karein",
    },
    "link": {
        "en": "Learn more",
        "hi": "और जानें",
        "hi-Latn": "Aur jaanein",
    },
}
_SALES_ROUTES: Final = frozenset({Route.GROUNDED, Route.VERBATIM})
_NO_ANSWER_ORDER: Final = ("whatsapp", "call")


def _label(kind: str, language: str) -> str:
    return localized(CTA_LABELS[kind], language)


def _handoff_reachable(config: AssistantConfig, office: OfficeState) -> bool:
    """The handoff card would show at least one channel (C5's own rules)."""
    return bool(build_channels(config, office, question="", visitor_name=None, language="en"))


def _channel_link(
    config: AssistantConfig, kind: str, office: OfficeState, *, prefill: str, language: str
) -> str | None:
    """The link of the owner's first usable handoff channel of `kind`. A kind the owner never
    listed is decided by the business card alone; one that is listed but disabled, closed
    right now or without a usable value gives none, so a button never shows what the card
    would hide."""
    if any(c.kind == kind for c in config.handoff.channels):
        for entry in build_channels(
            config, office, question=prefill, visitor_name=None, language=language
        ):
            if entry.kind == kind:
                return entry.url
        return None
    return channel_url(
        HandoffChannel(kind=kind),
        config,
        question=prefill,
        visitor_name=None,
    )


def build_cta(
    config: AssistantConfig,
    cta_type: str,
    *,
    language: str,
    prefill: str,
    office: OfficeState = OfficeState.UNKNOWN,
) -> "Cta | None":
    """The CTA for `cta_type`, or None when this knowledge base has no target for it now.

    Every URL comes from the owner's configuration or the two builders above, never from
    model text. WhatsApp, call and booking follow the owner's handoff channels (override
    value, enabled, open hours) before the business card.
    """
    # Deferred: `api.schemas.assistant` imports this package while it loads.
    from callobuzz_rag.api.schemas.assistant import Cta

    if cta_type in ("whatsapp", "call", "booking"):
        url = _channel_link(config, cta_type, office, prefill=prefill, language=language)
        if url is None:
            return None
        return Cta(type=cta_type, label=_label(cta_type, language), url=url)
    if cta_type == "link" and config.conversation.cta.link_url:
        owner = config.conversation.cta.link_label or {}
        label = localized(owner, language) or _label("link", language)
        return Cta(type="link", label=label[:40], url=config.conversation.cta.link_url)
    if cta_type == "handoff" and config.handoff.enabled and _handoff_reachable(config, office):
        return Cta(type="handoff", label=_label("handoff", language), url=None)
    return None  # "lead_form" is C4's form, "none" is none, a missing target is skipped


def choose_cta(
    config: AssistantConfig,
    *,
    label: TurnLabel,
    route: Route,
    state: ConversationState,
    ui_shown: bool,
    language: str,
    prefill: str,
    fallback_ui_missing: bool,
    office: OfficeState = OfficeState.UNKNOWN,
    topic_id: str | None = None,
) -> "Cta | None":
    """At most one CTA, never a declined type, never next to a lead form or handoff card,
    and never nagging a visitor who closed the lead form on a lead-trigger topic."""
    policy = config.conversation.cta
    if policy.max_per_answer == 0 or ui_shown:
        return None
    if topic_id is not None and state.lead_dismissed and topic_id in config.leads.triggers.topics:
        return None
    if label == TurnLabel.BUYING_INTENT and route in _SALES_ROUTES:
        kind = policy.buying_intent_cta
        if kind in ("none", "lead_form") or kind in state.cta_declined:
            return None
        return build_cta(config, kind, language=language, prefill=prefill, office=office)
    if route == Route.NO_ANSWER and fallback_ui_missing:
        wanted = config.scope.fallback.actions
        if "handoff" not in wanted and "lead" not in wanted:
            return None
        for kind in _NO_ANSWER_ORDER:
            if kind in state.cta_declined:
                continue
            cta = build_cta(config, kind, language=language, prefill=prefill, office=office)
            if cta is not None:
                return cta
    return None


# ---- the two offer providers ---------------------------------------------------------------

_CHIP_ROUTES: Final = frozenset({Route.GROUNDED, Route.VERBATIM, Route.EXPLAINER})


class QuickRepliesProvider:
    """Up to four chips: the topic's quick replies, then allow-list-safe AI follow-ups.

    Never after an abuse warning (it must not invite more turns); a decline pivots to the
    topics the assistant can help with; a greeting shows the owner's starters again.
    """

    kind = OfferKind.QUICK_REPLIES

    def wanted(self, ctx: OfferContext) -> str | None:
        route = ctx.signals.route
        if route == Route.DECLINE:
            return "decline_pivot" if ctx.config.scope.topics else None
        if route in _CHIP_ROUTES:
            return "answer"
        if route == Route.SMALLTALK:
            return "starters" if ctx.config.conversation.starters else None
        if route == Route.NO_ANSWER and topic_quick_replies(ctx.config, ctx.signals.topic_id):
            return "no_answer_topic"
        return None

    async def build(self, ctx: OfferContext, reason: str) -> list[QuickReply]:
        if reason == "decline_pivot":
            return decline_pivot_chips(ctx.config)
        if reason == "starters":
            return merge_quick_replies(ctx.config.conversation.starters)
        manual = topic_quick_replies(ctx.config, ctx.signals.topic_id)
        if reason != "answer":
            return merge_quick_replies(manual)
        ai = await ai_followups(
            ctx.llm,
            ctx.probe,
            config=ctx.config,
            question=ctx.question,
            answer=ctx.answer_text,
            sections=[str(s.get("section") or "") for s in ctx.cited_sources],
            language=ctx.language,
            topic_id=ctx.signals.topic_id,
            sources=ctx.cited_sources,
        )
        return merge_quick_replies(manual, ai)


class CtaProvider:
    """At most one call-to-action button, only where the visitor showed buying intent (or
    a fallback with no form or card to catch them), never a dismissed type."""

    kind = OfferKind.CTA

    def wanted(self, ctx: OfferContext) -> str | None:
        if ctx.ui_shown or ctx.config.conversation.cta.max_per_answer == 0:
            return None
        if ctx.signals.label == TurnLabel.BUYING_INTENT and ctx.signals.route in _SALES_ROUTES:
            return "buying_intent"
        if ctx.signals.route == Route.NO_ANSWER and ctx.ui_missing_for_fallback:
            return "no_answer"
        return None

    async def build(self, ctx: OfferContext, reason: str) -> "Cta | None":
        cta = choose_cta(
            ctx.config,
            label=ctx.signals.label,
            route=ctx.signals.route,
            state=ctx.state,
            ui_shown=ctx.ui_shown,
            language=ctx.language,
            prefill=ctx.question,
            fallback_ui_missing=ctx.ui_missing_for_fallback,
            office=ctx.signals.office,
            topic_id=ctx.signals.topic_id,
        )
        if cta is not None:
            ctx.state.record_cta_shown(cta.type)
        return cta


def register_reply_providers(reg: OfferRegistry = registry) -> None:
    """Register both providers once (safe to call again)."""
    if not reg.providers(OfferKind.QUICK_REPLIES):
        reg.register(QuickRepliesProvider())
    if not reg.providers(OfferKind.CTA):
        reg.register(CtaProvider())


register_reply_providers()
