"""The owner's "Plan & billing" extras (BIL6): this month's usage, the statements list, the next
charge, the top-up request and its once-an-hour rule, and the header meter. Aggregate SQL runs on
PostgreSQL (`tests/integration/test_billing_statements_db.py`); here the aggregates are stood in."""

import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.accounts.email.render import validate_context
from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing import summary
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, SA_EMAIL, World, auth_app, login, req

ACCOUNT = "/api/v1/billing/account"
TOP_UP = "/api/v1/billing/top-up-requests"
CONSUMPTION = "/api/v1/usage/consumption"


@pytest.fixture
def world() -> World:
    world = World()
    world.db.add(
        stored_pricing(
            world.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
            payment_type="prepaid",
            grace_minor=0,
        )
    )
    world.member(world.user("asha@shree.example"), world.shree, "owner")
    world.member(world.user("ravi@shree.example"), world.shree, "member")
    return world


@contextmanager
def app_for(world: World, **settings: Any) -> Iterator[Any]:
    """The app with the SQL the stand-in database cannot run replaced: this month's usage and
    the statements list answer from `app.state.stub`."""
    stub = {
        "usage": summary.UsagePeriod(questions=42, charged_minor=8_400, unpriced=2),
        "refs": [
            {"month": "2026-10", "closing_minor": 124_050, "live": True},
            {"month": "2026-09", "closing_minor": 90_000, "live": False},
        ],
    }

    async def usage(session: Any, account: Any, now: Any = None) -> Any:
        return stub["usage"]

    async def refs(session: Any, account: Any, now: Any = None) -> list[Any]:
        return stub["refs"]

    with (
        auth_app(world, **settings) as app,
        fake_accounts(world.db),
        patch.object(summary, "usage_this_period", usage),
        patch.object(summary, "recent_statements", refs),
    ):
        app.state.stub = stub
        yield app


async def token(app: Any, email: str) -> str:
    return str((await login(app, email)).json()["access_token"])


# ------------------------------------------------------------------ the owner's view


async def test_an_owner_sees_usage_so_far_the_next_charge_and_the_statements(
    world: World,
) -> None:
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=4,
                next_charge_on=date(2026, 11, 1),
                trial_ends_at=datetime(2026, 10, 20, tzinfo=UTC),
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["usage_this_period"] == {"questions": 42, "charged_minor": 8_400, "unpriced": 2}
    assert body["next_charge_on"] == "2026-11-01"
    assert body["trial_ends_at"].startswith("2026-10-20")
    assert body["statements"] == [
        {"month": "2026-10", "closing_minor": 124_050, "live": True},
        {"month": "2026-09", "closing_minor": 90_000, "live": False},
    ]


async def test_the_view_says_whether_the_plan_charges_anything(world: World) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    with app_for(world) as app:
        headers = {"token": await token(app, "asha@shree.example")}
        billed = await req(app, "GET", ACCOUNT, **headers)
        for pricing in list(world.db.all(PlanPricing)):
            world.db.rows[PlanPricing].remove(pricing)
        free = await req(app, "GET", ACCOUNT, **headers)
    assert billed.json()["billed"] is True
    assert free.json()["billed"] is False


async def test_a_member_sees_how_many_questions_but_no_money_or_statements(
    world: World,
) -> None:
    with app_for(world) as app:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id, currency="INR", next_charge_on=date(2026, 11, 1)
            )
        )
        res = await req(app, "GET", ACCOUNT, token=await token(app, "ravi@shree.example"))
    body = res.json()
    assert body["money_visible"] is False
    assert body["usage_this_period"] == {"questions": 42, "charged_minor": None, "unpriced": None}
    assert body["next_charge_on"] is None and body["trial_ends_at"] is None
    assert body["statements"] == []


async def test_members_who_may_see_money_see_the_amounts_but_never_the_statements(
    world: World,
) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    with app_for(world) as app:
        for pricing in world.db.all(PlanPricing):
            pricing.members_see_money = True
        world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=2))
        res = await req(app, "GET", ACCOUNT, token=await token(app, "ravi@shree.example"))
    body = res.json()
    assert body["usage_this_period"]["charged_minor"] == 8_400
    assert body["statements"] == []  # statements are for owners only


async def test_markup_and_cost_do_not_creep_into_the_view(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", ACCOUNT, token=await token(app, "asha@shree.example"))
    text = res.text.lower()
    assert "markup" not in text and "cost_usd" not in text


# ------------------------------------------------------------------ requesting a top-up


async def ask(app: Any, who: str, body: dict[str, Any] | None = None) -> Any:
    return await req(app, "POST", TOP_UP, token=await token(app, who), json=body or {})


async def test_a_top_up_request_is_mailed_to_the_super_admin_and_audited(world: World) -> None:
    with app_for(world) as app:
        res = await ask(app, "asha@shree.example", {"amount_minor": 500_000, "note": "UPI sent"})
    assert res.status_code == 202, res.text
    assert res.json() == {"sent": True}
    [(template, to, ctx)] = world.mail
    assert template == "billing_top_up_request" and to == SA_EMAIL
    validate_context(template, to, ctx)  # the real template accepts exactly this context
    assert ctx["tenant_name"] == "Shree Labs" and ctx["owner_email"] == "asha@shree.example"
    assert ctx["amount"] == "₹5,000.00" and ctx["note"] == "UPI sent"
    assert ctx["account_url"].endswith(f"/admin/customers/{world.shree.id}?tab=billing")
    [audit] = world.audits("billing.top_up_requested")
    assert audit.tenant_id == world.shree.id
    assert audit.details == {"amount_minor": 500_000, "currency": "INR"}
    assert audit.actor_label == "asha@shree.example"


async def test_the_amount_and_the_note_are_optional(world: World) -> None:
    with app_for(world) as app:
        res = await ask(app, "asha@shree.example")
    assert res.status_code == 202
    ctx = world.mail[0][2]
    assert ctx["amount"] == "" and ctx["note"] == ""


async def test_a_second_request_within_the_hour_is_refused_politely(world: World) -> None:
    with app_for(world) as app:
        first = await ask(app, "asha@shree.example", {"amount_minor": 100})
        second = await ask(app, "asha@shree.example", {"amount_minor": 100})
    assert first.status_code == 202
    assert second.status_code == 429 and second.json()["code"] == "topup_request_too_soon"
    assert 1 <= int(second.headers["retry-after"]) <= 3600
    assert second.json()["retry_after_s"] == int(second.headers["retry-after"])
    assert len(world.mail) == 1 and len(world.audits("billing.top_up_requested")) == 1


async def test_the_hour_is_per_workspace_and_ends(world: World) -> None:
    other = world.pune
    world.member(world.user("meera@pune.example"), other, "owner")
    with app_for(world) as app:
        assert (await ask(app, "asha@shree.example")).status_code == 202
        assert (await ask(app, "meera@pune.example")).status_code == 202  # another workspace
        for row in world.db.all(AuditLog):
            row.created_at = datetime.now(UTC) - timedelta(minutes=61)
        again = await ask(app, "asha@shree.example")
    assert again.status_code == 202


async def test_a_member_cannot_ask(world: World) -> None:
    with app_for(world) as app:
        res = await ask(app, "ravi@shree.example")
    assert res.status_code == 403 and res.json()["code"] == "owner_required"
    assert world.mail == []
    assert world.audits("billing.top_up_requested") == []


async def test_without_an_operator_address_it_is_audited_and_says_it_was_not_sent(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    with app_for(world, superadmin_email="") as app, caplog.at_level(logging.WARNING):
        res = await ask(app, "asha@shree.example", {"amount_minor": 100})
    assert res.status_code == 202 and res.json() == {"sent": False}
    assert world.mail == []
    assert len(world.audits("billing.top_up_requested")) == 1
    assert any("no operator e-mail" in r.message for r in caplog.records)


async def test_a_broken_queue_does_not_fail_the_customer(world: World) -> None:
    def broker_down(*args: Any, **kwargs: Any) -> None:
        raise OSError("broker down")

    with (
        app_for(world) as app,
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", broker_down),
    ):
        res = await ask(app, "asha@shree.example")
    assert res.status_code == 202 and res.json() == {"sent": False}
    assert len(world.audits("billing.top_up_requested")) == 1


async def test_the_operators_own_workspace_has_nothing_to_top_up(world: World) -> None:
    world.shree.is_operator = True
    with app_for(world) as app:
        res = await ask(app, "asha@shree.example")
    assert res.status_code == 409 and res.json()["code"] == "billing_exempt"
    assert world.mail == []


@pytest.mark.parametrize(
    "body",
    [
        {"amount_minor": 0},
        {"amount_minor": -5},
        {"amount_minor": 10**13},
        {"note": "x" * 501},
        {"amount": 100},  # a field that is not in the body
        {"amount_minor": 1.5},
    ],
)
async def test_a_bad_body_is_422(world: World, body: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await ask(app, "asha@shree.example", body)
    assert res.status_code == 422
    assert world.mail == []


async def test_the_admin_is_sent_to_the_admin_pages(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "POST", TOP_UP, key=ADMIN, json={})
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


async def test_with_billing_off_it_is_404(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                res = await ask(app, "asha@shree.example")
        finally:
            billing_config.reset_billing_config_cache()
    assert res.status_code == 404 and res.json()["code"] == "billing_disabled"


# ------------------------------------------------------------------ the header meter


async def consumption(app: Any, **kw: Any) -> Any:
    data = {
        "view": "operator_key",
        "spend_usd": None,
        "spend_known": True,
        "unpriced_requests": 0,
        "questions": 3,
        "question_limit": 100,
    }

    async def fake(session: Any, tenant_id: uuid.UUID, today: date) -> dict[str, Any]:
        return dict(data)

    with patch("callobuzz_rag.api.routes.usage.tenant_consumption", fake):
        return await req(app, "GET", CONSUMPTION, **kw)


def with_account(world: World, **fields: Any) -> BillingAccount:
    return world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", **fields))


async def test_a_prepaid_owner_gets_the_balance_in_the_header(world: World) -> None:
    with app_for(world) as app:
        with_account(world, balance_minor=124_050, ledger_seq=2, last_top_up_minor=500_000)
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    assert res.status_code == 200, res.text
    assert res.json()["billing"] == {
        "payment_type": "prepaid",
        "currency": "INR",
        "money_visible": True,
        "available_minor": 124_050,
        "credit_limit_minor": None,
        "paused": False,
        "alert_level": None,
    }
    assert res.json()["questions"] == 3  # the questions chip's data is still there


async def test_a_low_balance_carries_its_alert_level(world: World) -> None:
    with app_for(world) as app:
        with_account(world, balance_minor=20_000, ledger_seq=2, last_top_up_minor=500_000)
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    assert res.json()["billing"]["alert_level"] == 5 and res.json()["billing"]["paused"] is False


async def test_an_empty_balance_is_paused(world: World) -> None:
    with app_for(world) as app:
        with_account(world, balance_minor=0, ledger_seq=2, last_top_up_minor=500_000)
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    meter = res.json()["billing"]
    assert meter["paused"] is True and meter["alert_level"] is None
    assert meter["available_minor"] == 0


async def test_a_postpaid_owner_gets_credit_used_and_the_limit(world: World) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    with app_for(world) as app:
        for pricing in world.db.all(PlanPricing):
            pricing.payment_type = "postpaid"
            pricing.credit_limit_minor = 1_000_000
        with_account(world, balance_minor=-300_000, ledger_seq=3)
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    meter = res.json()["billing"]
    assert meter["payment_type"] == "postpaid"
    assert meter["available_minor"] == -300_000 and meter["credit_limit_minor"] == 1_000_000
    assert meter["paused"] is False


async def test_a_member_gets_no_amounts_unless_the_plan_allows(world: World) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    with app_for(world) as app:
        with_account(world, balance_minor=124_050, ledger_seq=2)
        headers = {"Authorization": f"Bearer {await token(app, 'ravi@shree.example')}"}
        hidden = await consumption(app, headers=headers)
        for pricing in world.db.all(PlanPricing):
            pricing.members_see_money = True
        shown = await consumption(app, headers=headers)
    assert hidden.json()["billing"]["money_visible"] is False
    assert hidden.json()["billing"]["available_minor"] is None
    assert shown.json()["billing"]["money_visible"] is True
    assert shown.json()["billing"]["available_minor"] == 124_050


async def test_a_workspace_that_is_not_billed_has_no_meter(world: World) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    for pricing in list(world.db.all(PlanPricing)):
        world.db.rows[PlanPricing].remove(pricing)
    with app_for(world) as app:
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    assert res.status_code == 200 and res.json()["billing"] is None


async def test_the_platform_view_has_no_meter(world: World) -> None:
    async def platform(session: Any, today: date) -> dict[str, Any]:
        return {
            "view": "platform",
            "spend_usd": 1.0,
            "spend_known": True,
            "unpriced_requests": 0,
            "questions": 1,
            "question_limit": None,
        }

    with (
        app_for(world) as app,
        patch("callobuzz_rag.api.routes.usage.platform_consumption", platform),
        patch(
            "callobuzz_rag.api.routes.usage.get_openrouter_balance",
            lambda settings: _none(),
        ),
        patch(
            "callobuzz_rag.api.routes.provider_balances.provider_summary",
            lambda settings, session: _none(),
        ),
    ):
        res = await req(app, "GET", CONSUMPTION, key=ADMIN)
    assert res.status_code == 200, res.text
    assert res.json()["billing"] is None


async def _none() -> None:
    return None


async def test_a_billing_problem_never_breaks_the_header(world: World) -> None:
    async def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("books unavailable")

    with app_for(world) as app, patch.object(summary, "billing_meter", boom):
        res = await consumption(
            app, headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"}
        )
    assert res.status_code == 200 and res.json()["billing"] is None
    assert res.json()["questions"] == 3


async def test_with_billing_off_there_is_no_meter(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                with_account(world, balance_minor=1_000)
                res = await consumption(
                    app,
                    headers={"Authorization": f"Bearer {await token(app, 'asha@shree.example')}"},
                )
        finally:
            billing_config.reset_billing_config_cache()
    assert res.status_code == 200 and res.json()["billing"] is None
