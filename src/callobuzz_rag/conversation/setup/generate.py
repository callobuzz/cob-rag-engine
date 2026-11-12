"""Write a draft assistant from the template, the website proposal and the interview answers.

The mapping is deterministic (C8 Task 4 table) and records, for every config path it sets, where
the value came from: "template", "crawl", "confirmed" (the owner-confirmed profile), "existing"
(kept from the owner's current draft or published assistant) or "q:<question id>". The fast
model is used only for wording (topic descriptions and examples, and the greeting); nothing it
returns is used for business facts (names, numbers, contact details).

Precedence for facts: confirmed profile > interview answer (hours) > crawl proposal > the
existing config > the template.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, get_args

from callobuzz_rag.conversation.schema import (
    AssistantConfig,
    GuidanceRule,
    HandoffChannel,
    QuickReply,
    Topic,
)
from callobuzz_rag.conversation.setup.interview import load_questions
from callobuzz_rag.conversation.setup.llm_calls import WORDING_TOKENS, setup_call_options
from callobuzz_rag.conversation.types import LangKey
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.safety import sanitize_for_prompt_literal

logger = logging.getLogger(__name__)

NEVER_DO_RULES: dict[str, str] = {
    "diagnose": "Never diagnose a condition or suggest medicines or doses; suggest seeing a "
    "doctor.",
    "quote_unlisted_prices": "Never state a price or fee that is not written in the documents.",
    "promise_discounts": "Never promise discounts, offers or free services.",
    "legal_financial_advice": "Never give legal or financial advice; suggest a qualified "
    "professional.",
    "competitors": "Do not discuss or compare competitors.",
    "politics_religion": "Do not discuss politics or religion; steer back to how you can help.",
    "guarantee_results": "Never guarantee results, admissions, approvals or outcomes.",
}
EXACT_ANSWER_RULES: dict[str, str] = {
    "prices": "Prices and fees: quote them exactly as the documents state; if not stated, say "
    "you will confirm.",
    "timings": "Timings: quote them exactly from the documents or the business card.",
    "refunds": "Refunds and cancellations: quote the documented policy exactly; never improvise "
    "exceptions.",
    "eligibility": "Eligibility: answer only from the documents; if unclear, offer to connect to "
    "a person.",
    "documents_required": "Documents required: list exactly what the documents say.",
    "address": "Address and directions: use only the business card or the documents.",
}
FOLLOWUP_TEXT: dict[str, str] = {
    "within_hour": "Thank you! Our team will call you within an hour.",
    "same_day": "Thank you! Our team will contact you today.",
    "next_working_day": "Thank you! Our team will contact you on the next working day.",
    "no_leads": "Thank you!",
}
# when_unsure -> (handoff channel kind, buying-intent CTA, fallback actions, card field needed)
_UNSURE: dict[str, tuple[str, str, list[str], str | None]] = {
    "whatsapp": ("whatsapp", "whatsapp", ["clarify", "handoff", "lead"], "whatsapp"),
    "call": ("call", "call", ["clarify", "handoff", "lead"], "phone"),
    "form": ("callback", "lead_form", ["clarify", "lead", "handoff"], None),
    "email": ("email", "handoff", ["clarify", "handoff", "lead"], "email"),
}
_CARD_FIELDS = ("phone", "whatsapp", "email", "address")
# Paths the owner may already have filled; setup keeps them (provenance "existing").
_KEEP_TOP = ("enabled", "advanced")
_KEEP_PATHS = (
    "identity.avatar_url",
    "identity.card.timezone",
    "identity.card.holidays",
    "identity.card.booking_url",
    "identity.card.service_areas",
    "identity.card.payment_info",
    "identity.card.refund_info",
    "identity.card.hours",
    "identity.card.website",
    "identity.business_name",
    "leads.notify",
    "handoff.notify",
    "leads.consent.privacy_policy_url",
    "language.reply_policy",
    "language.script_policy",
    *(f"identity.card.{k}" for k in _CARD_FIELDS),
)
_MAX_SLUG = 36


@dataclass
class GenerationResult:
    config: AssistantConfig
    provenance: dict[str, str]
    wording_used: str = "template"  # "ai" | "template"
    warnings: list[str] = field(default_factory=list)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "topic")[:_MAX_SLUG].strip("-") or "topic"


def _unique(base: str, taken: set[str]) -> str:
    """`base`, else `base-2`, `base-3`... that is in no set of taken ids."""
    if base not in taken:
        return base
    n = 2
    while True:
        suffix = f"-{n}"
        candidate = f"{base[: 40 - len(suffix)]}{suffix}"
        if candidate not in taken:
            return candidate
        n += 1


def _label(question_id: str, value: Any) -> str:
    if isinstance(value, dict):
        return str(value["other"])
    for q in load_questions():
        if q.id == question_id:
            return next((o.label for o in q.options if o.value == value), str(value))
    return str(value)


def _get(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for key in path.split("."):
        node = node[key]
    return node


def _quoted(value: str) -> str:
    return json.dumps(sanitize_for_prompt_literal(value)[:200], ensure_ascii=False)


async def _wording(
    llm: LLMProvider | None, topics: list[Topic], business: str, assistant: str, style: str
) -> dict[str, Any]:
    if llm is None or not llm.has_credentials or not topics:
        return {}
    prompt = (
        "Write short wording for a website assistant. Do not invent prices, phone numbers, "
        "addresses, timings or any other facts. Keep the literal placeholders {assistant_name} "
        "and {business_name} in the greeting instead of writing the names. Reply with ONLY JSON: "
        '{"topics": {"<topic id>": {"description": "<=25 words>", "examples": '
        '["<question>", ...max 3]}}, "greeting": "<=35 words>"}.\n'
        f"Business: {_quoted(business)}\nAssistant name: {_quoted(assistant)}\n"
        f"Greeting style: {_quoted(style)}\n"
        "Topics: " + json.dumps({t.id: t.name for t in topics}, ensure_ascii=False)
    )
    try:
        response = await llm.complete_json(
            [LLMMessage(role="user", content=prompt)],
            **setup_call_options(llm, WORDING_TOKENS),
        )
        parsed = json.loads(response.content)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("Setup wording call failed; keeping template wording", exc_info=True)
        return {}


async def generate_config(
    *,
    business_type: str,
    proposal: dict[str, Any] | None,
    answers: dict[str, Any],
    llm: LLMProvider | None,
    profile: dict[str, Any] | None = None,
    current: AssistantConfig | None = None,
    encryption_available: bool = True,
    website_url: str | None = None,
) -> GenerationResult:
    from callobuzz_rag.conversation.templates import load_template

    base = load_template(business_type).config
    data: dict[str, Any] = base.model_dump(mode="json")
    prov: dict[str, str] = {}
    warnings: list[str] = []
    proposal = proposal or {}
    profile = profile or {}

    def put(path: str, value: Any, source: str) -> None:
        node: Any = data
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
        prov[path] = source

    # What the owner already has beats the bare template; the facts below beat both.
    if current is not None:
        existing = current.model_dump(mode="json")
        for top in _KEEP_TOP:
            if existing[top] != data[top]:
                put(top, existing[top], "existing")
        for path in _KEEP_PATHS:
            value = _get(existing, path)
            if value not in (None, "", []) and value != _get(data, path):
                put(path, value, "existing")

    if proposal.get("business_name"):
        put("identity.business_name", proposal["business_name"], "crawl")
    for key in _CARD_FIELDS:
        if proposal.get(key):
            put(f"identity.card.{key}", proposal[key], "crawl")
    if proposal.get("hours"):
        put("identity.card.hours", proposal["hours"], "crawl")
    languages_seen = proposal.get("languages_seen") or []
    if languages_seen and languages_seen[0] in get_args(LangKey):
        put("language.documents_language", languages_seen[0], "crawl")
    if answers.get("hours"):
        put("identity.card.hours", answers["hours"], "q:hours")

    # The owner-confirmed profile beats the proposal and the answers.
    if profile.get("business_name"):
        put("identity.business_name", profile["business_name"], "confirmed")
    for key in (*_CARD_FIELDS, "website"):
        if profile.get(key):
            put(f"identity.card.{key}", profile[key], "confirmed")
    if profile.get("hours"):
        put("identity.card.hours", profile["hours"], "confirmed")
    if not data["identity"]["card"]["website"] and website_url:
        put("identity.card.website", website_url, "crawl")

    if "audience" in answers:
        put("identity.audience", _label("audience", answers["audience"])[:200], "q:audience")
    if "tone" in answers:
        put("voice.tone_preset", answers["tone"], "q:tone")
    if answers.get("languages"):
        known = [v for v in answers["languages"] if v in get_args(LangKey)]
        if known:
            put("language.supported", known, "q:languages")
            put("language.primary", known[0], "q:languages")
    if "assistant_name" in answers:
        put("identity.assistant_name", answers["assistant_name"], "q:assistant_name")
    if "sensitive_profile" in answers:
        put("scope.sensitive_profile", answers["sensitive_profile"], "q:sensitive_profile")
    else:
        prov["scope.sensitive_profile"] = "template"

    card = data["identity"]["card"]
    if "when_unsure" in answers:
        channel, cta, actions, needs = _UNSURE[answers["when_unsure"]]
        put("scope.fallback.actions", actions, "q:when_unsure")
        put("conversation.cta.buying_intent_cta", cta, "q:when_unsure")
        channels: list[dict[str, Any]] = data["handoff"]["channels"]
        if not any(c["kind"] == channel for c in channels):
            channels.append(HandoffChannel(kind=channel).model_dump(mode="json"))
        first = [c for c in channels if c["kind"] == channel]
        rest = [c for c in channels if c["kind"] != channel]
        first[0]["enabled"] = True
        put("handoff.channels", first + rest, "q:when_unsure")
        if needs and not card.get(needs):
            warnings.append(f"contact_missing:{needs}")

    wants_leads = bool(data["leads"]["enabled"])
    if answers.get("lead_followup"):
        value = answers["lead_followup"]
        wants_leads = value != "no_leads"
        message = dict(data["leads"]["confirmation_message"])
        message["en"] = FOLLOWUP_TEXT[value]
        put("leads.confirmation_message", message, "q:lead_followup")
    if wants_leads and not encryption_available:
        # Publishing refuses leads without an encryption key (encryption_not_configured).
        wants_leads = False
        warnings.append("leads_need_encryption_key")
    if answers.get("lead_followup") or not wants_leads:
        put(
            "leads.enabled",
            wants_leads,
            "q:lead_followup" if answers.get("lead_followup") else "template",
        )

    if answers.get("when_unsure") == "form" and not wants_leads:
        # The buying-intent button and the fallback point at a lead form that cannot open.
        warnings.append("lead_form_needs_leads")

    rules = [r for r in data["rules"] if not str(r["id"]).startswith("setup-")]
    taken_rules = {str(r["id"]) for r in rules}

    def add_rule(prefix: str, key: str, rule_type: str, text: str, question: str) -> None:
        rule_id = _unique(f"{prefix}-{key}", taken_rules)
        taken_rules.add(rule_id)
        rule = GuidanceRule(
            id=rule_id,
            type=rule_type,
            instruction=text[:500],
            source="setup",
        )
        rules.append(rule.model_dump(mode="json"))
        prov[f"rules.{rule_id}"] = question

    seen: set[str] = set()
    for value in answers.get("never_do", []):
        key = "other" if isinstance(value, dict) else str(value)
        if key != "other" and key in seen:
            continue
        seen.add(key)
        add_rule(
            "setup-never",
            key,
            "compliance",
            str(value["other"]) if isinstance(value, dict) else NEVER_DO_RULES[key],
            "q:never_do",
        )
    seen = set()
    for value in answers.get("exact_answers", []):
        key = "other" if isinstance(value, dict) else str(value)
        if key != "other" and key in seen:
            continue
        seen.add(key)
        text = (
            f"{value['other']}: answer only from the documents, exactly."
            if isinstance(value, dict)
            else EXACT_ANSWER_RULES[key]
        )
        add_rule("setup-exact", key, "context", text, "q:exact_answers")
    data["rules"] = rules

    services: list[str] = answers.get("top_services") or proposal.get("services") or []
    template_ids = {t["id"] for t in data["scope"]["topics"]}
    blocked_ids = {b["id"] for b in data["scope"]["blocked_topics"]}
    used: set[str] = set()
    topics: list[Topic] = []
    for name in services[:10]:
        slug = _slug(name)
        if slug in blocked_ids:
            slug = f"{slug[:28]}-service".strip("-")
        if slug in used or slug in blocked_ids:
            slug = _unique(slug, used | blocked_ids | template_ids)
        used.add(slug)
        topics.append(
            Topic(
                id=slug, name=name[:60], description=f"Questions about {name}."[:300], examples=[]
            )
        )
    wording = await _wording(
        llm,
        topics,
        data["identity"]["business_name"] or "the business",
        data["identity"]["assistant_name"],
        str(answers.get("greeting_style", "short_welcome")),
    )
    worded: dict[str, Any] = wording["topics"] if isinstance(wording.get("topics"), dict) else {}
    for topic in topics:
        extra: dict[str, Any] = worded[topic.id] if isinstance(worded.get(topic.id), dict) else {}
        if isinstance(extra.get("description"), str) and extra["description"].strip():
            topic.description = extra["description"].strip()[:300]
        if isinstance(extra.get("examples"), list):
            topic.examples = [str(e)[:200] for e in extra["examples"][:3] if str(e).strip()]
    if topics:
        kept = [t for t in data["scope"]["topics"] if t["id"] not in used]
        put("scope.topics", [t.model_dump(mode="json") for t in topics] + kept, "q:top_services")
        starters = [
            QuickReply(label=t.name[:20], message=f"Tell me about {t.name}"[:200])
            for t in topics[:3]
        ]
        put(
            "conversation.starters", [s.model_dump(mode="json") for s in starters], "q:top_services"
        )

    if answers.get("greeting_style"):
        greeting = dict(data["voice"]["greeting"])
        model_text = wording.get("greeting")
        if isinstance(model_text, str) and model_text.strip():
            greeting["en"] = model_text.strip()[:300]
        put("voice.greeting", greeting, "q:greeting_style")

    for path in ("identity", "voice", "language", "scope", "conversation", "leads", "handoff"):
        prov.setdefault(path, "template")
    config = AssistantConfig.model_validate(data)
    return GenerationResult(
        config=config,
        provenance=prov,
        wording_used="ai" if wording else "template",
        warnings=warnings,
    )
