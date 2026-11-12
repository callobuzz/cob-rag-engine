"""P5 T1: the own-key flag, the two new add-on kinds and service orders exist as models.

The kind rules are real SQL check expressions, so they are evaluated here with SQLite (it speaks the
same `IS NULL`, `AND`, `OR`, `IN` the checks use); PostgreSQL runs the real thing in
`tests/integration/test_saas_plans_db.py`.
"""

import sqlite3

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint

from callobuzz_rag.db.models import AddOn, AddOnGrant, Plan, ServiceOrder
from callobuzz_rag.db.models.add_on import KIND_FIELDS_CHECK, RENEWS_CHECK
from tests.unit.accounts_fakes import MODELS

_COLUMNS = [
    "kind",
    "credit_minor",
    "limit_field",
    "limit_delta",
    "questions",
    "feature",
    "duration",
    "renews",
    "includes_support_months",
]


def _ok(check: str, **row: object) -> bool:
    values = dict.fromkeys(_COLUMNS) | {"renews": 0} | row
    db = sqlite3.connect(":memory:")
    db.execute(f"CREATE TABLE t ({', '.join(_COLUMNS)}, CHECK ({check}))")
    try:
        db.execute(
            f"INSERT INTO t VALUES ({', '.join('?' for _ in _COLUMNS)})",
            [values[c] for c in _COLUMNS],
        )
    except sqlite3.IntegrityError:
        return False
    finally:
        db.close()
    return True


def test_plan_byok_allowed_defaults_to_true() -> None:
    column = Plan.__table__.columns["byok_allowed"]
    assert column.nullable is False
    assert "true" in str(column.server_default.arg)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "credit_pack", "credit_minor": 50000},
        {"kind": "extra_limit", "limit_field": "max_documents", "limit_delta": 5},
        {"kind": "question_pack", "questions": 500},
        {"kind": "feature_unlock", "feature": "byok", "duration": "period"},
        {"kind": "service"},
        {"kind": "service", "includes_support_months": 3},
    ],
)
def test_a_valid_row_of_each_kind_passes(row: dict[str, object]) -> None:
    assert _ok(KIND_FIELDS_CHECK, **row)


@pytest.mark.parametrize(
    "row",
    [
        {"kind": "feature_unlock"},  # no feature
        {"kind": "feature_unlock", "feature": "byok", "credit_minor": 5},
        {"kind": "service", "credit_minor": 5},
        {"kind": "service", "feature": "byok"},
        {"kind": "credit_pack", "credit_minor": 5, "feature": "byok"},
        {"kind": "mystery"},
    ],
)
def test_a_row_with_the_wrong_fields_is_refused(row: dict[str, object]) -> None:
    assert not _ok(KIND_FIELDS_CHECK, **row)


def test_renews_only_on_a_monthly_unlock_or_a_service() -> None:
    ok = {"feature": "byok", "duration": "period", "renews": 1}
    assert _ok(RENEWS_CHECK, kind="feature_unlock", **ok)
    assert _ok(RENEWS_CHECK, kind="service", renews=1)
    assert not _ok(RENEWS_CHECK, kind="credit_pack", credit_minor=5, renews=1)
    assert not _ok(
        RENEWS_CHECK, kind="feature_unlock", feature="byok", duration="permanent", renews=1
    )
    assert _ok(RENEWS_CHECK, kind="credit_pack", credit_minor=5, renews=0)


def test_catalogue_and_grant_checks_are_named() -> None:
    names = {c.name for c in AddOn.__table__.constraints if isinstance(c, CheckConstraint)}
    assert {
        "ck_add_on_kind",
        "ck_add_on_renews",
        "ck_add_on_feature",
        "ck_add_on_sac_code",
    } <= names
    grant_names = {
        c.name for c in AddOnGrant.__table__.constraints if isinstance(c, CheckConstraint)
    }
    assert "ck_add_on_grant_kind" in grant_names


def test_catalogue_defaults_are_off() -> None:
    columns = AddOn.__table__.columns
    for name in ("sold_to_owners", "placeholder", "renews"):
        assert "false" in str(columns[name].server_default.arg)  # type: ignore[union-attr]
    assert "cancelled_at" in AddOnGrant.__table__.columns
    assert "renewed_through" in AddOnGrant.__table__.columns


def test_service_order_is_registered_and_one_per_grant() -> None:
    assert ServiceOrder in set(MODELS.values())
    unique = {
        frozenset(c.name for c in cons.columns)
        for cons in ServiceOrder.__table__.constraints
        if isinstance(cons, UniqueConstraint)
    }
    assert frozenset({"grant_id"}) in unique
    (tenant_fk,) = ServiceOrder.__table__.columns["tenant_id"].foreign_keys
    assert tenant_fk.ondelete == "CASCADE"
    (grant_fk,) = ServiceOrder.__table__.columns["grant_id"].foreign_keys
    assert grant_fk.ondelete == "RESTRICT"
