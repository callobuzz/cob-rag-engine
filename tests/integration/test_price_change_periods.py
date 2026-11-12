"""A price raise reaches each business at its own next renewal, on PostgreSQL (TV3). Needs Docker.

Shree Dental renews on the 12th and Cafe Mocha on the 25th, both on "Growth" (Rs 999 a month,
Rs 2 a question). On 5 October the deployer announces Rs 1,299 and Rs 3 from 5 November. Nobody
pays the new price in the period it was announced in: Shree is charged Rs 999 on 12 October and
Rs 1,299 on 12 November, Cafe Mocha Rs 999 on 25 October and Rs 1,299 on 25 November; a question
asked on 13 November costs Rs 3 for Shree and still Rs 2 for Cafe. The hourly activation flips the
plan's current version once even when two workers run it at the same moment.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.notice import (
    activate_due,
    ist_midnight,
    save_with_notice,
)
from callobuzz_rag.billing.pricing import PricingFields, effective_pricing
from callobuzz_rag.billing.subscriptions import run_due
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.wallet_ledger import WalletLedger
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration

ANNOUNCED = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
STARTS = ist_midnight(date(2026, 11, 5))

GROWTH = PricingFields(
    currency="INR",
    subscription_fee_minor=99_900,
    usage_mode="per_question",
    per_question_minor=200,
    payment_type="postpaid",
    credit_limit_minor=10_000_000,
)
RAISED = GROWTH.model_copy(update={"subscription_fee_minor": 129_900, "per_question_minor": 300})


def at(y: int, m: int, d: int, h: int = 10) -> datetime:
    return datetime(y, m, d, h, tzinfo=UTC)


@pytest_asyncio.fixture
async def factory(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class Shop:
    def __init__(self, factory: Any, plan_id: uuid.UUID) -> None:
        self.factory = factory
        self.plan_id = plan_id
        self.tenants: dict[str, uuid.UUID] = {}

    async def add(self, name: str, renews: date, billing_day: int) -> None:
        async with self.factory() as s:
            tenant = Tenant(name=f"{name}-{uuid.uuid4().hex[:6]}", plan_id=self.plan_id)
            s.add(tenant)
            await s.flush()
            account = await ensure_account(s, tenant.id)
            account.billing_day = billing_day
            account.next_charge_on = renews
            account.period_anchor = renews
            await s.commit()
            self.tenants[name] = tenant.id

    async def charges(self, name: str) -> list[tuple[str, int]]:
        async with self.factory() as s:
            rows = (
                await s.execute(
                    select(WalletLedger)
                    .join(BillingAccount, BillingAccount.id == WalletLedger.account_id)
                    .where(
                        BillingAccount.tenant_id == self.tenants[name],
                        WalletLedger.entry_type == "subscription_charge",
                    )
                    .order_by(WalletLedger.seq)
                )
            ).scalars()
            return [(r.idempotency_key, r.amount_minor) for r in rows]

    async def per_question(self, name: str, now: datetime) -> int:
        async with self.factory() as s:
            eff = await effective_pricing(s, self.tenants[name], now=now)
            return eff.fields.per_question_minor

    async def versions(self) -> list[PlanPricing]:
        async with self.factory() as s:
            rows = await s.execute(
                select(PlanPricing)
                .where(PlanPricing.plan_id == self.plan_id)
                .order_by(PlanPricing.version)
                .execution_options(populate_existing=True)
            )
            return list(rows.scalars().all())


async def make_shop(factory: Any) -> Shop:
    async with factory() as s:
        plan = Plan(name=f"Growth-{uuid.uuid4().hex[:6]}", is_active=True)
        s.add(plan)
        await s.flush()
        s.add(PlanPricing(plan_id=plan.id, version=1, is_current=True, **GROWTH.model_dump()))
        await s.commit()
        shop = Shop(factory, plan.id)
    await shop.add("shree", date(2026, 10, 12), 12)
    await shop.add("cafe", date(2026, 10, 25), 25)
    return shop


async def schedule_raise(shop: Shop) -> None:
    async with shop.factory() as s:
        plan = await s.get(Plan, shop.plan_id)
        assert plan is not None
        saved = await save_with_notice(
            s,
            plan,
            RAISED,
            created_by="admin",
            apply="scheduled",
            effective_at=STARTS,
            notice_days=30,
            now=ANNOUNCED,
        )
        assert saved.scheduled and saved.workspaces == 2
        await s.commit()


async def test_a_raise_is_never_paid_inside_the_period_it_was_announced_in(factory: Any) -> None:
    shop = await make_shop(factory)
    await run_due(factory, now=at(2026, 10, 12))
    await run_due(factory, now=at(2026, 10, 25))
    await schedule_raise(shop)

    # announced but not started: both still pay version 1, and a version is not current yet
    assert [v.is_current for v in await shop.versions()] == [True, False]
    assert await shop.per_question("shree", at(2026, 10, 20)) == 200

    assert await activate_due(factory, STARTS.replace(hour=1)) == 1
    assert [v.is_current for v in await shop.versions()] == [False, True]

    # 6 Nov: both are still inside periods that began before the raise
    assert await shop.per_question("shree", at(2026, 11, 6)) == 200
    assert await shop.per_question("cafe", at(2026, 11, 6)) == 200

    await run_due(factory, now=at(2026, 11, 12))
    assert await shop.per_question("shree", at(2026, 11, 13)) == 300
    assert await shop.per_question("cafe", at(2026, 11, 13)) == 200

    await run_due(factory, now=at(2026, 11, 25))
    assert await shop.per_question("cafe", at(2026, 11, 26)) == 300

    assert await shop.charges("shree") == [
        ("sub:2026-10-12", -99_900),
        ("sub:2026-11-12", -129_900),
    ]
    assert await shop.charges("cafe") == [
        ("sub:2026-10-25", -99_900),
        ("sub:2026-11-25", -129_900),
    ]


async def test_a_catch_up_after_downtime_charges_each_period_at_its_own_price(
    factory: Any,
) -> None:
    shop = await make_shop(factory)
    await schedule_raise(shop)
    await activate_due(factory, STARTS.replace(hour=1))
    # the scheduler was down until 26 Nov: October's and November's charges come in order
    await run_due(factory, now=at(2026, 11, 26))
    assert await shop.charges("shree") == [
        ("sub:2026-10-12", -99_900),
        ("sub:2026-11-12", -129_900),
    ]
    assert await shop.charges("cafe") == [
        ("sub:2026-10-25", -99_900),
        ("sub:2026-11-25", -129_900),
    ]


async def test_two_workers_activating_at_once_flip_the_plan_once(factory: Any) -> None:
    shop = await make_shop(factory)
    await schedule_raise(shop)
    moment = STARTS.replace(hour=1)
    flipped = await asyncio.gather(
        activate_due(factory, moment), activate_due(factory, moment), activate_due(factory, moment)
    )
    assert sum(flipped) == 1
    assert [v.is_current for v in await shop.versions()] == [False, True]
    async with factory() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM plan_pricing WHERE plan_id = :p AND is_current"),
                {"p": shop.plan_id},
            )
        ).scalar_one()
    assert count == 1


async def test_a_raise_applied_now_is_refused_and_a_drop_is_not(factory: Any) -> None:
    shop = await make_shop(factory)
    async with factory() as s:
        plan = await s.get(Plan, shop.plan_id)
        assert plan is not None
        with pytest.raises(APIError) as err:
            await save_with_notice(
                s,
                plan,
                RAISED,
                created_by="admin",
                apply="now",
                effective_at=None,
                notice_days=30,
                now=ANNOUNCED,
            )
        assert err.value.code == "price_change_not_lower"
        await s.rollback()
        await s.refresh(plan)  # the rollback expired it; an async session cannot lazy-load
        saved = await save_with_notice(
            s,
            plan,
            GROWTH.model_copy(update={"subscription_fee_minor": 79_900}),
            created_by="admin",
            apply="now",
            effective_at=None,
            notice_days=30,
            now=ANNOUNCED,
        )
        assert not saved.scheduled and saved.row.is_current
        await s.commit()
