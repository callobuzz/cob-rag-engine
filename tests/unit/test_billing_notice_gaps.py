"""Gaps found in the review of the no-surprise billing epic (TV3): a first price on a plan with
businesses, free workspaces, announcements that failed to queue, the renewal date of a change of
period, held moves into a plan, a stale held raise, the renewal amount, a held move that can no
longer happen."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import accounts as billing_accounts
from callobuzz_rag.billing.notice import (
    announce_change,
    announce_scheduled_changes,
    apply_due_account_changes,
    hourly_pass,
    ist_midnight,
    pending_effect,
)
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, req
from tests.unit.test_billing_pending_changes import (
    app_for,
    billed_account,
    dearer_plan,
    move,
    patch_account,
)
from tests.unit.test_billing_price_change_routes import PRICING


def _world(*, priced: bool = True) -> World:
    w = World()
    if priced:
        w.db.add(
            stored_pricing(
                w.db.default_plan,
                subscription_fee_minor=99_900,
                usage_mode="per_question",
                per_question_minor=200,
            )
        )
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("meera@shree.example"), w.shree, "owner")
    w.member(w.user("dev@pune.example"), w.pune, "owner")
    return w


@pytest.fixture
def world() -> World:
    return _world()


@pytest.fixture
def free_world() -> World:
    """A plan with businesses on it and no pricing row: they pay nothing today."""
    return _world(priced=False)


def sent(w: World, template: str) -> list[tuple[str, dict[str, Any]]]:
    return [(to, ctx) for name, to, ctx in w.mail if name == template]


async def put_pricing(app: Any, w: World, **body: Any) -> Any:
    payload = {
        "usage_mode": "per_question",
        "per_question_minor": 200,
        "subscription_fee_minor": 99_900,
        **body,
    }
    return await req(app, "PUT", PRICING.format(w.db.default_plan.id), key=ADMIN, json=payload)


# ------------------------------------------------ TV3-01: the first price of a plan with businesses


async def test_a_first_price_on_a_plan_with_businesses_cannot_start_now(free_world: World) -> None:
    with app_for(free_world) as app:
        res = await put_pricing(app, free_world)
    assert res.status_code == 422 and res.json()["code"] == "price_change_not_lower"
    assert free_world.db.all(PlanPricing) == [] and free_world.mail == []


async def test_a_first_price_is_scheduled_and_every_owner_is_told(free_world: World) -> None:
    with app_for(free_world) as app:
        res = await put_pricing(app, free_world, apply="scheduled")
    assert res.status_code == 200, res.text
    [row] = free_world.db.all(PlanPricing)
    assert row.is_current is False and row.effective_at is not None
    assert res.json()["impact"] == "mixed" and res.json()["workspaces"] == 2
    told = sorted(to for to, _ in sent(free_world, "billing_price_change"))
    assert told == ["asha@shree.example", "dev@pune.example", "meera@shree.example"]
    changes = sent(free_world, "billing_price_change")[0][1]["changes"]
    assert {"label": "Plan fee", "old": "₹0", "new": "₹999"} in changes


async def test_a_first_price_on_a_plan_nobody_is_on_starts_at_once() -> None:
    w = _world(priced=False)
    w.db.rows[type(w.shree)].clear()
    with app_for(w) as app:
        res = await put_pricing(app, w)
    assert res.status_code == 200, res.text
    [row] = w.db.all(PlanPricing)
    assert row.is_current is True and w.mail == []


# ------------------------------------------------ TV3-02: a free workspace is not "never charged"


async def test_a_live_workspace_with_no_ledger_is_told_before_a_raise(world: World) -> None:
    account = world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=0,
            billing_day=12,
            next_charge_on=date.today() + timedelta(days=9),
        )
    )
    with app_for(world) as app:
        res = await patch_account(app, world, overrides={"subscription_fee_minor": 500_000})
    assert res.status_code == 200, res.text
    assert account.pending_change == {"overrides": {"subscription_fee_minor": 500_000}}
    assert res.json()["overrides"] == {}
    assert sorted(to for to, _ in sent(world, "billing_price_change")) == [
        "asha@shree.example",
        "meera@shree.example",
    ]


async def test_a_live_workspace_with_no_ledger_waits_for_a_dearer_plan(world: World) -> None:
    account = world.db.add(
        BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=0, billing_day=12)
    )
    pro = dearer_plan(world)
    before = world.shree.plan_id
    with app_for(world) as app:
        res = await move(app, world, pro)
    assert res.status_code == 200, res.text
    assert world.shree.plan_id == before
    assert account.pending_change == {"plan_id": str(pro.id)}
    assert len(sent(world, "billing_price_change")) == 2


# ------------------------------------------------ TV3-03: an announcement that failed is retried


async def test_a_raise_whose_mail_failed_is_announced_by_the_hourly_pass(world: World) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise ConnectionError("queue down")

    with (
        app_for(world) as app,
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", broken),
    ):
        res = await put_pricing(app, world, subscription_fee_minor=129_900, apply="scheduled")
    assert res.status_code == 200 and world.db.all(BillingNotice) == []

    queue: list[tuple[str, str, dict[str, Any]]] = []

    def enqueue(template: str, to: str, context: dict[str, Any], **_: Any) -> None:
        queue.append((template, to, context))

    told = await announce_scheduled_changes(
        world.db.factory(), enqueue=enqueue, product_name="XYZ AI", dashboard_url="http://x"
    )
    assert told == 2
    assert sorted(to for _, to, _ in queue) == [
        "asha@shree.example",
        "dev@pune.example",
        "meera@shree.example",
    ]
    assert {t for t, _, _ in queue} == {"billing_price_change"}
    assert len(world.db.all(BillingNotice)) == 2
    again = await announce_scheduled_changes(
        world.db.factory(), enqueue=enqueue, product_name="XYZ AI", dashboard_url="http://x"
    )
    assert again == 0 and len(queue) == 3  # told once each


async def test_a_workspace_that_joins_the_plan_later_is_told_by_the_pass(world: World) -> None:
    with app_for(world) as app:
        await put_pricing(app, world, subscription_fee_minor=129_900, apply="scheduled")
    late = world.tenant("Late Cafe")
    world.member(world.user("zoya@late.example"), late, "owner")
    queue: list[tuple[str, str]] = []
    told = await announce_scheduled_changes(
        world.db.factory(),
        enqueue=lambda template, to, ctx, **_: queue.append((template, to)),
        product_name="XYZ AI",
        dashboard_url="http://x",
    )
    assert queue == [("billing_price_change", "zoya@late.example")] and told == 1


async def test_the_hourly_pass_announces_before_it_activates(world: World) -> None:
    with app_for(world) as app:
        await put_pricing(app, world, subscription_fee_minor=129_900, apply="scheduled")
    for notice_row in world.db.all(BillingNotice):
        world.db.rows[BillingNotice].remove(notice_row)
    queue: list[str] = []
    from callobuzz_rag.accounts.settings_store import EngineSettings

    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()):
        result = await hourly_pass(
            world.db.factory(),
            datetime.now(UTC) + timedelta(days=45),
            enqueue=lambda template, to, ctx, **_: queue.append(to),
            engine_settings=EngineSettings(),
            dashboard_url="http://x",
        )
    assert result["announced"] == 2 and result["activated"] == 1 and len(queue) == 3


# ------------------------------------------------ TV3-04: the date of a change of period length


async def test_the_notice_dates_a_change_of_period_on_the_current_schedule(world: World) -> None:
    plan = world.db.default_plan
    v1 = world.db.all(PlanPricing)[0]
    v2 = stored_pricing(
        plan,
        version=2,
        subscription_fee_minor=99_900,
        usage_mode="per_question",
        per_question_minor=200,
        billing_period="yearly",
    )
    v2.is_current = False
    v2.effective_at = ist_midnight(date(2026, 11, 5))
    world.db.add(v2)
    world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=3,
            billing_day=12,
            next_charge_on=date(2026, 10, 12),
        )
    )
    queue: list[tuple[str, dict[str, Any]]] = []
    await announce_change(
        world.db.session(),
        plan=plan,
        old=v1,
        new=v2,
        kind="price_change",
        enqueue=lambda template, to, ctx, **_: queue.append((to, ctx)),
        product_name="XYZ AI",
        dashboard_url="http://x",
    )
    shree = [ctx for to, ctx in queue if to == "asha@shree.example"]
    assert shree[0]["effective_on"] == "12 November 2026"  # the renewal after 5 Nov, monthly


# ------------------------------------------------ TV3-05: held moves into a plan


async def test_a_held_move_into_a_plan_makes_its_raise_a_notice_matter(world: World) -> None:
    pro = dearer_plan(world)  # nobody is on Pro, one workspace is moving in
    billed_account(
        world,
        pending_change={"plan_id": str(pro.id)},
        pending_change_at=datetime.now(UTC) + timedelta(days=30),
    )
    body = {
        "usage_mode": "per_question",
        "per_question_minor": 200,
        "subscription_fee_minor": 249_900,
    }
    path = PRICING.format(pro.id)
    with app_for(world) as app:
        now_res = await req(app, "PUT", path, key=ADMIN, json=body)
        res = await req(app, "PUT", path, key=ADMIN, json={**body, "apply": "scheduled"})
    assert now_res.status_code == 422 and now_res.json()["code"] == "price_change_not_lower"
    assert res.status_code == 200, res.text
    assert sorted(to for to, _ in sent(world, "billing_price_change")) == [
        "asha@shree.example",
        "meera@shree.example",
    ]


def _scheduled_raise(world: World, plan: Plan, *, fee: int, in_days: int = 20) -> PlanPricing:
    v2 = stored_pricing(
        plan,
        version=2,
        subscription_fee_minor=fee,
        usage_mode="per_question",
        per_question_minor=200,
    )
    v2.is_current = False
    v2.effective_at = ist_midnight(date.today() + timedelta(days=in_days))
    return world.db.add(v2)


async def test_a_move_into_a_plan_with_a_scheduled_raise_is_held(world: World) -> None:
    """Pro costs less than the workspace pays today, but its raise (announced, starting in 20
    days) will cost more by the time the move could happen: the move waits and is announced."""
    account = billed_account(world)
    pro = dearer_plan(world, fee=49_900)
    _scheduled_raise(world, pro, fee=129_900)
    before = world.shree.plan_id
    with app_for(world) as app:
        res = await move(app, world, pro)
    assert res.status_code == 200, res.text
    assert world.shree.plan_id == before
    assert account.pending_change == {"plan_id": str(pro.id)}
    changes = sent(world, "billing_price_change")[0][1]["changes"]
    assert {"label": "Plan fee", "old": "₹999", "new": "₹1,299"} in changes


async def test_a_move_into_a_plan_whose_raise_is_still_cheaper_happens_now(world: World) -> None:
    billed_account(world)
    pro = dearer_plan(world, fee=49_900)
    _scheduled_raise(world, pro, fee=59_900)
    with app_for(world) as app:
        res = await move(app, world, pro)
    assert res.status_code == 200 and world.shree.plan_id == pro.id
    assert sent(world, "billing_price_change") == []


# ------------------------------------------------ TV3-06: a stale held raise


async def test_a_later_drop_cancels_the_held_raise_of_the_same_field(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        first = await patch_account(app, world, overrides={"per_question_minor": 300})
        assert first.status_code == 200 and account.pending_change is not None
        second = await patch_account(app, world, overrides={"per_question_minor": 150})
    assert second.status_code == 200, second.text
    assert account.overrides == {"per_question_minor": 150}
    assert account.pending_change is None and account.pending_change_at is None
    [cancelled] = world.audits("billing.pending_change_cancelled")
    assert cancelled.details["change"] == {"overrides": {"per_question_minor": 300}}
    due = datetime.now(UTC) + timedelta(days=90)
    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()):
        assert await apply_due_account_changes(world.db.factory(), due) == 0
    assert account.overrides == {"per_question_minor": 150}


async def test_a_drop_of_one_field_keeps_the_held_raise_of_another(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(
            app, world, overrides={"per_question_minor": 300, "subscription_fee_minor": 129_900}
        )
        await patch_account(app, world, overrides={"subscription_fee_minor": 50_000})
    assert account.overrides == {"subscription_fee_minor": 50_000}
    assert account.pending_change == {"overrides": {"per_question_minor": 300}}
    assert account.pending_change_at is not None


# ------------------------------------------------ TV3-08: the renewal amount sees a plan raise


async def test_the_pending_terms_include_a_plan_raise_that_starts_before_them(
    world: World,
) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
    assert account.pending_change_at is not None
    plan = world.db.default_plan
    v1 = world.db.all(PlanPricing)[0]
    v2 = stored_pricing(
        plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=200,
    )
    v2.is_current = False
    v2.effective_at = account.pending_change_at - timedelta(days=1)
    world.db.add(v2)
    assert v1.is_current
    with fake_accounts(world.db):
        ctx = await billing_accounts.load_context(world.db.session(), world.shree.id)
        new, target = (await pending_effect(world.db.session(), ctx)) or (None, None)
    assert target is None and new is not None
    assert (new.subscription_fee_minor, new.per_question_minor) == (129_900, 300)


# ------------------------------------------------ TV3-11: a held move that cannot happen


async def test_a_held_move_to_a_deactivated_plan_is_dropped_and_the_owners_told(
    world: World,
) -> None:
    account = billed_account(world)
    pro = dearer_plan(world)
    with app_for(world) as app:
        await move(app, world, pro)
    assert account.pending_change == {"plan_id": str(pro.id)} and account.pending_change_at
    pro.is_active = False
    world.mail.clear()
    queue: list[tuple[str, str, dict[str, Any]]] = []

    def enqueue(template: str, to: str, context: dict[str, Any], **_: Any) -> None:
        queue.append((template, to, context))

    due = account.pending_change_at + timedelta(hours=1)
    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()) as hook:
        for _ in range(2):
            applied = await apply_due_account_changes(
                world.db.factory(),
                due,
                enqueue=enqueue,
                product_name="XYZ AI",
                dashboard_url="http://x",
            )
            assert applied == 0
    hook.assert_not_awaited()
    assert world.shree.plan_id != pro.id
    assert account.pending_change is None and account.pending_change_at is None
    [audit] = world.audits("billing.pending_change_cancelled")
    assert audit.details["reason"] == "plan_inactive"
    assert sorted((t, to) for t, to, _ in queue) == [
        ("billing_price_change_cancelled", "asha@shree.example"),
        ("billing_price_change_cancelled", "meera@shree.example"),
    ]
    assert queue[0][2]["plan_name"] == "Pro"
