"""LP1 T2: the words the public website must not use."""

import pytest

from callobuzz_rag.landing.copy_rules import copy_warnings, copy_warnings_in_text
from callobuzz_rag.landing.schema import LandingContent, default_content


def test_the_default_site_has_zero_warnings() -> None:
    assert copy_warnings(default_content()) == []


def test_a_faq_answer_with_two_promises_gives_two_warnings_with_paths() -> None:
    data = default_content().model_dump(mode="json")
    faq = next(s for s in data["home"] if s["type"] == "faq")
    faq["items"][0]["a"] = {"en": "Guaranteed ROI from day one."}
    found = copy_warnings(LandingContent.model_validate(data))
    assert [(w.path, w.word) for w in found] == [
        ("home.faq.items[0].a.en", "ROI"),
        ("home.faq.items[0].a.en", "guaranteed"),
    ]
    assert len(found) == 2
    assert all(w.path == "home.faq.items[0].a.en" for w in found)
    assert found[0].sentence == "Guaranteed ROI from day one."


def test_open_source_and_agpl_are_not_flagged() -> None:
    assert copy_warnings_in_text("Open source under AGPL-3.0") == []
    assert copy_warnings_in_text("ROI guaranteed") == ["ROI", "guaranteed"]


def test_a_word_inside_another_word_is_not_flagged() -> None:
    assert copy_warnings_in_text("A corpuscle is a cell, and a ledgerman is not a ledger.") == [
        "ledger"
    ]
    assert copy_warnings_in_text("A corpuscle is a cell.") == []


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("We are 100% accurate", "100% accurate"),
        ("It is never wrong.", "never wrong"),
        ("Your corpus is safe", "corpus"),
        ("Each tenant has a key", "tenant"),
        ("Check the ledger", "ledger"),
        ("We guarantee it", "guaranteed"),
    ],
)
def test_each_flagged_phrase_is_found(text: str, word: str) -> None:
    assert copy_warnings_in_text(text) == [word]


def test_hinglish_text_is_checked_too() -> None:
    data = default_content().model_dump(mode="json")
    data["languages"] = ["en", "hi-Latn"]
    hero = next(s for s in data["home"] if s["type"] == "hero")
    hero["sub"] = {"en": hero["sub"]["en"], "hi-Latn": "Guaranteed jawab, ROI pakka."}
    found = copy_warnings(LandingContent.model_validate(data))
    assert {w.word for w in found} == {"guaranteed", "ROI"}
    assert all(w.path == "home.hero.sub.hi-Latn" for w in found)
