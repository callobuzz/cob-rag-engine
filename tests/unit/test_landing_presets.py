"""LP2 T1: the six industry presets, in English, Hindi and Hinglish."""

import re

import pytest

from callobuzz_rag.landing.copy_rules import copy_warnings, copy_warnings_in_text
from callobuzz_rag.landing.presets import (
    PRESET_KEYS,
    PresetNotFoundError,
    load_preset,
    preset_site,
)
from callobuzz_rag.landing.schema import ScriptedConversation

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
KEYS = ["clinic", "store", "coaching", "real_estate", "salon", "services"]


def test_the_six_presets_exist_in_three_languages() -> None:
    assert PRESET_KEYS == ("clinic", "store", "coaching", "real_estate", "salon", "services")
    for key in PRESET_KEYS:
        assert set(load_preset(key).languages) == {"en", "hi", "hi-Latn"}


def test_an_unknown_preset_is_refused() -> None:
    with pytest.raises(PresetNotFoundError):
        load_preset("nope")


@pytest.mark.parametrize("key", KEYS)
def test_every_preset_conversation_is_valid_and_ends_with_a_source(key: str) -> None:
    preset = load_preset(key)
    for lang in ("en", "hi", "hi-Latn"):
        conv = ScriptedConversation.model_validate(preset.conversation(lang).model_dump())
        assistant = [t for t in conv.turns if t.role == "assistant"]
        assert assistant and assistant[-1].source is not None


@pytest.mark.parametrize("key", KEYS)
def test_one_turn_says_it_does_not_know_and_offers_the_team(key: str) -> None:
    conv = load_preset(key).conversation("en")
    text = conv.turns[-1].text["en"].casefold()
    assert "not see" in text and "share your number" in text


@pytest.mark.parametrize("key", KEYS)
def test_scripts_match_their_language(key: str) -> None:
    preset = load_preset(key)
    for text in preset.texts("hi"):
        assert DEVANAGARI.search(text), text
    for text in preset.texts("hi-Latn"):
        assert not DEVANAGARI.search(text), text


@pytest.mark.parametrize("key", KEYS)
def test_presets_follow_the_copy_rules_and_use_rupees(key: str) -> None:
    preset = load_preset(key)
    for lang in ("en", "hi", "hi-Latn"):
        for text in preset.texts(lang):
            assert not copy_warnings_in_text(text), (key, lang, text)
            assert "$" not in text
        assert any("₹" in t for t in preset.texts(lang)) or key == "salon"


def test_lead_phones_are_masked() -> None:
    for key in PRESET_KEYS:
        lead = load_preset(key).conversation("en").lead
        assert lead is not None
        assert "•" in lead.phone_masked and not re.search(r"\d{6,}", lead.phone_masked)


@pytest.mark.parametrize("key", KEYS)
def test_a_preset_site_is_valid_in_all_languages_and_has_no_warnings(key: str) -> None:
    site = preset_site(key)
    assert site.languages == ["en", "hi", "hi-Latn"]
    assert copy_warnings(site) == []
    hero = next(s for s in site.home if s.type == "hero")
    assert hero.conversation.preset == key
    assert set(hero.conversation.turns[0].text) == {"en", "hi", "hi-Latn"}
