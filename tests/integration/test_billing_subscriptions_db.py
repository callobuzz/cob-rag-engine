"""Subscriptions, included credit and expiry on PostgreSQL, with a frozen clock (BIL3).
Needs Docker."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.billing.subscriptions import run_due
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.credit_lot import CreditLot
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration


def at(y: int, m: int, d: int, h: int = 10) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


class World:
    def __init__(self, factory: Any, plan_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        self.factory = factory
        self.plan_id = plan_id
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

    async def entries(self) -> list[WalletLedger]:
        async with self.factory() as s:
            return list(
                (
                    await s.execute(
                        select(WalletLedger)
                        .join(BillingAccount, BillingAccount.id == WalletLedger.account_id)
                        .where(BillingAccount.tenant_id == self.tenant_id)
                        .order_by(WalletLedger.seq)
                    )
                )
                .scalars()
                .all()
            )

    async def set_account(self, **values: Any) -> None:
        async with self.factory() as s:
            acc = await ensure_account(s, self.tenant_id)
            for k, v in values.items():
                setattr(acc, k, v)
            await s.commit()

    async def reprice(self, **fields: Any) -> None:
        async with self.factory() as s:
            await s.execute(
                text("UPDATE plan_pricing SET is_current = false WHERE plan_id = :p"),
                {"p": self.plan_id},
            )
            version = (
                await s.execute(
                    text(
                        "SELECT coalesce(max(version), 0) + 1 FROM plan_pricing WHERE plan_id = :p"
                    ),
                    {"p": self.plan_id},
                )
            ).scalar_one()
            s.add(
                PlanPricing(
                    plan_id=self.plan_id,
                    version=version,
                    is_current=True,
                    currency="INR",
                    **fields,
                )
            )
            await s.commit()


async def make_world(factory: Any, **pricing: Any) -> World:
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"sub-{plan_id.hex[:8]}"},
        )
        if pricing is not None:
            s.add(
                PlanPricing(plan_id=plan_id, version=1, is_current=True, currency="INR", **pricing)
            )
        tenant = Tenant(name=f"sub-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        await ensure_account(s, tenant.id)
        await s.commit()
        return World(factory, plan_id, tenant.id)


@pytest_asyncio.fixture
async def factory(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_monthly_subscription_is_charged_on_the_billing_day(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=49_900)
    await w.set_account(billing_day=5)
    await run_due(factory, now=at(2026, 10, 5))
    assert [(e.idempotency_key, e.amount_minor) for e in await w.entries()] == [
        ("sub:2026-10-05", -49_900)
    ]
    assert (await w.account()).next_charge_on == date(2026, 11, 5)
    await run_due(factory, now=at(2026, 10, 20))  # nothing due
    assert len(await w.entries()) == 1
    await run_due(factory, now=at(2026, 11, 7))
    assert [e.idempotency_key for e in await w.entries()] == ["sub:2026-10-05", "sub:2026-11-05"]
    await run_due(factory, now=at(2026, 11, 7, 11))  # a rerun adds nothing
    assert len(await w.entries()) == 2
    assert (await w.account()).balance_minor == -2 * 49_900  # prepaid is charged regardless (D10)


async def test_two_missed_months_are_charged_in_order(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=1_000)
    await w.set_account(
        billing_day=5, next_charge_on=date(2026, 10, 5), period_anchor=date(2026, 10, 5)
    )
    await run_due(factory, now=at(2026, 11, 6))
    keys = [e.idempotency_key for e in await w.entries()]
    assert keys == ["sub:2026-10-05", "sub:2026-11-05"]
    await run_due(factory, now=at(2026, 11, 6, 12))
    assert [e.idempotency_key for e in await w.entries()] == keys


async def test_a_trial_delays_the_first_charge(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=1_000, trial_days=14)
    await w.set_account(trial_ends_at=at(2026, 10, 19, 9))
    await run_due(factory, now=at(2026, 10, 5))
    assert await w.entries() == []
    assert (await w.account()).next_charge_on == date(2026, 10, 19)
    await run_due(factory, now=at(2026, 10, 19, 12))
    assert [e.idempotency_key for e in await w.entries()] == ["sub:2026-10-19"]


async def test_yearly_anchors_on_the_first_charges_month(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=5_000, billing_period="yearly")
    await w.set_account(billing_day=3)
    await run_due(factory, now=at(2026, 10, 5))
    acc = await w.account()
    assert acc.period_anchor == date(2026, 10, 5) and acc.next_charge_on == date(2027, 10, 3)


async def test_included_credit_is_spent_first_and_only_the_unused_part_expires(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(
        factory,
        subscription_fee_minor=0,
        included_credit_minor=20_000,
        usage_mode="per_question",
        per_question_minor=200,
    )
    await w.set_account(billing_day=5)
    await run_due(factory, now=at(2026, 10, 5))
    [credit] = await w.entries()
    assert credit.entry_type == "included_credit" and credit.amount_minor == 20_000
    async with factory() as s:
        lots = list((await s.execute(select(CreditLot))).scalars().all())
        lot = next(x for x in lots if x.entry_id == credit.id)
        assert lot.expires_at == at(2026, 11, 5, 0) and lot.remaining_minor == 20_000
        acc = await ensure_account(s, w.tenant_id)
        # the customer also paid ₹100 of their own, then used ₹80 of usage: the included credit goes first
        await append_entries(
            s, acc.id, [NewEntry(EntryType.TOP_UP, 10_000, "t1")], actor=SYSTEM_ACTOR
        )
        await append_entries(
            s, acc.id, [NewEntry(EntryType.USAGE_CHARGE, -8_000, "u1")], actor=SYSTEM_ACTOR
        )
        await s.commit()
    async with factory() as s:
        lot = (
            await s.execute(select(CreditLot).where(CreditLot.entry_id == credit.id))
        ).scalar_one()
        assert lot.remaining_minor == 12_000
    # the period ends: the unused 12,000 expires, the customer's own money stays
    await run_due(factory, now=at(2026, 11, 5, 1))
    keys = [(e.idempotency_key.split(":")[0], e.amount_minor) for e in await w.entries()]
    assert ("exp", -12_000) in keys
    acc = await w.account()
    # 20,000 credit + 10,000 top-up - 8,000 usage - 12,000 expired + the next period's 20,000
    assert acc.balance_minor == 30_000


async def test_expiry_never_removes_money_that_is_not_there(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, included_credit_minor=5_000)
    await run_due(factory, now=at(2026, 10, 5))
    async with factory() as s:
        acc = await ensure_account(s, w.tenant_id)
        # a correction takes the balance to ₹10 while the credit lot is untouched
        await append_entries(
            s, acc.id, [NewEntry(EntryType.ADJUSTMENT, -4_900, "adj1")], actor=SYSTEM_ACTOR
        )
        await s.commit()
    await run_due(factory, now=at(2026, 11, 6))
    expiry = [e for e in await w.entries() if e.entry_type == "expiry"]
    assert [e.amount_minor for e in expiry] == [-100]  # only what the balance still held


async def test_a_plan_that_drops_its_fee_stops_charging(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=1_000)
    await run_due(factory, now=at(2026, 10, 5))
    assert len(await w.entries()) == 1
    await w.reprice(subscription_fee_minor=0)
    await run_due(factory, now=at(2026, 12, 20))
    assert len(await w.entries()) == 1
    assert (await w.account()).next_charge_on is None
    await w.reprice(subscription_fee_minor=1_000)  # restored: a fresh period, no back-billing
    await run_due(factory, now=at(2027, 3, 2))
    assert [e.idempotency_key for e in await w.entries()][1:] == ["sub:2027-03-02"]


async def test_exempt_workspaces_are_never_charged(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=1_000)
    async with factory() as s:
        await s.execute(
            text("UPDATE tenants SET is_operator = true WHERE id = :t"), {"t": w.tenant_id}
        )
        await s.commit()
    await run_due(factory, now=at(2026, 10, 5))
    assert await w.entries() == []


async def test_usage_posting_and_the_schedule_share_one_ledger(factory) -> None:  # type: ignore[no-untyped-def]
    w = await make_world(factory, subscription_fee_minor=1_000)
    await run_due(factory, now=at(2026, 10, 5))
    await post_pending(factory, now=at(2026, 10, 5, 12) + timedelta(minutes=1))
    entries = await w.entries()
    assert [e.seq for e in entries] == list(range(1, len(entries) + 1))
    assert entries[-1].balance_after_minor == sum(e.amount_minor for e in entries)
