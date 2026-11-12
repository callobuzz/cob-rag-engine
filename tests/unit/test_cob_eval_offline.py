"""The LLM-free COB eval gate that runs in CI: role filter, negatives, rules routing, hit rate."""

from pathlib import Path

import pytest

from callobuzz_rag.cob.eval.items import EvalItem, load_items
from callobuzz_rag.cob.eval.offline import Bm25Index, check_negative, run_offline
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.router import load_rules

ROOT = Path(__file__).resolve().parents[2]
INDEX = load_docs_index(ROOT)
ITEMS = load_items(ROOT / "docs/cob/eval/cob-eval.jsonl")
REPORT = run_offline(INDEX, ITEMS, rules=load_rules())


def test_role_filter_is_absolute() -> None:
    assert REPORT.role_leaks == [], REPORT.role_leaks[:5]


def test_negative_role_items_all_pass() -> None:
    assert REPORT.negatives_failed == [], REPORT.negatives_failed


def test_rules_routing_items_all_pass() -> None:
    assert REPORT.routing_failed == [], REPORT.routing_failed


def test_doc_hit_rate_bars() -> None:
    assert REPORT.hit_at_5 >= 0.80, REPORT.misses[:10]
    assert REPORT.hit_at_5_indic >= 0.70, REPORT.misses_indic[:10]


def test_the_report_summary_is_what_gets_stored() -> None:
    summary = REPORT.summary()
    assert summary["items"] == len(ITEMS) and summary["passed"] is True
    assert set(summary) == {
        "items",
        "hit_at_5",
        "hit_at_5_indic",
        "role_leaks",
        "platform_leaks",  # COB9
        "negatives_failed",
        "routing_failed",
        "passed",
    }


def _negative(**overrides: object) -> EvalItem:
    base: dict[str, object] = {
        "id": "neg-test-001",
        "role": "owner",
        "question": "What is RAG_SESSION_SECRET and what do I put in it?",
        "expect": {
            "behaviour": "admin_managed",
            "slug": "settings/required",
            "anchor": "rag_session_secret",
            "must_not_include": ["RAG_SESSION_SECRET="],
        },
    }
    base.update(overrides)
    return EvalItem.model_validate(base)


def test_a_negative_item_fails_when_the_section_is_not_above_the_role() -> None:
    bm25 = Bm25Index(INDEX)
    assert check_negative(_negative(), bm25) is None
    # For an operator the section is not above the role, so the item is wrong, not the filter.
    assert "not above" in (check_negative(_negative(role="admin"), bm25) or "")
    # A section the role may read is not "above" it.
    allowed = _negative(
        expect={"behaviour": "admin_managed", "slug": "screens/widget", "anchor": "common-tasks"}
    )
    assert "not above" in (check_negative(allowed, bm25) or "")


def test_a_negative_item_fails_when_the_probe_would_not_fire() -> None:
    bm25 = Bm25Index(INDEX)
    lost = _negative(question="What is the colour of the sky?")
    assert "probe" in (check_negative(lost, bm25) or "")


def test_a_role_never_retrieves_what_is_above_it() -> None:
    bm25 = Bm25Index(INDEX)
    query = "RAG_SESSION_SECRET signs dashboard sign-ins"
    for hit in bm25.search(query, ["public"], 10):
        assert hit.audience == "public"
    admin = bm25.search(query, ["admin"], 3)
    assert admin and admin[0].slug == "settings/required"


def test_a_rules_item_that_the_rules_cannot_place_is_reported() -> None:
    item = EvalItem.model_validate(
        {
            "id": "route-test-001",
            "role": "owner",
            "question": "What is the meaning of life?",
            "expect": {"behaviour": "card", "intent": "account", "tool": "documents"},
            "routing": "rules",
        }
    )
    report = run_offline(INDEX, [item])
    assert report.routing_failed and report.routing_failed[0][0] == "route-test-001"
    assert report.passed is False


def test_a_mis_tagged_admin_section_is_a_role_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    import dataclasses

    import callobuzz_rag.cob.eval.offline as offline

    real = offline.records_for

    def tagged_public(index):  # type: ignore[no-untyped-def]
        for record in real(index):
            meta = {**record.metadata, "audience": "public"}
            yield dataclasses.replace(record, metadata=meta)

    monkeypatch.setattr(offline, "records_for", tagged_public)
    admin_only = [i for i in ITEMS if i.is_negative][:3]
    report = run_offline(INDEX, admin_only, rules=load_rules())
    assert report.role_leaks, "a section above the role, tagged public, must be caught"
