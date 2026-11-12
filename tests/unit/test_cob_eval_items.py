"""The COB eval set: enough questions, in the right mix, pointing at pages that exist."""

from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from callobuzz_rag.cob.eval.items import EvalItem, load_items
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.tools import REGISTRY
from callobuzz_rag.cob.types import allowed_audiences

ROOT = Path(__file__).resolve().parents[2]
ITEMS = load_items(ROOT / "docs/cob/eval/cob-eval.jsonl")
INDEX = load_docs_index(ROOT)


def test_composition() -> None:
    by = Counter(i.role for i in ITEMS)
    assert len(ITEMS) >= 150
    assert by["admin"] + by["operator_owner"] >= 50
    assert by["owner"] + by["member"] >= 70
    assert by["public"] >= 30
    langs = Counter(i.language for i in ITEMS)
    assert langs["hi"] >= 10 and langs["hi-Latn"] >= 10
    assert langs["hi"] + langs["hi-Latn"] >= 30
    neg = [i for i in ITEMS if i.expect.behaviour in {"admin_managed", "sign_in_hint"}]
    assert len(neg) >= 20
    assert sum(1 for i in ITEMS if i.expect.behaviour == "admin_managed") >= 10
    assert sum(1 for i in ITEMS if i.expect.behaviour == "sign_in_hint") >= 5
    assert sum(1 for i in ITEMS if i.expect.behaviour == "card" and i.expect.tool) >= 24
    assert sum(1 for i in ITEMS if i.expect.behaviour == "proposal") >= 18
    assert sum(1 for i in ITEMS if i.expect.behaviour in {"smalltalk", "no_answer"}) >= 10


def test_negative_roles_are_the_right_roles() -> None:
    for i in ITEMS:
        if i.expect.behaviour == "admin_managed":
            assert i.role in {"owner", "member"}, i.id
        if i.expect.behaviour == "sign_in_hint":
            assert i.role == "public", i.id


def test_every_expected_slug_and_anchor_exists() -> None:
    for i in ITEMS:
        if i.expect.slug:
            page = INDEX.pages.get(i.expect.slug)
            assert page, i.id
            if i.expect.anchor:
                assert i.expect.anchor in {s.anchor for s in page.sections}, i.id
        if i.expect.link_route:
            page = INDEX.pages.get(i.expect.slug or "")
            assert page is not None and page.route == i.expect.link_route, i.id


def test_every_tool_and_screen_is_covered() -> None:
    tools = {i.expect.tool for i in ITEMS if i.expect.tool}
    assert set(REGISTRY) - {"start_guided_setup"} <= tools
    reads = Counter(i.expect.tool for i in ITEMS if i.expect.behaviour == "card" and i.expect.tool)
    for name, tool in REGISTRY.items():
        if tool.kind == "read":
            assert reads[name] >= 2, name
    screens = {p.slug for p in INDEX.pages.values() if p.route}
    assert screens <= {i.expect.slug for i in ITEMS}


def test_tool_items_name_registered_tools_and_matching_intent() -> None:
    for i in ITEMS:
        if not i.expect.tool:
            continue
        assert i.expect.tool in REGISTRY, i.id
        kind = REGISTRY[i.expect.tool].kind
        assert i.expect.intent == ("account" if kind == "read" else "action"), i.id


def test_hindi_and_hinglish_items_carry_an_english_rendering() -> None:
    for i in ITEMS:
        if i.is_indic:
            assert i.translation_en, i.id
        else:
            assert i.translation_en is None, i.id


def test_negative_items_name_a_section_above_the_role() -> None:
    for i in ITEMS:
        if not i.is_negative:
            continue
        assert i.expect.slug, i.id
        page = INDEX.pages[i.expect.slug]
        audiences = {s.audience.value for s in page.sections if i.expect.anchor in (None, s.anchor)}
        assert audiences - set(allowed_audiences(i.cob_role)), i.id
        if i.expect.behaviour == "admin_managed":
            assert i.expect.must_not_include, i.id


def test_ids_unique_and_strict_schema() -> None:
    ids = [i.id for i in ITEMS]
    assert len(ids) == len(set(ids))
    with pytest.raises(ValidationError):
        EvalItem.model_validate(
            {
                "id": "x-001",
                "role": "owner",
                "question": "q?",
                "expect": {"behaviour": "answer"},
                "surprise": 1,
            }
        )
    with pytest.raises(ValidationError):
        EvalItem.model_validate(
            {"id": "x-002", "role": "owner", "question": "q?", "expect": {"behaviour": "nonsense"}}
        )
