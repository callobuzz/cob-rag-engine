"""The platform P&L on real data (BIL6): what counts as billed and as collected (a free credit
pack and included credit are neither, a pack paid outside counts at its price, a refund takes
cash back), the AI cost comes only from operator-paid usage in the range, the operator's own
workspace is left out and an Internal-plan customer is listed with billed 0. Needs Docker."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.billing import pnl
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url
from tests.integration.test_billing_accounts_db import _alembic

pytestmark = pytest.mark.integration

RATE = 83.5


async def scenario(url: str) -> None:
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with factory() as s:

            async def customer(name: str, *, internal: bool = False, operator: bool = False) -> Any:
                if internal:  # the one seeded Internal plan
                    plan_id = (
                        await s.execute(text("SELECT id FROM plans WHERE is_internal"))
                    ).scalar_one()
                else:
                    plan_id = uuid.uuid4()
                    await s.execute(
                        text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
                        {"i": plan_id, "n": f"{name}-{plan_id.hex[:6]}"},
                    )
                tenant = Tenant(name=name, plan_id=plan_id, is_operator=operator)
                s.add(tenant)
                await s.flush()
                kb = Corpus(name=f"{name} KB", tenant_id=tenant.id)
                s.add(kb)
                await s.flush()
                return tenant, kb

            shree, shree_kb = await customer("Shree")
            pilot, pilot_kb = await customer("Pilot", internal=True)
            ops, ops_kb = await customer("Operator", operator=True)
            account = await ensure_account(s, shree.id)
            ops_account = await ensure_account(s, ops.id)
            grant_paid, grant_free, pack = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            await s.execute(
                text(
                    "INSERT INTO add_on_catalogue (id, kind, name, currency, price_minor, "
                    "credit_minor, bonus_pct) VALUES (:i, 'credit_pack', 'Pack', 'INR', 50000, "
                    "55000, 10)"
                ),
                {"i": pack},
            )
            for gid, payment, price in ((grant_paid, "external", 50_000), (grant_free, "free", 0)):
                await s.execute(
                    text(
                        "INSERT INTO add_on_grants (id, tenant_id, add_on_id, kind, name, "
                        "currency, price_minor, credit_minor, bonus_pct, payment, granted_by) "
                        "VALUES (:g, :t, :a, 'credit_pack', 'Pack', 'INR', :p, 55000, 10, :pay, "
                        "'admin')"
                    ),
                    {"g": gid, "t": shree.id, "a": pack, "p": price, "pay": payment},
                )
            await s.commit()
            shree_id, shree_kb_id = shree.id, shree_kb.id
            pilot_id, pilot_kb_id = pilot.id, pilot_kb.id
            ops_id, ops_kb_id = ops.id, ops_kb.id
            account_id, ops_account_id = account.id, ops_account.id

        last_year = datetime(now.year - 1, 6, 15, tzinfo=UTC)
        now_entries = [
            NewEntry(EntryType.TOP_UP, 100_000, "t1"),
            NewEntry(EntryType.SUBSCRIPTION_CHARGE, -10_000, "s1"),
            NewEntry(EntryType.USAGE_CHARGE, -30_000, "u1"),
            NewEntry(EntryType.REFUND, -5_000, "r1"),
            NewEntry(EntryType.ADJUSTMENT, -300, "a1"),
            NewEntry(EntryType.INCLUDED_CREDIT, 10_000, "i1"),
            NewEntry(
                EntryType.ADD_ON_PURCHASE,
                55_000,
                f"addon:{grant_paid}",
                reference_type="add_on_grant",
                reference_id=str(grant_paid),
            ),
            NewEntry(
                EntryType.ADD_ON_PURCHASE,
                55_000,
                f"addon:{grant_free}",
                reference_type="add_on_grant",
                reference_id=str(grant_free),
            ),
        ]
        async with factory() as s:
            for acct, entries, at in (
                (account_id, now_entries, now),
                (account_id, [NewEntry(EntryType.TOP_UP, 777_000, "old")], last_year),
                (ops_account_id, [NewEntry(EntryType.SUBSCRIPTION_CHARGE, -9_999, "ops")], now),
            ):
                await append_entries(s, acct, entries, actor=SYSTEM_ACTOR, now=at)
            for tid, cid, payer, cost, unpriced in (
                (shree_id, shree_kb_id, "operator", 2_000_000, 1),
                (shree_id, shree_kb_id, "byok", 9_000_000, 0),  # their own key: not our cost
                (pilot_id, pilot_kb_id, "operator", 1_000_000, 0),
                (ops_id, ops_kb_id, "operator", 5_000_000, 0),
            ):
                await s.execute(
                    text(
                        "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, payer, "
                        "model, requests, prompt_tokens, completion_tokens, reasoning_tokens, "
                        "cost_micro, unpriced_requests) VALUES (current_date, :c, :t, 'api', :p, "
                        "'m', 1, 1, 1, 0, :cost, :u)"
                    ),
                    {"c": cid, "t": tid, "p": payer, "cost": cost, "u": unpriced},
                )
            await s.commit()

        month = now.date().replace(day=1)
        async with factory() as s:
            out = await pnl.build_pnl(s, month, month, currency="INR", rate=RATE)
        rows = {r.name: r for r in out.rows}
        assert set(rows) == {"Shree", "Pilot"}  # the operator's own workspace is left out
        shree_row = rows["Shree"]
        assert shree_row.billed_minor == 40_000  # subscription + usage, nothing else
        assert shree_row.collected_minor == 145_000  # 100,000 + 50,000 pack price - 5,000 refund
        assert shree_row.adjustments_minor == -300
        assert shree_row.ai_cost_minor == 16_700 and shree_row.ai_cost_known is False
        assert shree_row.unpriced_calls == 1 and shree_row.margin_minor == 40_000 - 16_700
        assert rows["Pilot"].billed_minor == 0 and rows["Pilot"].exempt
        assert rows["Pilot"].ai_cost_minor == 8_350
        assert out.totals.billed_minor == 40_000
        assert out.totals.margin_minor == 40_000 - 16_700 - 8_350

        # a wider range reaches the old top-up; the cost still comes only from this month
        first = datetime(now.year - 1, 6, 1).date()
        async with factory() as s:
            wide = await pnl.build_pnl(s, first, month, currency="USD", rate=RATE)
        assert len(wide.months) >= 13
        wide_rows = {r.name: r for r in wide.rows}
        assert wide_rows["Shree"].collected_minor == pnl.convert_minor(
            145_000 + 777_000, "INR", "USD", RATE
        )
        # a range with nothing in it is an empty table
        async with factory() as s:
            empty = await pnl.build_pnl(
                s,
                datetime(2019, 1, 1).date(),
                datetime(2019, 2, 1).date(),
                currency="INR",
                rate=RATE,
            )
        assert empty.rows == [] and empty.totals.margin_minor == 0
    finally:
        await engine.dispose()


def test_the_pnl_on_real_data() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(scenario(url))
