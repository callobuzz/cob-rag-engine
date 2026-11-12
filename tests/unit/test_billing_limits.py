"""Effective limits (BIL5): overrides replace the plan's, active add-ons add on top, an unlimited
limit stays unlimited, and every cap check reads them (`caps.effective_limits`,
`caps.question_quota`, the worker backstop). The SQL itself runs on PostgreSQL in
`tests/integration/test_billing_addons_db.py`; here rows are stand-ins and the statement's
shape is checked."""

import random
import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.accounts import caps
from callobuzz_rag.accounts.caps import Limits
from callobuzz_rag.billing.limits import apply_limit_adjustments
from callobuzz_rag.core.exceptions import DocumentLimitReachedError
from callobuzz_rag.db.models.plan import LIMIT_FIELDS

BASE = Limits(
    max_knowledge_bases=3,
    monthly_questions=1000,
    max_documents=100,
    max_widget_keys=2,
    max_upload_mb=20,
    max_document_tokens=None,
)


def test_overrides_replace_then_grants_add() -> None:
    out = apply_limit_adjustments(
        BASE,
        overrides={"max_documents": 500, "max_widget_keys": None},
        grants={"max_documents": 50, "monthly_questions": 200, "max_widget_keys": 3},
    )
    assert out.max_documents == 550  # override 500 + grant 50
    assert out.max_widget_keys is None  # the override says unlimited; a grant cannot limit it
    assert out.monthly_questions == 1200
    assert out.max_knowledge_bases == 3 and out.max_document_tokens is None


def test_other_fields_pass_through() -> None:
    out = apply_limit_adjustments(
        Limits(max_documents=5, count_cached_answers=False), overrides={}, grants={}
    )
    assert out == Limits(max_documents=5, count_cached_answers=False)


def random_limits(rng: random.Random) -> Limits:
    return Limits(
        **{f: (None if rng.random() < 0.3 else rng.randint(1, 5000)) for f in LIMIT_FIELDS}
    )


@pytest.mark.parametrize("seed", range(200))
def test_grants_never_lower_a_limit_and_none_stays_none(seed: int) -> None:
    rng = random.Random(seed)
    base = random_limits(rng)
    grants = {f: rng.randint(0, 1000) for f in LIMIT_FIELDS if rng.random() < 0.5}
    out = apply_limit_adjustments(base, overrides={}, grants=grants)
    for f in LIMIT_FIELDS:
        b, o = getattr(base, f), getattr(out, f)
        assert (o is None) if b is None else (o == b + grants.get(f, 0))


# --------------------------------------------------------- caps.effective_limits


def _session(row: Any) -> AsyncMock:
    session = AsyncMock()
    result = MagicMock()
    result.one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)
    return session


def _row(*, overrides: Any = None, grants: tuple[int, ...] = (0,) * 6, **plan: Any) -> tuple:
    base = {f: None for f in LIMIT_FIELDS} | plan
    return (
        plan.get("max_corpora"),
        base["max_knowledge_bases"],
        base["monthly_questions"],
        base["max_documents"],
        base["max_widget_keys"],
        base["max_upload_mb"],
        base["max_document_tokens"],
        True,
        overrides,
        *grants,
    )


async def test_effective_limits_follow_plan_overrides_and_grants() -> None:
    # grant sums come in LIMIT_FIELDS order: kbs, questions, documents, keys, upload, tokens
    row = _row(
        max_knowledge_bases=3,
        monthly_questions=1000,
        max_documents=100,
        max_widget_keys=2,
        overrides={"max_documents": 500, "max_widget_keys": None},
        grants=(1, 200, 50, 3, 0, 0),
    )
    out = await caps.effective_limits(_session(row), uuid.uuid4())
    assert out == Limits(
        max_knowledge_bases=4,
        monthly_questions=1200,
        max_documents=550,
        max_widget_keys=None,
    )


async def test_tenant_max_corpora_is_the_base_and_an_account_override_beats_it() -> None:
    row = _row(max_knowledge_bases=3, max_corpora=7, grants=(1, 0, 0, 0, 0, 0))
    assert (await caps.effective_limits(_session(row), uuid.uuid4())).max_knowledge_bases == 8
    row = _row(
        max_knowledge_bases=3,
        max_corpora=7,
        overrides={"max_knowledge_bases": 2},
        grants=(1,) + (0,) * 5,
    )
    assert (await caps.effective_limits(_session(row), uuid.uuid4())).max_knowledge_bases == 3


async def test_an_unlimited_plan_stays_unlimited_with_grants() -> None:
    row = _row(grants=(5, 5, 5, 5, 5, 5))
    assert await caps.effective_limits(_session(row), uuid.uuid4()) == Limits()


async def test_effective_limits_is_one_statement_with_grants_correlated_to_the_tenant() -> None:
    session = _session(_row())
    await caps.effective_limits(session, uuid.uuid4(), now=datetime(2026, 10, 4, tzinfo=UTC))
    assert session.execute.await_count == 1
    sql = str(session.execute.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert sql.count("FROM add_on_grants") == 6
    assert sql.count("add_on_grants.tenant_id = tenants.id") == 6
    assert "LEFT OUTER JOIN billing_accounts" in sql
    assert "add_on_grants.revoked_at IS NULL" in sql


# ------------------------------------------------------------ caps.question_quota


async def test_question_quota_adds_the_months_packs_and_honours_an_override() -> None:
    session = _session((1000, 40, {}, 500))
    assert await caps.question_quota(session, uuid.uuid4(), date(2026, 10, 1)) == (1500, 40)
    assert await caps.question_quota(
        _session((1000, 0, {"monthly_questions": 300}, 500)), uuid.uuid4(), date(2026, 10, 1)
    ) == (800, 0)
    # unlimited by the plan or by an override: nothing to enforce, packs or not
    assert (
        await caps.question_quota(_session((None, 9, {}, 500)), uuid.uuid4(), date(2026, 10, 1))
        is None
    )
    assert (
        await caps.question_quota(
            _session((1000, 9, {"monthly_questions": None}, 500)), uuid.uuid4(), date(2026, 10, 1)
        )
        is None
    )


async def test_question_quota_asks_for_packs_overlapping_that_month_only() -> None:
    session = _session((1000, 0, {}, 0))
    await caps.question_quota(session, uuid.uuid4(), date(2026, 10, 1))
    compiled = session.execute.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "add_on_grants.kind = %(kind_1)s" in sql and "add_on_grants.starts_at <" in sql
    assert "add_on_grants.ends_at >" in sql
    params = compiled.params
    assert datetime(2026, 11, 1, tzinfo=UTC) in params.values()  # window end
    assert datetime(2026, 10, 1, tzinfo=UTC) in params.values()  # window start
    assert "question_pack" in params.values()


# ---------------------------------------------------------- the worker backstop


async def test_worker_document_backstop_uses_effective_limits() -> None:
    tenant, job = uuid.uuid4(), uuid.uuid4()
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = tenant
    session.execute = AsyncMock(return_value=result)
    seen: list[uuid.UUID] = []

    async def limits(sess: Any, tenant_id: uuid.UUID, *a: Any, **kw: Any) -> Limits:
        seen.append(tenant_id)
        return Limits(max_documents=3)  # the plan says 2, an add-on made it 3

    with (
        patch.object(caps, "effective_limits", limits),
        patch.object(caps, "document_usage", AsyncMock(return_value=(2, 0))),
    ):
        await caps.ensure_document_room_for_job(session, uuid.uuid4(), job, 1)  # 2 + 1 <= 3
        with pytest.raises(DocumentLimitReachedError) as err:
            await caps.ensure_document_room_for_job(session, uuid.uuid4(), job, 2)
    assert seen == [tenant, tenant]
    assert err.value.details()["limit"] == 3


def test_revoked_and_expired_grants_do_not_count() -> None:
    """The in-force predicate: not revoked, begun, and not ended at the instant asked."""
    from callobuzz_rag.billing.limits import is_in_force

    sql = str(is_in_force(datetime(2026, 10, 4, tzinfo=UTC)).compile(dialect=postgresql.dialect()))
    assert "revoked_at IS NULL" in sql
    assert "starts_at <" in sql
    assert "ends_at IS NULL OR add_on_grants.ends_at >" in sql


def test_the_customer_list_shows_the_effective_limits_not_the_plans() -> None:
    from types import SimpleNamespace

    from callobuzz_rag.api.routes import tenants

    tenant = SimpleNamespace(max_corpora=None)
    row = SimpleNamespace(
        Tenant=tenant,
        plan_max_kbs=3,
        limit_overrides={"max_knowledge_bases": 5},
        grant_max_kbs=2,
        plan_monthly_questions=1000,
        grant_questions=500,
    )
    assert tenants._effective_max_corpora(row) == 7  # override 5 replaces the plan's 3, +2
    assert tenants._effective_limit(row, "monthly_questions", 1000, row.grant_questions) == 1500
    plain = SimpleNamespace(
        Tenant=SimpleNamespace(max_corpora=4), plan_max_kbs=3, limit_overrides=None, grant_max_kbs=0
    )
    assert tenants._effective_max_corpora(plain) == 4  # tenants.max_corpora replaces the plan's
    unlimited = SimpleNamespace(
        Tenant=tenant, plan_max_kbs=None, limit_overrides=None, grant_max_kbs=9
    )
    assert tenants._effective_max_corpora(unlimited) is None


async def test_refundable_cash_counts_the_price_paid_never_free_credit_or_bonus() -> None:
    from callobuzz_rag.billing import ledger

    captured: list[Any] = []

    class _Result:
        def scalar_one(self) -> int:
            return 0

    class _Session:
        async def execute(self, stmt: Any) -> _Result:
            captured.append(stmt)
            return _Result()

    await ledger.paid_cash_minor(_Session(), uuid.uuid4())  # type: ignore[arg-type]
    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "LEFT OUTER JOIN add_on_grants" in sql
    assert "add_on_grants.payment" in sql and "least(" in sql and "price_minor" in sql
