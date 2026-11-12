"""AssistantConfig v1: defaults, limits, strictness, localisation and the version hook."""

import pytest
from pydantic import ValidationError

from callobuzz_rag.conversation.schema import (
    CURRENT_SCHEMA_VERSION,
    AssistantConfig,
    fill_persona,
    load_config,
    localized,
)
from callobuzz_rag.conversation.types import LanguageInfo, Script, TurnLabel, script_of
from callobuzz_rag.core.exceptions import AssistantSchemaTooNewError


def test_defaults_match_the_design() -> None:
    c = AssistantConfig()
    assert c.schema_version == 1 == CURRENT_SCHEMA_VERSION
    assert c.enabled is True
    assert c.scope.scope_mode == "docs_plus_domain"
    assert c.leads.phone == "required" and c.leads.email == "optional"
    assert c.leads.consent.retention_days == 365
    assert c.identity.card.timezone == "Asia/Kolkata"
    assert c.identity.ai_disclosure is True
    assert c.voice.format.tables == "when_comparing"
    assert c.scope.claim_check == "enforce"
    assert c.language.supported == ["en", "hi", "hi-Latn"]


def test_unknown_fields_are_refused_at_every_level() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"persona": {}})
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"voice": {"tone": "friendly"}})


def test_limits() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"identity": {"business_profile": "x" * 501}})
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate(
            {"conversation": {"starters": [{"label": f"s{i}", "message": "m"} for i in range(5)]}}
        )
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate(
            {"conversation": {"starters": [{"label": "x" * 21, "message": "m"}]}}
        )
    with pytest.raises(ValidationError):  # at most 6 custom lead fields
        AssistantConfig.model_validate(
            {
                "leads": {
                    "custom_fields": [
                        {"id": f"f{i}", "label": {"en": "x"}, "type": "text"} for i in range(7)
                    ]
                }
            }
        )


def test_extra_instructions_hold_a_legacy_system_prompt() -> None:
    long = "x" * 15_000
    assert (
        AssistantConfig.model_validate(
            {"advanced": {"extra_instructions": long}}
        ).advanced.extra_instructions
        == long
    )
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"advanced": {"extra_instructions": long + "y"}})


def test_phone_or_email_must_be_required() -> None:
    with pytest.raises(ValidationError, match="phone or email"):
        AssistantConfig.model_validate({"leads": {"phone": "optional", "email": "optional"}})
    ok = AssistantConfig.model_validate({"leads": {"phone": "off", "email": "required"}})
    assert ok.leads.email == "required"


def test_card_phone_is_normalised_to_e164() -> None:
    c = AssistantConfig.model_validate(
        {"identity": {"card": {"phone": "098220 12345", "whatsapp": "+91-98220-12345"}}}
    )
    assert c.identity.card.phone == "+919822012345"
    assert c.identity.card.whatsapp == "+919822012345"
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"identity": {"card": {"phone": "12345"}}})


def test_hours_and_timezone_are_validated() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"identity": {"card": {"timezone": "Mars/Base"}}})
    with pytest.raises(ValidationError):  # equal times are nothing
        AssistantConfig.model_validate(
            {"identity": {"card": {"hours": [{"day": "mon", "open": "09:00", "close": "09:00"}]}}}
        )
    with pytest.raises(ValidationError):  # not HH:MM
        AssistantConfig.model_validate(
            {"identity": {"card": {"hours": [{"day": "mon", "open": "9am", "close": "17:00"}]}}}
        )
    c = AssistantConfig.model_validate(
        {
            "identity": {
                "card": {
                    "hours": [
                        {"day": "mon", "open": "09:00", "close": "13:00"},
                        {"day": "mon", "open": "16:00", "close": "20:30"},
                    ]
                }
            }
        }
    )
    assert len(c.identity.card.hours) == 2


def test_overnight_hours_are_allowed() -> None:
    c = AssistantConfig.model_validate(
        {"identity": {"card": {"hours": [{"day": "fri", "open": "18:00", "close": "02:00"}]}}}
    )
    assert c.identity.card.hours[0].close == "02:00"


def test_urls_must_be_http() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"identity": {"card": {"website": "javascript:alert(1)"}}})
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate({"identity": {"avatar_url": "http://x.example/a.png"}})


def test_select_field_needs_options() -> None:
    with pytest.raises(ValidationError):
        AssistantConfig.model_validate(
            {
                "leads": {
                    "custom_fields": [{"id": "course", "label": {"en": "Course"}, "type": "select"}]
                }
            }
        )


def test_localized_falls_back_to_english() -> None:
    text = {"en": "Hello", "hi": "नमस्ते"}
    assert localized(text, "hi") == "नमस्ते"
    assert localized(text, "hi-Latn") == "Hello"
    assert localized({}, "en") == ""


def test_fill_persona_only_fills_known_names() -> None:
    c = AssistantConfig.model_validate(
        {"identity": {"assistant_name": "Asha", "business_name": "Shree Labs"}}
    )
    assert fill_persona("I'm {assistant_name} of {business_name} {other}", c) == (
        "I'm Asha of Shree Labs {other}"
    )


def test_load_config_runs_and_refuses_versions() -> None:
    assert load_config({}).schema_version == 1
    with pytest.raises(AssistantSchemaTooNewError):
        load_config({"schema_version": 2})


def test_types() -> None:
    assert [label.value for label in TurnLabel] == [
        "smalltalk",
        "business_question",
        "domain_explainer",
        "off_topic",
        "buying_intent",
        "human_request",
        "abuse",
        "other",
    ]
    assert script_of("hi-Latn") is Script.LATIN
    assert script_of("mr") is Script.DEVANAGARI
    assert LanguageInfo("en", Script.LATIN, 1.0).code == "en"
