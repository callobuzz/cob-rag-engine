"""Self-service plan change on PostgreSQL (P6): an upgrade charges the prorated difference once,
waits for a payment when the balance cannot cover it, a downgrade waits for the renewal through
TV3's pending-change pass, and a call-off leaves the plan alone. Needs Docker."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing import plan_change
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.hooks import after_balance_change
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.notice import apply_due_account_changes, ist_midnight
from callobuzz_rag.billing.subscriptions import run_due
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 10, tzinfo=UTC)
RENEWAL = date(2026, 11, 1)  # the period is 1 Oct to 1 Nov: 31 days, 16 of them left


@pytest_asyncio.fixture
async def factory(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class World:
    def __init__(self, factory: Any, plans: dict[str, uuid.UUID], tenant_id: uuid.UUID) -> None:
        self.factory = factory
        self.plans = plans
        self.tenant_id = tenant_id

    async def account(self) -> BillingAccount:
        async with self.factory() as s:
            return (
                await s.execute(
                    select(BillingAccount)
                    .where(BillingAccount.tenant_id == self.tenant_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()

    async def plan_id(self) -> uuid.UUID:
        async with self.factory() as s:
            tenant = (
                await s.execute(
                    select(Tenant)
                    .where(Tenant.id == self.tenant_id)
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
            return tenant.plan_id

    async def entries(self) -> list[WalletLedger]:
        account = await self.account()
        async with self.factory() as s:
            rows = await s.execute(
                select(WalletLedger)
                .where(WalletLedger.account_id == account.id)
                .order_by(WalletLedger.seq)
            )
            return list(rows.scalars().all())

    async def top_up(self, amount: int, key: str) -> None:
        account = await self.account()
        async with self.factory() as s:
            await append_entries(
                s, account.id, [NewEntry(EntryType.TOP_UP, amount, key)], actor=SYSTEM_ACTOR
            )
            await s.commit()

    async def change(self, name: str, *, now: datetime = NOW) -> plan_change.ChangeOutcome:
        async with self.factory() as s:
            out = await plan_change.change_plan(
                s, self.tenant_id, self.plans[name], request=None, now=now
            )
            await s.commit()
            return out

    async def actions(self) -> list[str]:
        async with self.factory() as s:
            rows = await s.execute(
                select(AuditLog.action)
                .where(AuditLog.tenant_id == self.tenant_id)
                .order_by(AuditLog.created_at)
            )
            return list(rows.scalars().all())


PRICES = {
    "Free": {"subscription_fee_minor": 0, "included_credit_minor": 5_000},
    "Standard": {"subscription_fee_minor": 99_900, "included_credit_minor": 50_000},
    "Pro": {"subscription_fee_minor": 499_900, "included_credit_minor": 300_000},
    "Hidden": {"subscription_fee_minor": 1_000, "included_credit_minor": 0},
}


async def make_world(factory: Any, *, on: str = "Free", schedule: bool = True) -> World:
    suffix = uuid.uuid4().hex[:8]
    plans: dict[str, uuid.UUID] = {}
    async with factory() as s:
        for name, price in PRICES.items():
            plan_id = uuid.uuid4()
            await s.execute(
                text("INSERT INTO plans (id, name, self_service) VALUES (:i, :n, :ss)"),
                {"i": plan_id, "n": f"{name} {suffix}", "ss": name != "Hidden"},
            )
            s.add(
                PlanPricing(
                    plan_id=plan_id,
                    version=1,
                    is_current=True,
                    currency="INR",
                    usage_mode="per_question",
                    per_question_minor=50,
                    **price,
                )
            )
            plans[name] = plan_id
        tenant = Tenant(name=f"shop-{suffix}", plan_id=plans[on], status="active")
        s.add(tenant)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        if schedule:
            account.next_charge_on = RENEWAL
            account.period_anchor = date(2026, 10, 1)
            account.billing_day = 1
        await s.commit()
        return World(factory, plans, tenant.id)


def share(diff: int) -> int:
    return diff * 16 // 31


# ---------------------------------------------------------------- upgrade


async def test_an_upgrade_charges_the_rest_of_the_period_once(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory)
    await w.top_up(200_000, "t1")
    out = await w.change("Standard")
    assert out.status == "applied"
    assert out.charged_minor == share(99_900) and out.credit_minor == share(45_000)
    assert await w.plan_id() == w.plans["Standard"]
    rows = [(e.entry_type, e.amount_minor, e.idempotency_key) for e in await w.entries()]
    key = f"{w.tenant_id}:{w.plans['Standard']}:2026-10-01"
    assert rows[1:] == [
        ("subscription_charge", -share(99_900), f"plan-change:{key}"),
        ("included_credit", share(45_000), f"plan-change-credit:{key}"),
    ]
    assert "billing.plan_change_applied" in await w.actions()
    # a retry of the same request changes and charges nothing
    again = await w.change("Standard")
    assert again.status == "current"
    assert len(await w.entries()) == 3


async def test_an_upgrade_the_balance_cannot_cover_waits_for_the_payment(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory)
    out = await w.change("Pro")
    assert out.status == "awaiting_payment"
    assert out.amount_due_minor == share(499_900)
    assert out.shortfall_minor == share(499_900) + 1  # a prepaid balance must stay above zero
    assert await w.plan_id() == w.plans["Free"] and await w.entries() == []
    account = await w.account()
    assert account.pending_change is not None and account.pending_change["awaiting_payment"]
    assert account.pending_change_at is None  # never applied by the renewal pass
    # the money arrives: the balance hook applies it
    await w.top_up(300_000, "pay-1")
    await after_balance_change(factory, [account.id], now=NOW)
    assert await w.plan_id() == w.plans["Pro"]
    assert (await w.account()).pending_change is None
    charge = [e for e in await w.entries() if e.entry_type == "subscription_charge"]
    assert [c.amount_minor for c in charge] == [-share(499_900)]


async def test_a_waiting_upgrade_lapses_after_a_week(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory)
    await w.change("Pro")
    account = await w.account()
    later = datetime(2026, 10, 24, 10, tzinfo=UTC)
    await plan_change.settle_awaiting(factory, [account.id], now=later)
    assert (await w.account()).pending_change is None
    assert await w.plan_id() == w.plans["Free"]
    assert "billing.pending_change_cancelled" in await w.actions()


async def test_a_workspace_without_a_schedule_pays_the_first_period_under_the_schedule_keys(
    factory,  # type: ignore[no-untyped-def]
) -> None:
    w = await make_world(factory, schedule=False)
    await w.top_up(200_000, "t1")
    out = await w.change("Standard")
    assert out.status == "applied" and out.charged_minor == 99_900
    keys = [e.idempotency_key for e in await w.entries()]
    assert keys == ["t1", "sub:2026-10-16", "inc:2026-10-16"]
    assert (await w.account()).next_charge_on == date(2026, 11, 1)
    await run_due(factory, now=NOW)  # the hourly schedule charges nothing twice
    assert [e.idempotency_key for e in await w.entries()] == keys


# ---------------------------------------------------------------- downgrade and call-off


async def test_a_downgrade_waits_for_the_renewal_and_refunds_nothing(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, on="Standard")
    out = await w.change("Free")
    assert out.status == "scheduled" and out.effective_on == RENEWAL
    account = await w.account()
    assert account.pending_change == {"plan_id": str(w.plans["Free"]), "by": "owner"}
    assert account.pending_change_at == ist_midnight(RENEWAL)
    assert await w.plan_id() == w.plans["Standard"] and await w.entries() == []
    await apply_due_account_changes(factory, datetime(2026, 10, 31, 12, tzinfo=UTC))
    assert await w.plan_id() == w.plans["Standard"]  # not yet
    await apply_due_account_changes(factory, datetime(2026, 10, 31, 19, tzinfo=UTC))
    assert await w.plan_id() == w.plans["Free"]
    assert await w.entries() == []  # no refund


async def test_a_scheduled_move_can_be_called_off(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, on="Standard")
    await w.change("Free")
    async with factory() as s:
        await plan_change.cancel_change(s, w.tenant_id, request=None)
        await s.commit()
    account = await w.account()
    assert account.pending_change is None and account.pending_change_at is None
    with pytest.raises(APIError) as err:
        async with factory() as s:
            await plan_change.cancel_change(s, w.tenant_id, request=None)
    assert err.value.code == "pending_change_not_found"


async def test_a_change_the_provider_scheduled_blocks_the_owner(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, on="Standard")
    async with factory() as s:
        account = await ensure_account(s, w.tenant_id)
        account.pending_change = {"overrides": {"per_question_minor": 80}}
        account.pending_change_at = ist_midnight(RENEWAL)
        await s.commit()
    with pytest.raises(APIError) as err:
        await w.change("Pro")
    assert err.value.code == "plan_change_pending_from_provider"


async def test_only_self_service_plans_can_be_chosen(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory)
    with pytest.raises(APIError) as err:
        await w.change("Hidden")
    assert err.value.code == "plan_not_found"


# ---------------------------------------------------------------- sign-up


async def test_a_paid_plan_chosen_at_sign_up_waits_for_the_owner_to_pay(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, schedule=False)
    async with factory() as s:
        tenant = await s.get(Tenant, w.tenant_id)
        assert tenant is not None
        choice = await plan_change.apply_signup_choice(
            s, tenant, w.plans["Pro"], request=None, now=NOW
        )
        await s.commit()
    assert choice is not None and choice.status == "awaiting_payment"
    assert choice.amount_due_minor == 499_900
    assert await w.entries() == [] and await w.plan_id() == w.plans["Free"]


async def test_a_free_plan_chosen_at_sign_up_applies_at_once(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, on="Standard", schedule=False)
    async with factory() as s:
        tenant = await s.get(Tenant, w.tenant_id)
        assert tenant is not None
        choice = await plan_change.apply_signup_choice(
            s, tenant, w.plans["Free"], request=None, now=NOW
        )
        await s.commit()
    assert choice is not None and choice.status == "active"
    assert await w.plan_id() == w.plans["Free"]
    assert await w.entries() == []
