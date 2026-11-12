"""Monthly statements on real data (BIL6): a wallet with entries and posted usage in two past
months and the current one. Each statement adds up, chains into the next, ends on the live
balance, the usage breakdown plus the carried rounding equals the posted usage to the micro, the
snapshot freezes the closed months once, and a deleted knowledge base keeps a name. Needs Docker."""

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing import statements
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url
from tests.integration.test_billing_accounts_db import _alembic

pytestmark = pytest.mark.integration

AUG, SEP = datetime(2026, 8, 20, 9, 0, tzinfo=UTC), datetime(2026, 9, 12, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


async def answers(factory: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID, n: int) -> None:
    for _ in range(n):
        async with factory() as s:
            await record_usage(
                s,
                corpus_id=corpus_id,
                tenant_id=tenant_id,
                source="api",
                prompt_tokens=1,
                completion_tokens=1,
                model="m",
                request_id=uuid.uuid4(),
            )


async def scenario(url: str) -> None:
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as s:
            plan_id = uuid.uuid4()
            await s.execute(
                text("INSERT INTO plans (id, name) VALUES (:i, :n)"), {"i": plan_id, "n": "P"}
            )
            s.add(
                PlanPricing(
                    plan_id=plan_id,
                    version=1,
                    is_current=True,
                    currency="INR",
                    usage_mode="per_question",
                    per_question_minor=1,  # a hundredth of a rupee, so rounding is carried
                    created_at=datetime(2020, 1, 1, tzinfo=UTC),
                )
            )
            tenant = Tenant(name="Shree", plan_id=plan_id)
            s.add(tenant)
            await s.flush()
            kb = Corpus(name="Clinic FAQ", tenant_id=tenant.id)
            gone = Corpus(name="Old FAQ", tenant_id=tenant.id)
            s.add_all([kb, gone])
            await s.flush()
            account = await ensure_account(s, tenant.id)
            await s.commit()
            tenant_id, account_id = tenant.id, account.id
            kb_id, gone_id = kb.id, gone.id

        async def entry(kind: EntryType, amount: int, key: str, at: datetime) -> None:
            async with factory() as s:
                await append_entries(
                    s, account_id, [NewEntry(kind, amount, key)], actor=SYSTEM_ACTOR, now=at
                )
                await s.commit()

        now = NOW  # pinned: the months below are fixed, so "now" must be October 2026
        await entry(EntryType.TOP_UP, 50_000, "t-aug", AUG)
        await entry(EntryType.SUBSCRIPTION_CHARGE, -9_900, "s-aug", AUG)
        await answers(factory, tenant_id, kb_id, 7)
        await answers(factory, tenant_id, gone_id, 3)
        await post_pending(factory, now=AUG)
        await entry(EntryType.TOP_UP, 20_000, "t-sep", SEP)
        await answers(factory, tenant_id, kb_id, 5)
        await post_pending(factory, now=SEP)
        await entry(EntryType.ADJUSTMENT, -300, "a-now", now)
        await answers(factory, tenant_id, kb_id, 2)
        await post_pending(factory, now=now)
        async with factory() as s:  # the knowledge base is deleted afterwards: charges outlive it
            await s.execute(text("DELETE FROM corpora WHERE id = :c"), {"c": gone_id})
            await s.commit()

        async with factory() as s:
            account = await s.get(BillingAccount, account_id)
            assert account is not None
            built = {}
            for month in await statements.available_months(s, account, now):
                built[month] = await statements.build_month(
                    s, account, month, now=now, live=month == statements.month_of(now)
                )
            assert list(built)[:3] == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
            previous = None
            for month, st in built.items():
                assert st.opening_minor + sum(x.amount_minor for x in st.lines) == st.closing_minor
                usage_micro = sum(r.amount_micro for r in st.usage_breakdown)
                assert (
                    usage_micro + st.rounding_carried_micro
                    == -st.totals["usage_charge"] * MICRO_PER_MINOR
                ), month
                if previous is not None:
                    assert st.opening_minor == previous.closing_minor
                previous = st
            assert built[date(2026, 8, 1)].opening_minor == 0
            aug = built[date(2026, 8, 1)]
            labels = {r.label: r for r in aug.usage_breakdown}
            assert labels["Clinic FAQ · m · API"].answers == 7
            assert labels["Deleted knowledge base · m · API"].answers == 3
            assert [sec.key for sec in aug.sections] == ["subscription", "usage", "top_ups"]
            # the live month ends on the real balance, and the carry on the account's residue
            live = built[statements.month_of(now)]
            assert live.live and live.closing_minor == account.balance_minor
            residue_end = await statements._residue_at(
                s, account, statements.next_month(statements.month_of(now))
            )
            assert residue_end == account.usage_residue_micro
            listed = await statements.summaries(s, account, now=now)
            assert [(m.month, m.closing_minor) for m in reversed(listed)] == [
                (m, st.closing_minor) for m, st in built.items()
            ]

        written = await statements.snapshot_closed_months(factory, now=now)
        assert written == 2  # August and September; the current month stays live
        assert await statements.snapshot_closed_months(factory, now=now) == 0
        async with factory() as s:
            account = await s.get(BillingAccount, account_id)
            assert account is not None
            frozen = await statements.statement_for(s, account, date(2026, 8, 1), now=now)
            assert frozen.live is False
            assert frozen.model_dump(mode="json") == (
                built[date(2026, 8, 1)].model_copy(update={"generated_at": frozen.generated_at})
            ).model_dump(mode="json")
            with pytest.raises(Exception) as err:
                await statements.statement_for(s, account, date(2026, 7, 1), now=now)
            assert getattr(err.value, "code", None) == "statement_not_found"
            with pytest.raises(Exception) as err2:
                await statements.statement_for(s, account, date(2027, 1, 1), now=now)
            assert getattr(err2.value, "code", None) == "statement_not_found"
        # a frozen statement does not change when the books move on
        await entry(EntryType.TOP_UP, 1_000, "t-late", now)
        async with factory() as s:
            account = await s.get(BillingAccount, account_id)
            assert account is not None
            again = await statements.statement_for(s, account, date(2026, 9, 1), now=now)
            assert again.closing_minor == built[date(2026, 9, 1)].closing_minor
    finally:
        await engine.dispose()


def test_statements_add_up_on_real_data() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(scenario(url))


def test_the_migration_downgrades() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        _alembic(url, "phase14_004", down=True)
        _alembic(url, "head")
