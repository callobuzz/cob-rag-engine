"""TV2: the consent notice says a summary and a reference code are shared (v2)."""

import re
import uuid

import pytest

from callobuzz_rag.conversation.leads.consent import (
    DEFAULT_NOTICES,
    consent_row,
    notice_for,
    notice_version_for,
)
from callobuzz_rag.conversation.schema import _NOTICE, _NOTICE_V1, AssistantConfig

LANGS = ("en", "hi", "hi-Latn")


def _config(notice: dict[str, str] | None = None, version: int = 1) -> AssistantConfig:
    raw = AssistantConfig().model_dump()
    raw["identity"]["business_name"] = "Shree Dental Clinic"
    raw["identity"]["card"]["email"] = "care@shreedental.in"
    if notice is not None:
        raw["leads"]["consent"]["notice"] = notice
    raw["leads"]["consent"]["notice_version"] = version
    return AssistantConfig.model_validate(raw)


def _row(config: AssistantConfig, lang: str):
    return consent_row(
        lead_id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        config=config,
        language=lang,
        notice_text=notice_for(config, lang),
        ip=None,
    )


def test_v2_is_v1_plus_one_sentence_in_every_language() -> None:
    for lang in LANGS:
        assert _NOTICE[lang] != _NOTICE_V1[lang]
        assert len(_NOTICE[lang]) <= 1500
        assert "{business_name}'s team" in _NOTICE["en"]
        # v1 is still all there, in order
        head = _NOTICE_V1[lang].partition(" ")[0]
        assert _NOTICE[lang].startswith(head)
    assert "summary" in _NOTICE["en"] and "reference code" in _NOTICE["en"]
    assert "summary" in _NOTICE["hi-Latn"] and "reference code" in _NOTICE["hi-Latn"]
    assert "संदर्भ कोड" in _NOTICE["hi"]


def test_a_new_config_defaults_to_v2_and_version_2() -> None:
    cfg = AssistantConfig()
    assert cfg.leads.consent.notice == DEFAULT_NOTICES == _NOTICE
    assert cfg.leads.consent.notice_version == 2


@pytest.mark.parametrize("lang", LANGS)
def test_an_unedited_v1_default_is_shown_as_v2(lang: str) -> None:
    cfg = _config(dict(_NOTICE_V1), version=1)
    shown = notice_for(cfg, lang)
    assert "reference code" in shown or "संदर्भ कोड" in shown
    assert "Shree Dental Clinic" in shown
    assert notice_version_for(cfg, lang) == 2
    assert _row(cfg, lang).notice_version == 2


def test_an_owner_edited_notice_is_left_alone() -> None:
    mine = {"en": "I agree for {business_name} to call me. {purpose}"}
    cfg = _config(mine, version=3)
    assert (
        notice_for(cfg, "en")
        == "I agree for Shree Dental Clinic to call me. To contact you about your enquiry."
    )
    assert notice_version_for(cfg, "en") == 3
    assert _row(cfg, "en").notice_version == 3


def test_a_mixed_config_upgrades_only_the_untouched_language() -> None:
    cfg = _config({"en": "My own words", "hi": _NOTICE_V1["hi"]}, version=1)
    assert notice_for(cfg, "en") == "My own words"
    assert notice_version_for(cfg, "en") == 1
    assert "संदर्भ कोड" in notice_for(cfg, "hi")
    assert notice_version_for(cfg, "hi") == 2


def test_the_placeholders_are_still_filled_one_by_one_on_v2() -> None:
    cfg = _config(version=2)
    text = notice_for(cfg, "en")
    for token in (
        "{business_name}",
        "{purpose}",
        "{retention_days}",
        "{contact}",
        "{privacy_link}",
    ):
        assert token not in text
    assert "care@shreedental.in" in text and "365 days" in text
    assert re.search(
        r"summary of this chat and a reference code go to Shree Dental Clinic's team", text
    )
    # braces typed by an owner stay as typed (never `str.format`)
    braces = _config({"en": "Share {weird} {business_name}"}, version=2)
    assert notice_for(braces, "en") == "Share {weird} Shree Dental Clinic"


def test_the_dashboards_recommended_wording_equals_the_engines_default() -> None:
    import json
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "services/dashboard/src/features/assistant/consentNotice.ts"
    ).read_text(encoding="utf-8")
    found = {
        m.group(1): json.loads(m.group(2))
        for m in re.finditer(r'^\t"?([\w-]+)"?:\s*("(?:[^"\\]|\\.)*"),$', source, re.MULTILINE)
    }
    assert found == dict(_NOTICE)
