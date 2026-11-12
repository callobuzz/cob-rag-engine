"""Price changes through the admin API (TV3): a raise is scheduled and announced, never applied
inside the period it was announced in; a drop may start at once; a plan can be cancelled back."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

PRICING = "/api/v1/admin/billing/plans/{}/pricing"


@pytest.fixture
def world() -> World:
    w = World()
    w.db.add(
        stored_pricing(
            w.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
        )
    )
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("dev@pune.example"), w.pune, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    return w


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app, fake_accounts(world.db):
        yield app


async def put(app: Any, world: World, **body: Any) -> Any:
    payload = {
        "usage_mode": "per_question",
        "per_question_minor": 200,
        "subscription_fee_minor": 99_900,
        **body,
    }
    return await req(app, "PUT", PRICING.format(world.db.default_plan.id), key=ADMIN, json=payload)


def versions(world: World) -> list[PlanPricing]:
    return sorted(world.db.all(PlanPricing), key=lambda r: r.version)


async def test_a_raise_applied_now_is_refused_with_the_earliest_day(world: World) -> None:
    with app_for(world) as app:
        res = await put(app, world, subscription_fee_minor=129_900)
    assert res.status_code == 422 and res.json()["code"] == "price_change_not_lower"
    assert len(versions(world)) == 1 and world.mail == []


async def test_a_raise_with_too_little_notice_is_refused(world: World) -> None:
    soon = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    with app_for(world) as app:
        res = await put(
            app, world, subscription_fee_minor=129_900, apply="scheduled", effective_at=soon
        )
    body = res.json()
    assert res.status_code == 422 and body["code"] == "price_change_notice_too_short"
    assert "earliest" in body


async def test_a_scheduled_raise_leaves_the_current_price_and_tells_every_owner(
    world: World,
) -> None:
    with app_for(world) as app:
        res = await put(app, world, subscription_fee_minor=129_900, apply="scheduled")
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["is_current"] is True and body["subscription_fee_minor"] == 99_900
    assert body["scheduled"]["subscription_fee_minor"] == 129_900
    assert body["scheduled"]["is_current"] is False and body["impact"] == "higher"
    assert body["scheduled"]["effective_at"] is not None
    rows = versions(world)
    assert [r.is_current for r in rows] == [True, False]
    [audit] = world.audits("billing.price_change_scheduled")
    assert audit.details["workspaces"] == 2 and audit.details["impact"] == "higher"
    assert world.audits("billing.pricing_updated") == []
    sent = [(name, to) for name, to, _ in world.mail]
    assert sorted(sent) == [
        ("billing_price_change", "asha@shree.example"),
        ("billing_price_change", "dev@pune.example"),
    ]
    ctx = world.mail[0][2]
    assert ctx["lower"] is False and ctx["plan_name"] == "Standard"
    assert ctx["changes"] == [{"label": "Plan fee", "old": "₹999", "new": "₹1,299"}]
    assert len(world.db.all(BillingNotice)) == 2


async def test_saving_again_replaces_the_scheduled_change_and_tells_them(world: World) -> None:
    with app_for(world) as app:
        await put(app, world, subscription_fee_minor=129_900, apply="scheduled")
        world.mail.clear()
        res = await put(app, world, subscription_fee_minor=119_900, apply="scheduled")
    assert res.status_code == 200, res.text
    assert [r.version for r in versions(world)] == [1, 2]
    assert versions(world)[1].subscription_fee_minor == 119_900
    kinds = sorted(name for name, _, _ in world.mail)
    assert kinds == ["billing_price_change"] * 2 + ["billing_price_change_cancelled"] * 2
    assert len(world.audits("billing.price_change_cancelled")) == 1


async def test_a_drop_starts_at_once_and_is_good_news(world: World) -> None:
    with app_for(world) as app:
        res = await put(app, world, subscription_fee_minor=79_900)
    body = res.json()
    assert res.status_code == 200 and body["version"] == 2 and body["is_current"] is True
    assert body["scheduled"] is None and body["impact"] == "lower"
    assert len(world.audits("billing.pricing_updated")) == 1
    assert {to for _, to, _ in world.mail} == {"asha@shree.example", "dev@pune.example"}
    assert all(ctx["lower"] is True for _, _, ctx in world.mail)
    assert {n.kind for n in world.db.all(BillingNotice)} == {"plan_lower"}


async def test_preview_writes_nothing(world: World) -> None:
    with app_for(world) as app:
        res = await req(
            app,
            "POST",
            PRICING.format(world.db.default_plan.id) + "/preview",
            key=ADMIN,
            json={
                "usage_mode": "per_question",
                "per_question_minor": 300,
                "subscription_fee_minor": 99_900,
            },
        )
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["impact"] == "higher" and body["workspaces"] == 2
    assert body["changed"] == ["per_question_minor"] and body["earliest_effective_at"]
    assert len(versions(world)) == 1 and world.mail == []


async def test_cancelling_a_scheduled_change_tells_the_businesses(world: World) -> None:
    with app_for(world) as app:
        await put(app, world, subscription_fee_minor=129_900, apply="scheduled")
        world.mail.clear()
        gone = await req(
            app, "DELETE", PRICING.format(world.db.default_plan.id) + "/scheduled", key=ADMIN
        )
        again = await req(
            app, "DELETE", PRICING.format(world.db.default_plan.id) + "/scheduled", key=ADMIN
        )
    assert gone.status_code == 204
    assert again.status_code == 404 and again.json()["code"] == "scheduled_pricing_not_found"
    assert len(versions(world)) == 1
    assert {name for name, _, _ in world.mail} == {"billing_price_change_cancelled"}
    assert len(world.audits("billing.price_change_cancelled")) == 1


async def test_the_plan_shows_what_is_scheduled(world: World) -> None:
    with app_for(world) as app:
        await put(app, world, subscription_fee_minor=129_900, apply="scheduled")
        res = await req(app, "GET", PRICING.format(world.db.default_plan.id), key=ADMIN)
    body = res.json()
    assert body["subscription_fee_minor"] == 99_900
    assert body["scheduled"]["subscription_fee_minor"] == 129_900


async def test_a_queue_failure_does_not_undo_the_saved_change(world: World) -> None:
    from unittest.mock import patch

    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("queue down")

    with (
        app_for(world) as app,
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", broken),
    ):
        res = await put(app, world, subscription_fee_minor=129_900, apply="scheduled")
    assert res.status_code == 200 and len(versions(world)) == 2
    assert world.db.all(BillingNotice) == []  # nothing recorded, so a re-run would tell them
