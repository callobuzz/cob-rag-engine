"""A raise for one workspace (its own terms, or a dearer plan) waits for the notice period and
applies at its next renewal; the deployer can apply it at once when the business asked, with the
reason on record (TV3)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.api.schemas.tenant import AdminTenantResponse
from callobuzz_rag.billing.notice import (
    applies_from,
    apply_due_account_changes,
    earliest_effective,
    ist_midnight,
)
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

ACCOUNT = "/api/v1/admin/billing/accounts/{}"
PLAN_MOVE = "/api/v1/admin/tenants/{}/plan"


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
    w.member(w.user("meera@shree.example"), w.shree, "owner")
    return w


def plain_tenant(world: World) -> Any:
    """The tenant read the route ends with, as a plain read (its SQL needs PostgreSQL)."""

    async def plain(session: Any, tenant_id: Any) -> AdminTenantResponse:
        t = next(t for t in world.db.all(Tenant) if t.id == tenant_id)
        plan = next(p for p in world.db.all(Plan) if p.id == t.plan_id)
        return AdminTenantResponse(
            id=t.id,
            name=t.name,
            description=t.description,
            corpus_count=0,
            max_corpora=t.max_corpora,
            effective_max_corpora=t.max_corpora,
            plan_id=t.plan_id,
            plan_name=plan.name,
            status=t.status,
            is_active=t.is_active,
            is_operator=t.is_operator,
            has_api_key=False,
            activated_at=t.activated_at,
            created_at=t.created_at,
            activation_note=t.activation_note,
            created_by_user_id=t.created_by_user_id,
            owner_emails=[],
            member_count=0,
            monthly_questions=None,
            questions_this_month=0,
        )

    return plain


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch("callobuzz_rag.api.routes.tenants._admin_tenant", plain_tenant(world)),
    ):
        yield app


def billed_account(world: World, **extra: Any) -> BillingAccount:
    return world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=5,
            billing_day=12,
            next_charge_on=date.today() + timedelta(days=9),
            **extra,
        )
    )


async def patch_account(app: Any, world: World, **body: Any) -> Any:
    return await req(app, "PATCH", ACCOUNT.format(world.shree.id), key=ADMIN, json=body)


def expected_start(account: BillingAccount) -> datetime:
    return ist_midnight(applies_from(account, earliest_effective(datetime.now(UTC), 30), "monthly"))


async def test_a_raise_waits_and_the_live_terms_do_not_change(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        res = await patch_account(app, world, overrides={"per_question_minor": 300})
    body = res.json()
    assert res.status_code == 200, res.text
    assert body["overrides"] == {} and body["effective"]["per_question_minor"] == 200
    assert account.pending_change == {"overrides": {"per_question_minor": 300}}
    assert account.pending_change_at == expected_start(account)
    assert body["pending_change"]["overrides"] == {"per_question_minor": 300}
    assert body["pending_change"]["changes"] == [
        {"label": "Price per question", "old": "₹2", "new": "₹3"}
    ]
    [audit] = world.audits("billing.pending_change_scheduled")
    assert audit.details["change"] == {"overrides": {"per_question_minor": 300}}
    assert world.audits("billing.account_updated") == []
    assert sorted(to for name, to, _ in world.mail if name == "billing_price_change") == [
        "asha@shree.example",
        "meera@shree.example",
    ]
    assert [n.kind for n in world.db.all(BillingNotice)] == ["pending_change"]


async def test_the_start_is_at_least_the_notice_period_away(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
    assert account.pending_change_at is not None
    assert account.pending_change_at >= datetime.now(UTC) + timedelta(days=30)


async def test_the_business_agreed_applies_now_and_records_who_asked(world: World) -> None:
    account = billed_account(world)
    reason = "Asked for priority support on call, 5 Oct"
    with app_for(world) as app:
        res = await patch_account(
            app,
            world,
            overrides={"per_question_minor": 300},
            business_agreed=True,
            reason=reason,
        )
    assert res.status_code == 200, res.text
    assert res.json()["overrides"] == {"per_question_minor": 300}
    assert account.pending_change is None
    [agreed] = world.audits("billing.change_agreed")
    assert agreed.details["reason"] == reason
    assert agreed.details["change"] == {"per_question_minor": 300}
    assert [n for n, _, _ in world.mail if n == "billing_price_change"] == []


async def test_agreeing_needs_a_real_reason(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        res = await patch_account(
            app,
            world,
            overrides={"per_question_minor": 300},
            business_agreed=True,
            reason="ok",
        )
    assert res.status_code == 422 and res.json()["code"] == "agreement_reason_required"
    assert account.pending_change is None and account.overrides in (None, {})


async def test_a_drop_applies_now(world: World) -> None:
    billed_account(world)
    with app_for(world) as app:
        res = await patch_account(app, world, overrides={"per_question_minor": 150})
    assert res.json()["overrides"] == {"per_question_minor": 150}
    assert res.json()["pending_change"] is None
    assert world.audits("billing.pending_change_scheduled") == []


async def test_a_workspace_that_was_never_activated_has_nothing_to_wait_for(world: World) -> None:
    world.shree.status = "pending"
    account = world.db.add(
        BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=0, billing_day=12)
    )
    with app_for(world) as app:
        res = await patch_account(app, world, overrides={"per_question_minor": 300})
    assert res.json()["overrides"] == {"per_question_minor": 300}
    assert account.pending_change is None


async def test_other_changes_in_the_same_request_still_apply(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        res = await patch_account(
            app, world, overrides={"per_question_minor": 300}, notes="asked on call"
        )
    assert res.status_code == 200
    assert account.notes == "asked on call" and account.pending_change is not None


async def test_a_second_raise_replaces_the_pending_one(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
        await patch_account(app, world, overrides={"per_question_minor": 400})
    assert account.pending_change == {"overrides": {"per_question_minor": 400}}
    assert len(world.audits("billing.pending_change_scheduled")) == 2


async def test_cancel_drops_it_and_says_when_there_is_none(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
        gone = await req(
            app, "DELETE", ACCOUNT.format(world.shree.id) + "/pending-change", key=ADMIN
        )
        again = await req(
            app, "DELETE", ACCOUNT.format(world.shree.id) + "/pending-change", key=ADMIN
        )
    assert gone.status_code == 204 and account.pending_change is None
    assert again.status_code == 404 and again.json()["code"] == "pending_change_not_found"
    assert len(world.audits("billing.pending_change_cancelled")) == 1


# ---------------------------------------------------------------- moving to another plan


def dearer_plan(world: World, fee: int = 199_900) -> Plan:
    plan = world.db.add(Plan(name="Pro", is_active=True))
    world.db.add(
        stored_pricing(
            plan, subscription_fee_minor=fee, usage_mode="per_question", per_question_minor=200
        )
    )
    return plan


async def move(app: Any, world: World, plan: Plan, **extra: Any) -> Any:
    return await req(
        app,
        "POST",
        PLAN_MOVE.format(world.shree.id),
        key=ADMIN,
        json={"plan_id": str(plan.id), **extra},
    )


async def test_a_move_to_a_dearer_plan_waits(world: World) -> None:
    account = billed_account(world)
    pro = dearer_plan(world)
    before = world.shree.plan_id
    with app_for(world) as app:
        res = await move(app, world, pro)
    assert res.status_code == 200, res.text
    assert world.shree.plan_id == before
    assert account.pending_change == {"plan_id": str(pro.id)}
    assert res.json()["plan_change_scheduled_for"] is not None
    assert world.audits("tenant.plan_changed") == []
    assert len(world.audits("billing.pending_change_scheduled")) == 1
    assert {to for name, to, _ in world.mail if name == "billing_price_change"} == {
        "asha@shree.example",
        "meera@shree.example",
    }


async def test_a_move_to_a_cheaper_plan_happens_now(world: World) -> None:
    billed_account(world)
    cheap = dearer_plan(world, fee=49_900)
    with app_for(world) as app:
        res = await move(app, world, cheap)
    assert res.status_code == 200 and world.shree.plan_id == cheap.id
    assert res.json()["plan_change_scheduled_for"] is None


async def test_a_dearer_plan_the_business_asked_for_applies_now(world: World) -> None:
    billed_account(world)
    pro = dearer_plan(world)
    with app_for(world) as app:
        res = await move(
            app, world, pro, business_agreed=True, reason="Owner asked by phone on 5 Oct"
        )
    assert res.status_code == 200 and world.shree.plan_id == pro.id
    assert len(world.audits("billing.change_agreed")) == 1
    assert len(world.audits("tenant.plan_changed")) == 1


# ---------------------------------------------------------------- the hourly beat


async def test_the_beat_applies_a_due_change_once(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
    assert account.pending_change_at is not None
    due = account.pending_change_at + timedelta(hours=1)
    with (
        patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()) as hook,
        fake_accounts(world.db),
    ):
        assert await apply_due_account_changes(world.db.factory(), due) == 1
        assert await apply_due_account_changes(world.db.factory(), due) == 0
    assert account.overrides == {"per_question_minor": 300}
    assert account.pending_change is None and account.pending_change_at is None
    [applied] = world.audits("billing.pending_change_applied")
    assert applied.actor_kind == "system"
    hook.assert_awaited_once()


async def test_the_beat_leaves_a_change_that_is_not_due_yet(world: World) -> None:
    account = billed_account(world)
    with app_for(world) as app:
        await patch_account(app, world, overrides={"per_question_minor": 300})
    assert account.pending_change_at is not None
    early = account.pending_change_at - timedelta(hours=1)
    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()):
        assert await apply_due_account_changes(world.db.factory(), early) == 0
    assert account.pending_change is not None


async def test_the_beat_moves_a_workspace_to_its_new_plan(world: World) -> None:
    account = billed_account(world)
    pro = dearer_plan(world)
    with app_for(world) as app:
        await move(app, world, pro)
    assert account.pending_change_at is not None
    due = account.pending_change_at + timedelta(minutes=1)
    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()):
        assert await apply_due_account_changes(world.db.factory(), due) == 1
    assert world.shree.plan_id == pro.id and account.pending_change is None
    assert len(world.audits("tenant.plan_changed")) == 1


async def test_a_later_move_now_cancels_the_held_move(world: World) -> None:
    """Hold a dearer move, then move to a cheaper plan at once: the renewal must not move the
    workspace to the dearer plan after all."""
    account = billed_account(world)
    pro = dearer_plan(world)
    cheap = dearer_plan(world, fee=49_900)
    cheap.name = "Lite"
    with app_for(world) as app:
        await move(app, world, pro)
        assert account.pending_change == {"plan_id": str(pro.id)}
        res = await move(app, world, cheap)
    assert res.status_code == 200 and world.shree.plan_id == cheap.id
    assert account.pending_change is None and account.pending_change_at is None
    assert len(world.audits("billing.pending_change_cancelled")) == 1
    due = datetime.now(UTC) + timedelta(days=60)
    with patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()):
        assert await apply_due_account_changes(world.db.factory(), due) == 0
    assert world.shree.plan_id == cheap.id
