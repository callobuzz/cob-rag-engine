"""LP1 T2: the landing content schema, the shipped default and the translation gaps."""

import pytest
from pydantic import ValidationError

from callobuzz_rag.landing.schema import (
    LandingContent,
    default_content,
    text_limits,
    translation_gaps,
)


def _content(**over: object) -> dict:
    data = default_content().model_dump(mode="json")
    data.update(over)
    return data


def _hero(data: dict) -> dict:
    return next(s for s in data["home"] if s["type"] == "hero")


def test_the_shipped_default_is_valid_and_has_the_required_sections() -> None:
    content = default_content()
    types = [s.type for s in content.home]
    assert types.count("hero") == 1 and types.count("final_cta") == 1
    assert content.languages == ["en"] and content.default_language == "en"


def test_the_default_survives_a_dump_and_load() -> None:
    data = default_content().model_dump(mode="json")
    assert LandingContent.model_validate(data).model_dump(mode="json") == data
    whatsapp = next(s for s in data["home"] if s["type"] == "whatsapp_journey")
    assert "from" in whatsapp["chapters"][0]["messages"][0]


def test_required_sections_cannot_be_switched_off() -> None:
    data = _content()
    _hero(data)["enabled"] = False
    with pytest.raises(ValidationError, match="hero"):
        LandingContent.model_validate(data)


def test_unknown_fields_and_types_are_refused() -> None:
    data = _content()
    data["home"][0]["onclick"] = "alert(1)"
    with pytest.raises(ValidationError):
        LandingContent.model_validate(data)
    data = _content()
    data["home"].append({"id": "x", "type": "iframe"})
    with pytest.raises(ValidationError):
        LandingContent.model_validate(data)


@pytest.mark.parametrize(
    "href", ["javascript:alert(1)", "//evil.example/x", "data:text/html,x", " /a", "http://x.in"]
)
def test_hrefs_are_site_relative_or_https(href: str) -> None:
    data = _content()
    _hero(data)["primary_href"] = href
    with pytest.raises(ValidationError):
        LandingContent.model_validate(data)


def test_good_hrefs_pass_and_contact_links_only_where_allowed() -> None:
    data = _content()
    _hero(data)["primary_href"] = "https://wa.me/919822012345"
    LandingContent.model_validate(data)
    data["nav"]["links"][0] = {"label": {"en": "Call"}, "href": "tel:+919822012345"}
    LandingContent.model_validate(data)
    data = _content()
    _hero(data)["primary_href"] = "mailto:hello@sharmadental.in"
    with pytest.raises(ValidationError):
        LandingContent.model_validate(data)


def test_text_limits_hold_per_language() -> None:
    data = _content()
    _hero(data)["headline"] = {"en": "x" * 61}
    with pytest.raises(ValidationError, match="60"):
        LandingContent.model_validate(data)
    data = _content(languages=["en", "hi"])
    _hero(data)["headline"] = {"en": "ok", "hi": "अ" * 61}
    with pytest.raises(ValidationError, match="60"):
        LandingContent.model_validate(data)


def test_every_text_needs_the_default_language() -> None:
    data = _content(languages=["en", "hi"])
    _hero(data)["headline"] = {"hi": "नमस्ते"}
    with pytest.raises(ValidationError, match="default language"):
        LandingContent.model_validate(data)


def test_texts_are_stripped_and_control_characters_refused() -> None:
    data = _content()
    _hero(data)["headline"] = {"en": "  Hello  "}
    assert _hero(LandingContent.model_validate(data).model_dump(mode="json"))["headline"] == {
        "en": "Hello"
    }
    _hero(data)["headline"] = {"en": "bad\x00text"}
    with pytest.raises(ValidationError):
        LandingContent.model_validate(data)


def test_a_conversation_starts_with_the_visitor_and_alternates() -> None:
    data = _content()
    conv = _hero(data)["conversation"]
    conv["turns"] = [conv["turns"][1], conv["turns"][0]]
    with pytest.raises(ValidationError, match="visitor"):
        LandingContent.model_validate(data)


def test_a_visitor_line_is_shorter_than_an_assistant_line() -> None:
    data = _content()
    _hero(data)["conversation"]["turns"][0]["text"] = {"en": "x" * 141}
    with pytest.raises(ValidationError, match="140"):
        LandingContent.model_validate(data)


def test_the_value_report_must_say_estimated() -> None:
    data = _content()
    report = next(s for s in data["home"] if s["type"] == "value_report")
    report["caption"] = {"en": "This month you earned this much."}
    with pytest.raises(ValidationError, match="estimated"):
        LandingContent.model_validate(data)


def test_two_sections_cannot_share_an_id() -> None:
    data = _content()
    data["home"][1]["id"] = data["home"][0]["id"]
    with pytest.raises(ValidationError, match="same id"):
        LandingContent.model_validate(data)


def test_translation_gaps_name_the_missing_paths() -> None:
    content = LandingContent.model_validate(_content(languages=["en", "hi"]))
    gaps = translation_gaps(content, "hi")
    assert "home.hero.headline" in gaps
    prefixes = ("home.", "pricing.", "features.", "about.", "contact.", "nav", "footer.", "seo.")
    assert all(g.startswith(prefixes) for g in gaps), gaps
    assert translation_gaps(content, "en") == []


def test_a_disabled_part_has_no_gaps() -> None:
    data = _content(languages=["en", "hi"])
    data["pricing"]["enabled"] = False
    gaps = translation_gaps(LandingContent.model_validate(data), "hi")
    assert not any(g.startswith("pricing.") for g in gaps)


def test_text_limits_are_read_from_the_fields() -> None:
    limits = text_limits()
    assert limits["hero.headline"] == 60
    assert limits["hero.sub"] == 160
    assert limits["faq.items.q"] == 120
    assert limits["seo.og_headline"] == 70
    assert limits["pricing.credit_box"] == 600
