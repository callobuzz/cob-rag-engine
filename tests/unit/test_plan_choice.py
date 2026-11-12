"""Self-service plan choice (P6): the proration rule, the sign-up `?plan=` link, the owner's
routes (members refused, a demo refused, billing off), and the public plan list.

The money flows themselves (an upgrade charged once, a payment that settles a waiting move, a
downgrade at the renewal) run on PostgreSQL in `tests/integration/test_plan_change_db.py`. Here
the routes go through the real app on the in-memory database of `accounts_fakes.py`.
"""

import random
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.billing import plan_change
from callobuzz_rag.billing.plan_change import (
    classify,
    pay_amount,
    per_period,
    plan_slug,
    prorate,
    shortfall,
    split_difference,
)
from callobuzz_rag.billing.pricing import PricingFields
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.demo.guard import refusal
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World, auth_app, login, req
from tests.unit.test_signup import signup, verify

NEW = "lata@kaveri.example"


# ---------------------------------------------------------------- the proration rule


def test_prorate_is_integer_never_negative_and_never_more_than_the_difference() -> None:
    rng = random.Random(6)
    for _ in range(5_000):
        diff = rng.randint(-1_000_000, 10_000_000)
        total = rng.randint(0, 366)
        remaining = rng.randint(-5, 400)
        got = prorate(diff, total, remaining)
        assert type(got) is int
        assert 0 <= got <= max(diff, 0)
        now, rest = split_difference(diff, total, remaining)
        assert now == got and now + rest == max(diff, 0) and rest >= 0


def test_prorate_grows_with_the_days_left_and_charges_the_whole_difference_on_day_one() -> None:
    rng = random.Random(7)
    for _ in range(2_000):
        diff = rng.randint(1, 5_000_000)
        total = rng.randint(28, 31)
        shares = [prorate(diff, total, days) for days in range(total + 1)]
        assert shares == sorted(shares)
        assert shares[total] == diff and shares[0] == 0
        assert prorate(diff, total, total + 9) == diff  # never more than a whole period


def test_the_documented_example() -> None:
    # ₹999 a month more, 16 of 31 days left: ₹515.61, floored to the paisa
    assert prorate(99_900, 31, 16) == 51_561
    assert per_period(1_200_000, "yearly", "monthly") == 100_000
    assert per_period(99_900, "monthly", "yearly") == 1_198_800
    assert classify(0, 99_900) == "upgrade" and classify(99_900, 0) == "downgrade"
    assert classify(500, 500) == "switch"


def test_shortfall_keeps_a_prepaid_balance_above_the_pause_line() -> None:
    prepaid = PricingFields(payment_type="prepaid")
    assert shortfall(prepaid, 0, 1_000) == 1_001
    assert shortfall(prepaid, 5_000, 1_000) == 0
    assert plan_change.covers(prepaid, 1_001, 1_000)
    assert not plan_change.covers(prepaid, 1_000, 1_000)
    graced = PricingFields(payment_type="prepaid", grace_minor=500)
    assert shortfall(graced, 0, 1_000) == 1_000
    postpaid = PricingFields(payment_type="postpaid", credit_limit_minor=10_000)
    assert shortfall(postpaid, 0, 5_000) == 0 and shortfall(postpaid, 0, 12_000) == 2_001


def test_the_pay_page_amount_is_whole_units_and_at_least_the_smallest_top_up() -> None:
    assert pay_amount(1_001, minimum_minor=50_000, registered=True, prices_include_gst=False) == (
        50_000
    )
    assert pay_amount(
        258_351, minimum_minor=50_000, registered=False, prices_include_gst=False
    ) == (258_400)
    # prices that include GST: the total typed must leave the amount after tax
    got = pay_amount(100_000, minimum_minor=100, registered=True, prices_include_gst=True)
    from callobuzz_rag.billing.invoicing import gst

    assert got % 100 == 0
    assert all(
        gst.split_inclusive(got, k, "27").taxable_minor >= 100_000 for k in ("intra", "inter")
    )


def test_slugs() -> None:
    assert plan_slug("Pro") == "pro" and plan_slug("Growth Plus!") == "growth-plus"


# ---------------------------------------------------------------- the world


@pytest.fixture(autouse=True)
def _fresh_settings_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


@pytest.fixture
def world() -> World:
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.db.add(stored_pricing(w.db.default_plan, subscription_fee_minor=0))
    return w


def plan(world: World, name: str, *, fee: int = 0, self_service: bool = True, **kw: Any) -> Plan:
    row = world.db.add(Plan(name=name, is_active=True, self_service=self_service, **kw))
    world.db.add(stored_pricing(row, subscription_fee_minor=fee))
    return row


def open_mode(world: World) -> None:
    world.db.rows[EngineSettingsRow] = []
    world.db.add(EngineSettingsRow(id=1, value={"signup_mode": "open"}))
    invalidate_engine_settings_cache()


@contextmanager
def billing(on: bool) -> Iterator[None]:
    with (
        patch("callobuzz_rag.billing.plan_change.billing_enabled", lambda: on),
        patch("callobuzz_rag.api.routes.billing_plans.billing_enabled", lambda: on),
    ):
        yield


async def call(app: Any, method: str, path: str, email: str, **kw: Any) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, method, path, token=token, **kw)


def new_tenant(world: World) -> Tenant:
    (tenant,) = [t for t in world.db.all(Tenant) if t.name == "Kaveri Foods"]
    return tenant


# ---------------------------------------------------------------- sign-up with ?plan=


async def test_a_free_plan_from_the_pricing_page_is_the_new_workspaces_plan(world: World) -> None:
    starter = plan(world, "Starter")
    open_mode(world)
    with auth_app(world) as app, fake_accounts(world.db):
        assert (await signup(app, NEW, plan="starter")).status_code == 202
        done = await verify(app, world, NEW)
    assert done.status_code == 200, done.text
    assert new_tenant(world).plan_id == starter.id
    choice = done.json()["plan_choice"]
    assert choice["status"] == "active" and choice["plan_name"] == "Starter"


async def test_a_paid_plan_waits_for_the_owners_payment_and_charges_nothing(world: World) -> None:
    pro = plan(world, "Pro", fee=499_900)
    open_mode(world)
    with auth_app(world) as app, fake_accounts(world.db), billing(True):
        assert (await signup(app, NEW, plan=str(pro.id))).status_code == 202
        done = await verify(app, world, NEW)
    assert done.status_code == 200, done.text
    tenant = new_tenant(world)
    assert tenant.plan_id == world.db.default_plan.id  # nothing applies until paid
    choice = done.json()["plan_choice"]
    assert choice["status"] == "awaiting_payment" and choice["amount_due_minor"] == 499_900
    assert world.audits("billing.plan_change_awaiting_payment")


@pytest.mark.parametrize(
    "ref",
    ["no-such-plan", str(uuid.uuid4()), "hidden", "retired", "", "pro; drop table plans"],
)
async def test_an_unknown_or_foreign_plan_falls_back_to_the_default(world: World, ref: str) -> None:
    plan(world, "Hidden", self_service=False)
    world.db.add(Plan(name="Retired", is_active=False, self_service=True))
    open_mode(world)
    with auth_app(world) as app, fake_accounts(world.db):
        assert (await signup(app, NEW, plan=ref)).status_code == 202
        done = await verify(app, world, NEW)
    assert done.status_code == 200, done.text
    assert new_tenant(world).plan_id == world.db.default_plan.id
    assert done.json().get("plan_choice") is None


async def test_a_paid_plan_with_billing_off_starts_on_the_default(world: World) -> None:
    pro = plan(world, "Pro", fee=499_900)
    open_mode(world)
    with auth_app(world) as app, fake_accounts(world.db), billing(False):
        await signup(app, NEW, plan=str(pro.id))
        done = await verify(app, world, NEW)
    assert new_tenant(world).plan_id == world.db.default_plan.id
    assert done.json().get("plan_choice") is None


async def test_in_approval_mode_the_plan_waits_for_the_approval(world: World) -> None:
    starter = plan(world, "Starter")
    with auth_app(world) as app, fake_accounts(world.db):
        await signup(app, NEW, plan="Starter")
        done = await verify(app, world, NEW)
        tenant = new_tenant(world)
        assert done.json()["plan_choice"]["status"] == "on_approval"
        assert tenant.plan_id == world.db.default_plan.id
        assert tenant.requested_plan_id == starter.id
        from tests.unit.test_auth_routes import ADMIN
        from tests.unit.test_billing_pending_changes import plain_tenant

        with patch("callobuzz_rag.api.routes.tenants._admin_tenant", plain_tenant(world)):
            res = await req(
                app, "POST", f"/api/v1/admin/tenants/{tenant.id}/activate", key=ADMIN, json={}
            )
    assert res.status_code == 200, res.text
    assert tenant.plan_id == starter.id and tenant.requested_plan_id is None


# ---------------------------------------------------------------- the owner's routes


async def test_members_cannot_see_or_change_plans(world: World) -> None:
    pro = plan(world, "Pro", fee=499_900)
    with auth_app(world) as app, fake_accounts(world.db):
        for method, path, body in (
            ("GET", "/api/v1/billing/plans", None),
            ("POST", "/api/v1/billing/plan", {"plan_id": str(pro.id)}),
            ("DELETE", "/api/v1/billing/plan/pending", None),
        ):
            res = await call(app, method, path, "ravi@shree.example", json=body)
            assert res.status_code == 403, (method, path, res.text)
    assert world.shree.plan_id == world.db.default_plan.id


async def test_with_billing_off_a_free_plan_applies_and_a_paid_one_is_refused(
    world: World,
) -> None:
    starter = plan(world, "Starter")
    pro = plan(world, "Pro", fee=499_900)
    with auth_app(world) as app, fake_accounts(world.db), billing(False):
        listing = await call(app, "GET", "/api/v1/billing/plans", "asha@shree.example")
        paid = await call(
            app, "POST", "/api/v1/billing/plan", "asha@shree.example", json={"plan_id": str(pro.id)}
        )
        free = await call(
            app,
            "POST",
            "/api/v1/billing/plan",
            "asha@shree.example",
            json={"plan_id": str(starter.id)},
        )
    assert listing.status_code == 200, listing.text
    by_name = {i["name"]: i for i in listing.json()["items"]}
    assert listing.json()["billing_on"] is False
    assert by_name["Pro"]["available"] is False
    assert by_name["Pro"]["unavailable_reason"] == "needs_billing"
    assert by_name["Starter"]["available"] is True and by_name["Starter"]["price"] is None
    assert paid.status_code == 409 and paid.json()["code"] == "plan_change_needs_billing"
    assert free.status_code == 200 and free.json()["status"] == "applied"
    assert world.shree.plan_id == starter.id
    assert world.audits("tenant.plan_changed")


async def test_a_plan_that_is_not_self_service_cannot_be_chosen(world: World) -> None:
    hidden = plan(world, "Hidden", self_service=False)
    with auth_app(world) as app, fake_accounts(world.db), billing(False):
        res = await call(
            app,
            "POST",
            "/api/v1/billing/plan",
            "asha@shree.example",
            json={"plan_id": str(hidden.id)},
        )
    assert res.status_code == 404 and res.json()["code"] == "plan_not_found"


def test_a_demo_refuses_the_plan_routes() -> None:
    assert refusal("POST", "/api/v1/billing/plan", admin_key=False) == "billing"
    assert refusal("DELETE", "/api/v1/billing/plan/pending", admin_key=False) is not None
    assert refusal("GET", "/api/v1/billing/plans", admin_key=False) is None
    assert refusal("GET", "/api/v1/public/plans", admin_key=False) is None


async def test_the_public_list_shows_only_self_service_plans(world: World) -> None:
    plan(world, "Starter")
    plan(world, "Pro", fee=499_900)
    plan(world, "Hidden", self_service=False)
    with auth_app(world) as app, billing(True):
        res = await req(app, "GET", "/api/v1/public/plans")
    assert res.status_code == 200, res.text
    items = res.json()["items"]
    assert [(i["name"], i["slug"], i["free"]) for i in items] == [
        ("Starter", "starter", True),
        ("Pro", "pro", False),
    ]
    assert items[1]["price"]["subscription_fee_minor"] == 499_900
    with auth_app(world) as app, billing(False):
        off = await req(app, "GET", "/api/v1/public/plans")
    assert [i["name"] for i in off.json()["items"]] == ["Starter"]


async def _approve(world: World, app: Any, tenant: Tenant, body: dict[str, Any]) -> Any:
    from tests.unit.test_auth_routes import ADMIN
    from tests.unit.test_billing_pending_changes import plain_tenant

    with patch("callobuzz_rag.api.routes.tenants._admin_tenant", plain_tenant(world)):
        return await req(
            app, "POST", f"/api/v1/admin/tenants/{tenant.id}/activate", key=ADMIN, json=body
        )


async def test_the_operator_keeping_the_chosen_paid_plan_never_charges_on_its_own(
    world: World,
) -> None:
    pro = plan(world, "Pro", fee=499_900)
    with auth_app(world) as app, fake_accounts(world.db), billing(True):
        await signup(app, NEW, plan="pro")
        await verify(app, world, NEW)
        tenant = new_tenant(world)
        res = await _approve(world, app, tenant, {"plan_id": str(pro.id)})
    assert res.status_code == 200, res.text
    assert tenant.plan_id == world.db.default_plan.id  # it waits for the owner's payment
    assert world.audits("billing.plan_change_awaiting_payment")


async def test_the_operators_other_plan_wins_over_the_owners_choice(world: World) -> None:
    plan(world, "Starter")
    other = plan(world, "Clinic", self_service=False)
    with auth_app(world) as app, fake_accounts(world.db):
        await signup(app, NEW, plan="starter")
        await verify(app, world, NEW)
        tenant = new_tenant(world)
        res = await _approve(world, app, tenant, {"plan_id": str(other.id)})
    assert res.status_code == 200, res.text
    assert tenant.plan_id == other.id and tenant.requested_plan_id is None
