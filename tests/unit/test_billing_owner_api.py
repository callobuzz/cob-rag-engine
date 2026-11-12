"""`GET /api/v1/billing/account`: a workspace's own terms; members see no money by default."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World, auth_app, login, req

URL = "/api/v1/billing/account"


@pytest.fixture
def world() -> World:
    world = World()
    world.db.add(
        stored_pricing(
            world.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="cost_plus",
            markup_bp=2500,
            per_question_minor=200,
            payment_type="postpaid",
            credit_limit_minor=500_000,
            included_credit_minor=10_000,
        )
    )
    return world


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app, fake_accounts(world.db):
        yield app


async def session_token(app: Any, email: str) -> str:
    return str((await login(app, email)).json()["access_token"])


def sign_in_as(world: World, tenant: Any, email: str, role: str) -> None:
    world.member(world.user(email), tenant, role)


async def test_owner_sees_everything_on_the_plan_and_the_account_is_created(world: World) -> None:
    sign_in_as(world, world.shree, "asha@shree.example", "owner")
    assert world.db.all(BillingAccount) == []
    with app_for(world) as app:
        res = await req(app, "GET", URL, token=await session_token(app, "asha@shree.example"))
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_id"] == str(world.shree.id) and body["money_visible"] is True
    assert body["currency"] == "INR" and body["exempt"] is False
    assert body["subscription_fee_minor"] == 99_900 and body["per_question_minor"] == 200
    assert body["credit_limit_minor"] == 500_000 and body["included_credit_minor"] == 10_000
    assert (body["payment_type"], body["usage_mode"], body["byok_mode"]) == (
        "postpaid",
        "cost_plus",
        "free",
    )
    assert body["plan"]["name"] == world.db.default_plan.name
    assert len(world.db.all(BillingAccount)) == 1


async def test_markup_and_notes_are_never_in_the_view(world: World) -> None:
    sign_in_as(world, world.shree, "asha@shree.example", "owner")
    world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", notes="pays late"))
    with app_for(world) as app:
        res = await req(app, "GET", URL, token=await session_token(app, "asha@shree.example"))
    assert res.status_code == 200
    text = res.text.lower()
    assert "markup" not in text and "2500" not in text
    assert "notes" not in text and "pays late" not in text
    assert "pause_override" not in text and "cost" not in text.replace("cost_plus", "")


async def test_a_member_sees_terms_but_no_money_unless_the_plan_allows(world: World) -> None:
    sign_in_as(world, world.shree, "ravi@shree.example", "member")
    money = (
        "subscription_fee_minor",
        "per_question_minor",
        "byok_per_question_minor",
        "overage_per_question_minor",
        "included_credit_minor",
        "credit_limit_minor",
    )
    with app_for(world) as app:
        token = await session_token(app, "ravi@shree.example")
        hidden = await req(app, "GET", URL, token=token)
        for pricing in world.db.all(PlanPricing):
            pricing.members_see_money = True
        shown = await req(app, "GET", URL, token=token)
    assert hidden.status_code == 200
    assert hidden.json()["money_visible"] is False
    assert all(hidden.json()[f] is None for f in money)
    assert (
        hidden.json()["payment_type"] == "postpaid" and hidden.json()["usage_mode"] == "cost_plus"
    )
    assert shown.json()["money_visible"] is True and shown.json()["per_question_minor"] == 200


async def test_an_account_override_can_hide_or_show_money_for_members(world: World) -> None:
    sign_in_as(world, world.shree, "ravi@shree.example", "member")
    world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            overrides={"members_see_money": True, "per_question_minor": 150},
        )
    )
    with app_for(world) as app:
        res = await req(app, "GET", URL, token=await session_token(app, "ravi@shree.example"))
    assert res.json()["money_visible"] is True and res.json()["per_question_minor"] == 150


async def test_each_workspace_sees_only_its_own_terms(world: World) -> None:
    sign_in_as(world, world.shree, "asha@shree.example", "owner")
    sign_in_as(world, world.pune, "dev@pune.example", "owner")
    world.db.add(
        BillingAccount(tenant_id=world.pune.id, currency="INR", overrides={"per_question_minor": 5})
    )
    with app_for(world) as app:
        shree = await req(app, "GET", URL, token=await session_token(app, "asha@shree.example"))
        pune = await req(app, "GET", URL, token=await session_token(app, "dev@pune.example"))
    assert (
        shree.json()["tenant_id"] == str(world.shree.id)
        and shree.json()["per_question_minor"] == 200
    )
    assert pune.json()["tenant_id"] == str(world.pune.id) and pune.json()["per_question_minor"] == 5


async def test_no_credential_is_refused_and_the_switch_hides_the_route(world: World) -> None:
    sign_in_as(world, world.shree, "asha@shree.example", "owner")
    with app_for(world) as app:
        anonymous = await req(app, "GET", URL)
        token = await session_token(app, "asha@shree.example")
    assert anonymous.status_code == 401
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                off = await req(app, "GET", URL, token=token)
        finally:
            billing_config.reset_billing_config_cache()
    assert off.status_code == 404 and off.json()["code"] == "billing_disabled"


# BIL5 add-ons
async def test_add_ons_in_force_are_listed_without_prices(world: World) -> None:
    from datetime import UTC, datetime, timedelta

    from callobuzz_rag.db.models.add_on import AddOn, AddOnGrant

    sign_in_as(world, world.shree, "asha@shree.example", "member")
    now = datetime.now(UTC)
    add_on = world.db.add(
        AddOn(
            kind="question_pack",
            name="500 extra questions",
            currency="INR",
            price_minor=29_900,
            questions=500,
        )
    )

    def grant(**kw: Any) -> None:
        world.db.add(
            AddOnGrant(
                tenant_id=world.shree.id,
                add_on_id=add_on.id,
                kind="question_pack",
                name="500 extra questions",
                currency="INR",
                price_minor=29_900,
                questions=500,
                payment="external",
                granted_by="admin key",
                starts_at=now - timedelta(days=1),
                **kw,
            )
        )

    grant(ends_at=now + timedelta(days=3))  # in force
    grant(ends_at=now - timedelta(hours=1))  # over
    grant(ends_at=now + timedelta(days=3), revoked_at=now)  # revoked
    world.db.add(
        AddOnGrant(
            tenant_id=world.shree.id,
            add_on_id=add_on.id,
            kind="credit_pack",
            name="Credit",
            currency="INR",
            price_minor=1,
            credit_minor=1,
            payment="external",
            granted_by="admin key",
            starts_at=now,
        )
    )
    with app_for(world) as app:
        res = await req(app, "GET", URL, token=await session_token(app, "asha@shree.example"))
    assert res.status_code == 200, res.text
    [item] = res.json()["add_ons"]
    assert item["name"] == "500 extra questions" and item["kind"] == "question_pack"
    assert item["summary"].startswith("+500 questions until ")
    assert set(item) == {"name", "kind", "summary", "ends_at"}
    assert "29900" not in res.text


async def test_no_add_ons_is_an_empty_list(world: World) -> None:
    sign_in_as(world, world.shree, "asha@shree.example", "owner")
    with app_for(world) as app:
        res = await req(app, "GET", URL, token=await session_token(app, "asha@shree.example"))
    assert res.json()["add_ons"] == []


# /BIL5 add-ons
