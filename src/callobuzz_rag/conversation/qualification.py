"""Three-bucket lead qualification from the owner's rules (design 4.7).

A lead is `qualified` when every condition of any `qualified` rule holds, else `partial` when
every condition of any `partial` rule holds, else `unqualified`. No rules means no bucket
(None), so a knowledge base that never defined criteria keeps today's routing.

`intent` is the lead's `trigger` (`buying_intent`, `no_answer`, `callback`, `conversational`...).

Hold-back: an unqualified lead of a knowledge base with `daily_digest` on is not e-mailed at
once, only in the digest. If the owner switches the digest off before it runs, that lead never
gets a mail (it is in the inbox, and the webhook always fires).
"""

from typing import Any

from callobuzz_rag.api.schemas.leads import Qualification
from callobuzz_rag.conversation.schema import Condition, Leads, QualificationRule


def flatten_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """`{name, phone, email, custom: {id: value}}` as one mapping; contact fields win over a
    custom field that reuses their id."""
    custom = dict(fields.get("custom") or {})
    contact = {k: v for k, v in fields.items() if k != "custom"}
    return {**custom, **contact}


def _text(value: Any) -> str:
    return str(value).strip().casefold()


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _same(actual: Any, wanted: Any) -> bool:
    left, right = _number(actual), _number(wanted)
    if left is not None and right is not None:
        return left == right
    return _text(actual) == _text(wanted)


def condition_holds(cond: Condition, fields: dict[str, Any], intent: str | None) -> bool:
    actual = intent if cond.field == "intent" else fields.get(cond.field)
    if cond.op == "present":
        return actual not in (None, "", [])
    if actual in (None, ""):
        return False
    if cond.op == "eq":
        return _same(actual, cond.value)
    if cond.op == "neq":
        return not _same(actual, cond.value)
    if cond.op == "in":
        options = cond.value if isinstance(cond.value, list) else [cond.value]
        return any(_same(actual, o) for o in options)
    left, right = _number(actual), _number(cond.value)
    if left is None or right is None:
        return False
    return left >= right if cond.op == "gte" else left <= right


def _matches(rule: QualificationRule, fields: dict[str, Any], intent: str | None) -> bool:
    return all(condition_holds(c, fields, intent) for c in rule.conditions)


def qualify(
    fields: dict[str, Any], intent: str | None, rules: list[QualificationRule]
) -> Qualification | None:
    if not rules:
        return None
    flat = flatten_fields(fields)
    if any(_matches(r, flat, intent) for r in rules if r.status == "qualified"):
        return "qualified"
    if any(_matches(r, flat, intent) for r in rules if r.status == "partial"):
        return "partial"
    return "unqualified"


def notify_now(qualification: str | None, leads: Leads) -> bool:
    """Whether the per-lead e-mail goes out now (the webhook always does)."""
    return not (qualification == "unqualified" and leads.notify.daily_digest)
