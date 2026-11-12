"""Setup generation (C8): template + proposal + answers -> a valid AssistantConfig with provenance."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.setup.generate import generate_config
from callobuzz_rag.conversation.store import publish_problems
from callobuzz_rag.conversation.templates import load_template
from callobuzz_rag.llm.models import LLMResponse

PROPOSAL = {
    "business_type": "clinic",
    "business_name": "Shree Labs",
    "services": ["Blood tests"],
    "hours": [{"day": "mon", "open": "07:00", "close": "20:00"}],
    "phone": "+919822012345",
    "whatsapp": "+919822012345",
    "email": "care@shreelabs.in",
    "address": "Kothrud, Pune",
    "languages_seen": ["en"],
}
ANSWERS = {
    "audience": "patients",
    "tone": "warm",
    "languages": ["en", "hi-Latn"],
    "never_do": ["diagnose", {"other": "Never share staff phone numbers"}],
    "when_unsure": "whatsapp",
    "top_services": ["Blood tests", "Home sample collection", "Health packages"],
    "lead_followup": "same_day",
    "exact_answers": ["prices"],
    "assistant_name": "Asha",
    "greeting_style": "welcome_with_services",
}


def _llm(payload: Any = None, *, error: Exception | None = None) -> MagicMock:
    llm = MagicMock()
    llm.has_credentials = True
    llm.fast_call_options.return_value = {"model": "fast"}
    if error is not None:
        llm.complete_json = AsyncMock(side_effect=error)
    else:
        llm.complete_json = AsyncMock(
            return_value=LLMResponse(content=json.dumps(payload), model="fast")
        )
    return llm


async def _gen(business_type: str = "clinic", **kw: Any) -> Any:
    args: dict[str, Any] = {
        "business_type": business_type,
        "proposal": PROPOSAL,
        "answers": ANSWERS,
        "llm": None,
    }
    args.update(kw)
    return await generate_config(**args)


async def test_template_wording_without_model() -> None:
    result = await _gen()
    cfg = result.config
    assert isinstance(cfg, AssistantConfig)
    assert cfg.identity.business_name == "Shree Labs"
    assert cfg.identity.assistant_name == "Asha"
    assert cfg.identity.card.phone == "+919822012345"
    assert cfg.voice.tone_preset == "warm"
    assert cfg.language.supported == ["en", "hi-Latn"] and cfg.language.primary == "en"
    assert [t.name for t in cfg.scope.topics][:3] == [
        "Blood tests",
        "Home sample collection",
        "Health packages",
    ]
    assert cfg.scope.sensitive_profile == "medical"  # clinic template fixes it
    assert cfg.conversation.cta.buying_intent_cta == "whatsapp"
    ids = {r.id for r in cfg.rules}
    assert {"setup-never-diagnose", "setup-never-other", "setup-exact-prices"} <= ids
    assert result.provenance["identity.business_name"] == "crawl"
    assert result.provenance["voice.tone_preset"] == "q:tone"
    assert result.provenance["rules.setup-never-diagnose"] == "q:never_do"
    assert result.provenance["scope.sensitive_profile"] == "template"
    assert result.wording_used == "template"


async def test_answer_hours_override_proposal() -> None:
    answers = {**ANSWERS, "hours": [{"day": "sat", "open": "08:00", "close": "14:00"}]}
    result = await _gen(answers=answers)
    assert [h.day for h in result.config.identity.card.hours] == ["sat"]
    assert result.provenance["identity.card.hours"] == "q:hours"


async def test_confirmed_profile_beats_answers_and_proposal() -> None:
    answers = {**ANSWERS, "hours": [{"day": "sat", "open": "08:00", "close": "14:00"}]}
    profile = {
        "business_name": "Shree Diagnostics",
        "phone": "+912012345678",
        "hours": [{"day": "sun", "open": "09:00", "close": "12:00"}],
        "website": "https://shreelabs.in",
    }
    result = await _gen(answers=answers, profile=profile)
    card = result.config.identity.card
    assert result.config.identity.business_name == "Shree Diagnostics"
    assert card.phone == "+912012345678" and card.whatsapp == "+919822012345"
    assert [h.day for h in card.hours] == ["sun"]
    assert card.website == "https://shreelabs.in"
    assert result.provenance["identity.business_name"] == "confirmed"
    assert result.provenance["identity.card.hours"] == "confirmed"


async def test_website_url_fills_the_card_when_nothing_else_does() -> None:
    result = await _gen(website_url="https://shreelabs.in/")
    assert result.config.identity.card.website == "https://shreelabs.in/"


async def test_no_leads_disables_leads() -> None:
    result = await _gen("store", proposal=None, answers={**ANSWERS, "lead_followup": "no_leads"})
    assert result.config.leads.enabled is False


async def test_leads_stay_off_without_an_encryption_key() -> None:
    result = await _gen(encryption_available=False)
    assert result.config.leads.enabled is False
    assert "leads_need_encryption_key" in result.warnings
    assert publish_problems(result.config, encryption_available=False, prompt_budget=10_000) is None


async def test_leads_on_with_an_encryption_key() -> None:
    result = await _gen(encryption_available=True)
    assert result.config.leads.enabled is True
    assert "leads_need_encryption_key" not in result.warnings


async def test_missing_contact_for_the_chosen_channel_is_a_warning() -> None:
    proposal = {**PROPOSAL, "whatsapp": None}
    result = await _gen(proposal=proposal)
    assert "contact_missing:whatsapp" in result.warnings
    result = await _gen(proposal=proposal, profile={"whatsapp": "+919811122233"})
    assert "contact_missing:whatsapp" not in result.warnings


async def test_form_without_leads_is_a_warning() -> None:
    answers = {**ANSWERS, "when_unsure": "form", "lead_followup": "no_leads"}
    result = await _gen(answers=answers, encryption_available=True)
    assert "lead_form_needs_leads" in result.warnings
    result = await _gen(answers={**ANSWERS, "when_unsure": "form"}, encryption_available=True)
    assert "lead_form_needs_leads" not in result.warnings
    result = await _gen(answers={**ANSWERS, "when_unsure": "form"}, encryption_available=False)
    assert "lead_form_needs_leads" in result.warnings


async def test_model_writes_wording_only() -> None:
    llm = _llm(
        {
            "topics": {
                "blood-tests": {
                    "description": "Tests on blood samples.",
                    "examples": ["CBC kab tak milega?"],
                },
            },
            "greeting": "Namaste! I'm {assistant_name} from {business_name}. Ask about tests.",
            "business_name": "Evil Corp",
        }
    )
    result = await _gen(llm=llm)
    topic = next(t for t in result.config.scope.topics if t.id == "blood-tests")
    assert topic.description == "Tests on blood samples."
    assert result.config.identity.business_name == "Shree Labs"  # never taken from the model
    assert result.config.voice.greeting["en"].startswith("Namaste! I'm {assistant_name}")
    assert result.wording_used == "ai"
    prompt = llm.complete_json.await_args.args[0][0].content
    assert "{assistant_name}" in prompt and "{business_name}" in prompt


async def test_model_failure_falls_back_to_template_wording() -> None:
    result = await _gen(llm=_llm(error=RuntimeError("provider down")))
    assert result.config.scope.topics[0].description == "Questions about Blood tests."
    assert result.wording_used == "template"


async def test_wording_cap_is_raised_for_a_capped_model() -> None:
    llm = _llm({})
    llm.fast_call_options.return_value = {"model": "fast", "max_tokens": 512}
    await _gen(llm=llm)
    assert llm.complete_json.await_args.kwargs["max_tokens"] >= 2000


async def test_devanagari_services_get_distinct_topic_ids() -> None:
    answers = {**ANSWERS, "top_services": ["रक्त जांच", "घर पर सैंपल"]}
    result = await _gen(answers=answers)
    ids = [t.id for t in result.config.scope.topics]
    assert len(ids) == len(set(ids)) and ids[:2] == ["topic", "topic-2"]


async def test_slug_collisions_and_long_names() -> None:
    long = "Comprehensive master health checkup package for "
    answers = {
        **ANSWERS,
        "top_services": ["Blood tests", "blood-tests", long + "adults", long + "seniors"],
    }
    result = await _gen(answers=answers)
    ids = [t.id for t in result.config.scope.topics]
    assert len(ids) == len(set(ids))
    assert ids[:2] == ["blood-tests", "blood-tests-2"]
    assert all(len(i) <= 40 for i in ids)


async def test_service_named_like_a_blocked_topic_is_suffixed() -> None:
    blocked = load_template("clinic").config.scope.blocked_topics[0].id
    answers = {**ANSWERS, "top_services": [blocked.replace("-", " ")]}
    result = await _gen(answers=answers)
    ids = [t.id for t in result.config.scope.topics]
    assert f"{blocked}-service" in ids and blocked not in ids


async def test_service_named_like_a_template_topic_replaces_it() -> None:
    template_topic = load_template("store").config.scope.topics[0]
    answers = {**ANSWERS, "top_services": [template_topic.name]}
    result = await _gen("store", proposal=None, answers=answers)
    ids = [t.id for t in result.config.scope.topics]
    assert ids.count(template_topic.id) == 1


async def test_two_other_answers_get_distinct_rule_ids() -> None:
    answers = {
        **ANSWERS,
        "never_do": ["diagnose", {"other": "a"}],
        "exact_answers": [{"other": "b"}],
    }
    ok = await _gen(answers=answers)
    assert {"setup-never-other", "setup-exact-other"} <= {r.id for r in ok.config.rules}
    # The interview refuses two, but generation must not crash on a stored pair either.
    twice = {**ANSWERS, "never_do": [{"other": "a"}, {"other": "b"}]}
    result = await _gen(answers=twice)
    ids = [r.id for r in result.config.rules]
    assert len(ids) == len(set(ids))
    assert "setup-never-other-2" in ids


async def test_the_owners_current_assistant_survives() -> None:
    template = load_template("clinic").config
    current = template.model_copy(deep=True)
    current.enabled = False
    current.advanced.extra_instructions = "Always mention our lab accreditation."
    current.identity.avatar_url = "https://shreelabs.in/asha.png"
    current.identity.card.booking_url = "https://shreelabs.in/book"
    current.identity.card.payment_info = "UPI and cards"
    current.leads.notify.emails = ["owner@shreelabs.in"]
    current.language.script_policy = "roman"
    result = await _gen(current=current)
    cfg = result.config
    assert cfg.enabled is False
    assert cfg.advanced.extra_instructions == "Always mention our lab accreditation."
    assert cfg.identity.avatar_url == "https://shreelabs.in/asha.png"
    assert cfg.identity.card.booking_url == "https://shreelabs.in/book"
    assert cfg.identity.card.payment_info == "UPI and cards"
    assert cfg.leads.notify.emails == ["owner@shreelabs.in"]
    assert cfg.language.script_policy == "roman"
    assert result.provenance["enabled"] == "existing"
    assert result.provenance["advanced"] == "existing"


async def test_existing_contact_is_a_fallback_for_the_crawl() -> None:
    current = load_template("clinic").config.model_copy(deep=True)
    current.identity.business_name = "Old Name"
    current.identity.card.address = "12 Park Road"
    result = await _gen(proposal={**PROPOSAL, "address": None, "business_name": None})
    assert result.config.identity.business_name != "Old Name"
    result = await _gen(
        current=current, proposal={**PROPOSAL, "address": None, "business_name": None}
    )
    assert result.config.identity.business_name == "Old Name"
    assert result.config.identity.card.address == "12 Park Road"


async def test_hindi_site_sets_the_documents_language() -> None:
    result = await _gen(proposal={**PROPOSAL, "languages_seen": ["hi"]})
    assert result.config.language.documents_language == "hi"
    assert result.provenance["language.documents_language"] == "crawl"
    result = await _gen(proposal=None)
    assert result.config.language.documents_language == "en"


async def test_a_missing_handoff_channel_is_added() -> None:
    result = await _gen()
    kinds = [c.kind for c in result.config.handoff.channels]
    assert kinds[0] == "whatsapp"
    assert all(c.enabled for c in result.config.handoff.channels[:1])
