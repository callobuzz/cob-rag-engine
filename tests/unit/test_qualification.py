"""Lead qualification: three buckets from owner rules (C9)."""

import pytest

from callobuzz_rag.conversation.qualification import (
    condition_holds,
    flatten_fields,
    notify_now,
    qualify,
)
from callobuzz_rag.conversation.schema import Condition, Leads, QualificationRule

FIELDS = {
    "name": "Priya Kulkarni",
    "phone": "+919812345678",
    "email": None,
    "custom": {"bhk": "3BHK", "budget": "85", "visit": "2026-10-10"},
}


def rule(status: str, *conds: tuple[str, str, object]) -> QualificationRule:
    return QualificationRule.model_validate(
        {
            "status": status,
            "conditions": [{"field": f, "op": op, "value": v} for f, op, v in conds],
        }
    )


def test_flatten_puts_custom_fields_beside_contact_fields() -> None:
    flat = flatten_fields(FIELDS)
    assert flat["bhk"] == "3BHK" and flat["phone"] == "+919812345678" and "custom" not in flat


def test_contact_fields_win_over_a_custom_field_with_the_same_id() -> None:
    flat = flatten_fields({**FIELDS, "custom": {"phone": "000"}})
    assert flat["phone"] == "+919812345678"


@pytest.mark.parametrize(
    ("field", "op", "value", "expected"),
    [
        ("bhk", "eq", "3bhk", True),
        ("bhk", "neq", "2BHK", True),
        ("bhk", "in", ["2BHK", "3BHK"], True),
        ("budget", "gte", 80, True),
        ("budget", "lte", 50, False),
        ("budget", "eq", 85, True),
        ("budget", "eq", 80, False),
        ("budget", "in", ["80", "85"], True),
        ("budget", "neq", 85, False),
        ("email", "present", None, False),
        ("phone", "present", None, True),
        ("budget", "gte", "lots", False),
        ("missing", "eq", "x", False),
        ("intent", "eq", "buying_intent", True),
    ],
)
def test_condition_ops(field: str, op: str, value: object, expected: bool) -> None:
    cond = Condition.model_validate({"field": field, "op": op, "value": value})
    assert condition_holds(cond, flatten_fields(FIELDS), "buying_intent") is expected


def test_no_rules_means_no_bucket() -> None:
    assert qualify(FIELDS, "buying_intent", []) is None


def test_qualified_wins_over_partial_whatever_the_list_order() -> None:
    rules = [rule("partial", ("bhk", "present", None)), rule("qualified", ("budget", "gte", 80))]
    assert qualify(FIELDS, "buying_intent", rules) == "qualified"


def test_every_condition_of_a_rule_must_hold() -> None:
    rules = [rule("qualified", ("budget", "gte", 80), ("bhk", "eq", "2BHK"))]
    assert qualify(FIELDS, "buying_intent", rules) == "unqualified"


def test_partial_when_only_partial_matches() -> None:
    rules = [
        rule("qualified", ("budget", "gte", 150)),
        rule("partial", ("intent", "eq", "buying_intent")),
    ]
    assert qualify(FIELDS, "buying_intent", rules) == "partial"


def test_notify_now_holds_back_only_unqualified_with_digest_on() -> None:
    leads = Leads()
    assert notify_now("unqualified", leads) is True
    digest = Leads.model_validate({"notify": {"daily_digest": True}})
    assert notify_now("unqualified", digest) is False
    assert notify_now("partial", digest) is True
    assert notify_now(None, digest) is True


def test_a_custom_field_may_not_reuse_a_contact_id() -> None:
    from pydantic import ValidationError

    from callobuzz_rag.conversation.schema import AssistantConfig

    ok = AssistantConfig().model_dump()
    ok["leads"]["custom_fields"] = [
        {"id": "budget", "label": {"en": "X"}, "type": "text", "required": False}
    ]
    AssistantConfig.model_validate(ok)
    for reserved in ("name", "phone", "email"):
        raw = AssistantConfig().model_dump()
        raw["leads"]["custom_fields"] = [
            {"id": reserved, "label": {"en": "X"}, "type": "text", "required": False}
        ]
        with pytest.raises(ValidationError):
            AssistantConfig.model_validate(raw)
