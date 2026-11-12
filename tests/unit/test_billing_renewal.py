"""The renewal reminder (TV3): one e-mail to the owners a few days before a renewal, with the exact
amount, once per renewal date."""

import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import EngineSettings, TrustSettings
from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing.notice import ist_midnight
from callobuzz_rag.billing.renewal import renewal_pass
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_notice import BillingNotice
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant  # noqa: TC001
from tests.unit.billing_fakes import stored_pricing
from tests.unit.test_auth_routes import World

# 09:00 IST on 5 October 2026
NOW = datetime(2026, 10, 5, 3, 30, tzinfo=UTC)
TODAY = date(2026, 10, 5)


@pytest.fixture(autouse=True)
def _billing_on() -> Any:
    os.environ["RAG_BILLING_ENABLED"] = "true"
    billing_config.reset_billing_config_cache()
    yield
    billing_config.reset_billing_config_cache()


class Mails:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.fail = False

    def __call__(self, name: str, to: str, context: dict[str, Any], **_: Any) -> None:
        if self.fail:
            raise ConnectionError("queue down")
        self.sent.append((name, to, context))


@pytest.fixture
def world() -> World:
    w = World()
    w.db.add(
        stored_pricing(
            w.db.default_plan,
            subscription_fee_minor=99_900,
            usage_mode="per_question",
            per_question_minor=200,
            included_credit_minor=50_000,
        )
    )
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("meera@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    return w


def renews_in(world: World, days: int, **extra: Any) -> BillingAccount:
    return world.db.add(
        BillingAccount(
            tenant_id=world.shree.id,
            currency="INR",
            ledger_seq=3,
            billing_day=12,
            next_charge_on=TODAY + timedelta(days=days),
            **extra,
        )
    )


def engine(days: int = 7) -> EngineSettings:
    settings = EngineSettings()
    settings.trust = TrustSettings(renewal_reminder_days=days)
    settings.branding.product_name = "XYZ AI"
    return settings


async def run(world: World, mails: Mails, *, days: int = 7) -> dict[str, int]:
    return await renewal_pass(
        world.db.factory(),
        NOW,
        enqueue=mails,
        engine_settings=engine(days),
        dashboard_url="https://app.xyz-ai.in",
    )


async def test_one_mail_per_owner_with_the_exact_amount(world: World) -> None:
    renews_in(world, 7)
    mails = Mails()
    result = await run(world, mails)
    assert result["sent"] == 1
    assert sorted(to for _, to, _ in mails.sent) == ["asha@shree.example", "meera@shree.example"]
    name, _, ctx = mails.sent[0]
    assert name == "billing_renewal_reminder"
    assert ctx["amount"] == "₹999" and ctx["renews_on"] == "12 October 2026"
    assert ctx["business_name"] == "Shree Labs" and ctx["product_name"] == "XYZ AI"
    assert ctx["plan_name"] == "Standard" and ctx["included"] == "₹500"
    assert ctx["new_price"] is False
    assert ctx["billing_url"] == "https://app.xyz-ai.in/plan"


async def test_a_second_run_sends_nothing_more(world: World) -> None:
    renews_in(world, 7)
    mails = Mails()
    await run(world, mails)
    again = await run(world, mails)
    assert again["sent"] == 0 and len(mails.sent) == 2
    [notice] = world.db.all(BillingNotice)
    assert (notice.kind, notice.key) == ("renewal", "2026-10-12")


async def test_six_days_ahead_is_only_sent_when_yesterday_was_missed(world: World) -> None:
    renews_in(world, 6)
    mails = Mails()
    # yesterday's run sent it
    world.db.add(BillingNotice(tenant_id=world.shree.id, kind="renewal", key="2026-10-11"))
    await run(world, mails)
    assert mails.sent == []
    # never sent: the day after the missed one still tells the owner
    world.db.rows[BillingNotice].clear()
    result = await run(world, mails)
    assert result["sent"] == 1 and len(mails.sent) == 2


async def test_five_days_ahead_and_zero_days_setting_send_nothing(world: World) -> None:
    account = renews_in(world, 5)
    mails = Mails()
    await run(world, mails)
    account.next_charge_on = TODAY + timedelta(days=7)
    await run(world, mails, days=0)
    assert mails.sent == []


async def test_a_workspace_that_pays_nothing_is_not_reminded(world: World) -> None:
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    renews_in(world, 7)
    pricing = world.db.all(PlanPricing)[0]
    pricing.subscription_fee_minor = 0
    pricing.included_credit_minor = 0
    mails = Mails()
    await run(world, mails)
    assert mails.sent == []


@pytest.mark.parametrize("kind", ["suspended", "operator", "system", "internal"])
async def test_workspaces_that_are_not_billed_are_skipped(world: World, kind: str) -> None:
    renews_in(world, 7)
    tenant: Tenant = world.shree
    if kind == "suspended":
        tenant.status = "suspended"
    elif kind == "operator":
        tenant.is_operator = True
    elif kind == "system":
        tenant.is_system = True
    else:
        plan = next(p for p in world.db.all(Plan) if p.id == tenant.plan_id)
        plan.is_internal = True
    mails = Mails()
    await run(world, mails)
    assert mails.sent == []


async def test_a_scheduled_raise_before_the_renewal_shows_the_new_amount(world: World) -> None:
    renews_in(world, 7)
    coming = stored_pricing(
        world.db.default_plan,
        version=2,
        subscription_fee_minor=129_900,
        usage_mode="per_question",
        per_question_minor=200,
        included_credit_minor=50_000,
    )
    coming.is_current = False
    coming.effective_at = ist_midnight(TODAY + timedelta(days=3))
    world.db.add(coming)
    mails = Mails()
    await run(world, mails)
    ctx = mails.sent[0][2]
    assert ctx["amount"] == "₹1,299" and ctx["new_price"] is True


async def test_a_failing_queue_leaves_no_notice_so_the_next_run_tries_again(
    world: World,
) -> None:
    renews_in(world, 7)
    mails = Mails()
    mails.fail = True
    result = await run(world, mails)
    assert result["sent"] == 0 and result["failed"] == 1
    assert world.db.all(BillingNotice) == []
    mails.fail = False
    assert (await run(world, mails))["sent"] == 1


async def test_a_pending_change_due_before_the_renewal_sets_the_amount(world: World) -> None:
    """The hourly job applies the pending change before the renewal is charged, so the reminder
    states what will really be charged and says the new price applies."""
    renews_in(
        world,
        7,
        pending_change={"overrides": {"subscription_fee_minor": 149_900}},
        pending_change_at=ist_midnight(TODAY + timedelta(days=7)),
    )
    mails = Mails()
    await run(world, mails)
    ctx = mails.sent[0][2]
    assert ctx["amount"] == "₹1,499" and ctx["new_price"] is True


async def test_a_pending_change_after_the_renewal_does_not_touch_the_amount(world: World) -> None:
    renews_in(
        world,
        7,
        pending_change={"overrides": {"subscription_fee_minor": 149_900}},
        pending_change_at=ist_midnight(TODAY + timedelta(days=8)),
    )
    mails = Mails()
    await run(world, mails)
    assert mails.sent[0][2]["amount"] == "₹999" and mails.sent[0][2]["new_price"] is False
