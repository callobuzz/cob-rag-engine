"""Effective limits: the plan's, the customer's own overrides, plus the add-ons in force (BIL5).

    effective[f] = (override[f] if f in overrides else base[f]) + sum(active grants[f])

`None` is unlimited and stays `None` whatever is granted on top (a grant adds to a finite limit
only). Question packs add to `monthly_questions` for the UTC month they were granted in;
period-bound extra limits end at the next UTC month start; permanent ones last until revoked.

`apply_limit_adjustments` is the pure rule (property-tested); `active_grant_sum` is the SQL
expression `accounts/caps.py` embeds, as scalar subqueries, in the one statement that reads a
tenant's limits.
"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, ScalarSelect, and_, func, or_, select

from callobuzz_rag.db.models.add_on import AddOnGrant
from callobuzz_rag.db.models.plan import LIMIT_FIELDS

if TYPE_CHECKING:
    from callobuzz_rag.accounts.caps import Limits

# The grant kinds that change a limit (a credit pack changes the balance, not a limit).
LIMIT_GRANT_KINDS = ("extra_limit", "question_pack")

_INSTANT = timedelta(microseconds=1)


def apply_limit_adjustments(
    base: "Limits", overrides: Mapping[str, int | None], grants: Mapping[str, int]
) -> "Limits":
    """`base` with the account's overrides replacing it and the grants added on top.

    An override of `None` means unlimited (grants cannot limit it); an unlimited base stays
    unlimited. Fields that are not limits (`count_cached_answers`) pass through."""
    values: dict[str, Any] = {}
    for field in LIMIT_FIELDS:
        value = overrides[field] if field in overrides else getattr(base, field)
        values[field] = None if value is None else value + int(grants.get(field, 0))
    return replace(base, **values)


def is_in_force(start: datetime, end: datetime | None = None) -> ColumnElement[bool]:
    """The grants in force at some point of `[start, end)` (default: at `start`): not revoked,
    already begun, not yet ended. A grant that ends exactly at `start` is out."""
    window_end = end if end is not None else start + _INSTANT
    return and_(
        AddOnGrant.revoked_at.is_(None),
        AddOnGrant.starts_at < window_end,
        or_(AddOnGrant.ends_at.is_(None), AddOnGrant.ends_at > start),
    )


def active_grant_sum(
    tenant_id: Any, field: str, start: datetime, end: datetime | None = None
) -> ScalarSelect[int]:
    """`SELECT coalesce(sum(delta), 0)` over the tenant's grants in force for `field` (one of
    `LIMIT_FIELDS`). `tenant_id` is a column of the enclosing statement, so the subquery
    correlates to it and the whole limit read stays one statement."""
    if field == "monthly_questions":
        amount, kind_filter = AddOnGrant.questions, AddOnGrant.kind == "question_pack"
    else:
        amount = AddOnGrant.limit_delta
        kind_filter = and_(AddOnGrant.kind == "extra_limit", AddOnGrant.limit_field == field)
    return (
        select(func.coalesce(func.sum(amount), 0))
        .where(AddOnGrant.tenant_id == tenant_id, kind_filter, is_in_force(start, end))
        .scalar_subquery()
    )
