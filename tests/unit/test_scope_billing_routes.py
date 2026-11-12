"""Billing routes and the scoping rules: a workspace sees only itself, keys that are not a
workspace's are refused, members get no money, and the admin routes are for the admin."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.billing import accounts, summary  # BIL6
from callobuzz_rag.billing.pricing import PricingFields, build_effective
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.scope_harness import World, call, scoped_app

OWNER_URL = "/api/v1/billing/account"
ADMIN_ROUTES = [
    ("GET", "/api/v1/admin/billing/accounts", None),
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}", None),
    ("PATCH", "/api/v1/admin/billing/accounts/{tenant_id}", {"notes": "x"}),
    ("GET", "/api/v1/admin/billing/plans/{plan_id}/pricing", None),
    ("PUT", "/api/v1/admin/billing/plans/{plan_id}/pricing", {}),
    ("GET", "/api/v1/admin/billing/plans/{plan_id}/pricing/history", None),
    # BIL3 ledger
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/ledger", None),
    (
        "POST",
        "/api/v1/admin/billing/accounts/{tenant_id}/ledger",
        {"entry_type": "top_up", "amount_minor": 100, "idempotency_key": "scope-test-0001"},
    ),
    ("GET", "/api/v1/admin/billing/reconciliation", None),
    ("POST", "/api/v1/admin/billing/reconciliation", None),
    # /BIL3 ledger
    # P1 payments
    ("GET", "/api/v1/admin/company", None),
    ("PUT", "/api/v1/admin/company", {"legal_name": "X"}),
    ("GET", "/api/v1/admin/billing/tax-profile", None),
    ("PUT", "/api/v1/admin/billing/tax-profile", {}),
    ("GET", "/api/v1/admin/billing/payments/settings", None),
    ("PUT", "/api/v1/admin/billing/payments/settings", {"provider": "none"}),
    ("POST", "/api/v1/admin/billing/payments/settings/test", None),
    ("GET", "/api/v1/admin/billing/payments", None),
    ("GET", "/api/v1/admin/billing/payments/events", None),
    ("POST", "/api/v1/admin/billing/payments/events/{plan_id}/retry", None),
    ("GET", "/api/v1/admin/billing/payments/{plan_id}", None),
    (
        "POST",
        "/api/v1/admin/billing/payments/{plan_id}/refunds",
        {"amount_minor": 100, "memo": "test", "idempotency_key": "scope-test-0001"},
    ),
    ("POST", "/api/v1/admin/billing/accounts/{tenant_id}/payment-links", {"purpose": "plan"}),
    # /P1 payments
    # P2 invoices
    ("GET", "/api/v1/admin/billing/invoices", None),
    ("GET", "/api/v1/admin/billing/invoices/export?start=2026-04-01&end=2026-04-30", None),
    ("GET", "/api/v1/admin/billing/invoices/{plan_id}/pdf", None),
    ("POST", "/api/v1/admin/billing/invoices/{plan_id}/resend", None),
    # /P2 invoices
    # P5 plans
    ("GET", "/api/v1/admin/billing/service-orders", None),
    ("PATCH", "/api/v1/admin/billing/service-orders/{plan_id}", {"status": "in_progress"}),
    # /P5 plans
]


@contextmanager
def billing_world() -> Iterator[tuple[Any, World]]:
    """The scoping harness, with `load_context` answering from its two tenants."""
    with scoped_app() as (app, world):
        plan = Plan(id=uuid.uuid4(), name="Pro", is_internal=False)
        values = PricingFields(
            subscription_fee_minor=50_000, per_question_minor=3, usage_mode="per_question"
        ).model_dump()
        pricing = PlanPricing(
            id=uuid.uuid4(), plan_id=plan.id, version=1, is_current=True, **values
        )

        async def load_context(session: Any, tenant_id: uuid.UUID) -> accounts.BillingContext:
            row = world.rows[tenant_id]
            tenant = SimpleNamespace(**{**vars(row), "is_system": False})
            account = BillingAccount(
                tenant_id=tenant_id,
                currency="INR",
                overrides={},
                ledger_seq=0,
                balance_minor=0,
                usage_residue_micro=0,
            )
            return accounts.BillingContext(
                tenant, plan, pricing, account, build_effective(tenant, plan, pricing, account)
            )

        async def owed(session: Any, account: BillingAccount) -> int:  # BIL3: no SUM() here
            return 0

        async def usage(session: Any, account: Any, now: Any = None) -> Any:  # BIL6
            return summary.UsagePeriod(questions=0, charged_minor=0, unpriced=0)

        async def trust(session: Any, ctx: Any, usage: Any, **_: Any) -> dict[str, Any]:  # TV3
            return {"renewal": None, "price_changes": [], "spend_cap": None}

        async def refs(session: Any, account: Any, now: Any = None) -> list[Any]:  # BIL6
            return []

        with (
            patch.object(accounts, "load_context", load_context),
            patch("callobuzz_rag.billing.ledger.owed_micro", owed),
            patch.object(summary, "usage_this_period", usage),  # BIL6: needs SUM()
            patch.object(summary, "recent_statements", refs),  # BIL6
            patch.object(summary, "trust_fields", trust),  # TV3: reads the engine settings row
        ):
            yield app, world


@pytest.mark.parametrize(
    ("key", "tenant"),
    [("tk_a", "a"), ("tk_b", "b"), ("owner_a", "a"), ("member_a", "a")],
)
async def test_a_workspace_always_sees_its_own_account(key: str, tenant: str) -> None:
    with billing_world() as (app, world):
        res = await call(app, key, "GET", OWNER_URL)
        own = (world.tenant_a if tenant == "a" else world.tenant_b).id
    assert res.status_code == 200, res.text
    assert res.json()["tenant_id"] == str(own)


async def test_a_member_gets_no_money() -> None:
    with billing_world() as (app, _):
        owner = await call(app, "owner_a", "GET", OWNER_URL)
        member = await call(app, "member_a", "GET", OWNER_URL)
    assert owner.json()["subscription_fee_minor"] == 50_000
    assert member.json()["money_visible"] is False
    assert member.json()["subscription_fee_minor"] is None
    assert member.json()["per_question_minor"] is None


@pytest.mark.parametrize("key", ["ck_a", "ck_b"])
async def test_a_knowledge_base_key_is_refused(key: str) -> None:
    with billing_world() as (app, _):
        res = await call(app, key, "GET", OWNER_URL)
    assert res.status_code == 403 and res.json()["code"] == "scope_forbidden"


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_is_sent_to_the_admin_routes(key: str) -> None:
    with billing_world() as (app, _):
        res = await call(app, key, "GET", OWNER_URL)
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


async def test_no_credential_is_401() -> None:
    with billing_world() as (app, _):
        res = await call(app, None, "GET", OWNER_URL)
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["tk_a", "tk_b", "ck_a", "ck_b", "owner_a", "member_a"])
@pytest.mark.parametrize(("method", "template", "body"), ADMIN_ROUTES)
async def test_admin_billing_routes_refuse_every_workspace_credential(
    key: str, method: str, template: str, body: Any
) -> None:
    with billing_world() as (app, world):
        path = template.format(tenant_id=world.tenant_a.id, plan_id=uuid.uuid4())
        res = await call(app, key, method, path, json=body)
    assert res.status_code == 403, (key, path, res.text)


# BIL3 ledger
LEDGER_URL = "/api/v1/billing/ledger"


@pytest.mark.parametrize(("key", "tenant"), [("tk_a", "a"), ("tk_b", "b"), ("owner_a", "a")])
async def test_an_owner_reads_only_their_own_transactions(key: str, tenant: str) -> None:
    with billing_world() as (app, _):
        res = await call(app, key, "GET", LEDGER_URL)
    assert res.status_code == 200, res.text
    assert res.json() == {"items": [], "total": 0}


async def test_a_member_is_refused_the_transactions() -> None:
    with billing_world() as (app, _):
        res = await call(app, "member_a", "GET", LEDGER_URL)
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


@pytest.mark.parametrize("key", ["ck_a", "ck_b"])
async def test_a_knowledge_base_key_is_refused_the_transactions(key: str) -> None:
    with billing_world() as (app, _):
        res = await call(app, key, "GET", LEDGER_URL)
    assert res.status_code == 403 and res.json()["code"] == "scope_forbidden"


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_uses_the_admin_transactions(key: str) -> None:
    with billing_world() as (app, _):
        res = await call(app, key, "GET", LEDGER_URL)
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


async def test_the_transactions_need_a_credential() -> None:
    with billing_world() as (app, _):
        res = await call(app, None, "GET", LEDGER_URL)
    assert res.status_code == 401


# /BIL3 ledger


# BIL6 statements
STATEMENTS_URL = "/api/v1/billing/statements"
ADMIN_STATEMENT_ROUTES = [
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/statements", None),
    ("GET", "/api/v1/admin/billing/accounts/{tenant_id}/statements/2026-10", None),
    ("GET", "/api/v1/admin/billing/pnl", None),
]


@contextmanager
def statements_world() -> Iterator[tuple[Any, World, list[uuid.UUID]]]:
    """The scoping harness with the statement builder answering from the account it is given,
    so which workspace the route asked about is visible in the answer."""
    from datetime import UTC, date, datetime

    from callobuzz_rag.api.routes import billing_statements
    from callobuzz_rag.billing import statements

    asked: list[uuid.UUID] = []

    async def account(session: Any, tenant_id: uuid.UUID) -> BillingAccount:
        asked.append(tenant_id)
        return BillingAccount(id=uuid.uuid4(), tenant_id=tenant_id, currency="INR")

    async def statement_for(
        session: Any, acct: BillingAccount, month: date, *, now: Any = None
    ) -> statements.StatementData:
        marker = acct.tenant_id.int % 1_000_000
        return statements.build_statement(
            [],
            [],
            opening=marker,
            residue_start=0,
            residue_end=0,
            names=statements.Names(),
            month=month,
            now=datetime(2026, 10, 8, tzinfo=UTC),
        )

    async def summaries(
        session: Any, acct: BillingAccount, *, now: Any = None, limit: Any = None
    ) -> list[statements.MonthSummary]:
        return [statements.MonthSummary(date(2026, 10, 1), acct.tenant_id.int % 1_000_000, 0, True)]

    with (
        scoped_app() as (app, world),
        patch.object(billing_statements, "_account", account),
        patch.object(statements, "statement_for", statement_for),
        patch.object(statements, "summaries", summaries),
    ):
        yield app, world, asked


@pytest.mark.parametrize(("key", "tenant"), [("tk_a", "a"), ("tk_b", "b"), ("owner_a", "a")])
async def test_an_owner_reads_only_their_own_statements(key: str, tenant: str) -> None:
    with statements_world() as (app, world, asked):
        listed = await call(app, key, "GET", STATEMENTS_URL)
        one = await call(app, key, "GET", f"{STATEMENTS_URL}/2026-10")
        own = (world.tenant_a if tenant == "a" else world.tenant_b).id
    assert listed.status_code == 200 and one.status_code == 200, (listed.text, one.text)
    marker = own.int % 1_000_000
    assert listed.json()["items"][0]["opening_minor"] == marker
    assert one.json()["opening_minor"] == marker
    assert set(asked) == {own}  # there is no tenant id in the path to change


async def test_a_member_is_refused_the_statements() -> None:
    with statements_world() as (app, _, asked):
        listed = await call(app, "member_a", "GET", STATEMENTS_URL)
        one = await call(app, "member_a", "GET", f"{STATEMENTS_URL}/2026-10")
    for res in (listed, one):
        assert res.status_code == 403 and res.json()["code"] == "owner_required"
    assert asked == []


@pytest.mark.parametrize("key", ["ck_a", "ck_b"])
async def test_a_knowledge_base_key_is_refused_the_statements(key: str) -> None:
    with statements_world() as (app, _, asked):
        listed = await call(app, key, "GET", STATEMENTS_URL)
        one = await call(app, key, "GET", f"{STATEMENTS_URL}/2026-10")
    for res in (listed, one):
        assert res.status_code == 403 and res.json()["code"] == "scope_forbidden"
    assert asked == []


@pytest.mark.parametrize("key", ["admin", "sa"])
async def test_the_admin_uses_the_admin_statements(key: str) -> None:
    with statements_world() as (app, _, _):
        res = await call(app, key, "GET", STATEMENTS_URL)
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


async def test_the_statements_need_a_credential() -> None:
    with statements_world() as (app, _, _):
        res = await call(app, None, "GET", f"{STATEMENTS_URL}/2026-10")
    assert res.status_code == 401


@pytest.mark.parametrize("key", ["tk_a", "tk_b", "ck_a", "ck_b", "owner_a", "member_a"])
@pytest.mark.parametrize(("method", "template", "body"), ADMIN_STATEMENT_ROUTES)
async def test_admin_statement_routes_refuse_every_workspace_credential(
    key: str, method: str, template: str, body: Any
) -> None:
    with statements_world() as (app, world, asked):
        path = template.format(tenant_id=world.tenant_a.id)
        res = await call(app, key, method, path, json=body)
    assert res.status_code == 403, (key, path, res.text)
    assert asked == []


# /BIL6 statements
