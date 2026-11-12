"""Charging and the ledger under concurrency, on real PostgreSQL (BIL3). Needs Docker.

One request id is charged once however many callers race for it; parallel top-ups, postings
and charges leave a ledger that adds up; two posting workers never post a charge twice.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import (
    SYSTEM_ACTOR,
    EntryType,
    IdempotencyConflict,
    NewEntry,
    append_entries,
)
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration

PER_QUESTION_MINOR = 200  # Rs 2.00


class Flow:
    def __init__(
        self, factory: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID, account_id: uuid.UUID
    ):
        self.factory = factory
        self.tenant_id = tenant_id
        self.corpus_id = corpus_id
        self.account_id = account_id
        self.subscription_fee = 0

    async def _scalar(self, sql: str, **params: Any) -> Any:
        async with self.factory() as s:
            return (
                await s.execute(text(sql), {"a": self.account_id, "t": self.tenant_id, **params})
            ).scalar_one()

    async def record(self, *, request_id: uuid.UUID, questions: int) -> None:
        async with self.factory() as s:
            await record_usage(
                s,
                corpus_id=self.corpus_id,
                tenant_id=self.tenant_id,
                source="api",
                prompt_tokens=1,
                completion_tokens=1,
                model="m",
                requests=questions,
                request_id=request_id,
            )

    async def top_up(self, minor: int, *, key: str) -> None:
        async with self.factory() as s:
            await append_entries(
                s, self.account_id, [NewEntry(EntryType.TOP_UP, minor, key)], actor=SYSTEM_ACTOR
            )
            await s.commit()

    async def post(self) -> None:
        await post_pending(self.factory, now=datetime.now(UTC))

    async def count(self, table: str, **where: Any) -> int:
        clause = " AND ".join(f"{k} = :{k}" for k in where) or "true"
        async with self.factory() as s:
            return (
                await s.execute(text(f"SELECT count(*) FROM {table} WHERE {clause}"), where)
            ).scalar_one()

    async def usage_requests_today(self) -> int:
        return await self._scalar(
            "SELECT coalesce(sum(requests), 0) FROM usage_daily WHERE tenant_id = :t"
        )

    async def ledger_usage_minor(self) -> int:
        return await self._scalar(
            "SELECT coalesce(sum(amount_minor), 0) FROM wallet_ledger "
            "WHERE account_id = :a AND entry_type = 'usage_charge'"
        )

    async def balance(self) -> int:
        return await self._scalar("SELECT balance_minor FROM billing_accounts WHERE id = :a")

    async def unposted(self) -> int:
        return await self._scalar(
            "SELECT count(*) FROM usage_charges WHERE account_id = :a AND posted_at IS NULL"
        )

    async def ledger_is_consistent(self) -> bool:
        bad = await self._scalar(
            "SELECT count(*) FROM (SELECT seq, balance_after_minor, "
            "sum(amount_minor) OVER (ORDER BY seq) AS run, row_number() OVER (ORDER BY seq) AS rn "
            "FROM wallet_ledger WHERE account_id = :a) x "
            "WHERE x.run <> x.balance_after_minor OR x.rn <> x.seq"
        )
        total, n = (
            (
                await self._scalar(
                    "SELECT (coalesce(sum(amount_minor), 0), count(*))::text FROM wallet_ledger "
                    "WHERE account_id = :a"
                )
            )
            .strip("()")
            .split(",")
        )
        row = await self._scalar(
            "SELECT balance_minor::text || ',' || ledger_seq FROM billing_accounts WHERE id = :a"
        )
        balance, seq = row.split(",")
        return bad == 0 and int(balance) == int(total) and int(seq) == int(n)

    async def conservation_holds(self) -> bool:
        charged = await self._scalar(
            "SELECT coalesce(sum(amount_micro), 0) FROM usage_charges WHERE account_id = :a"
        )
        pending = await self._scalar(
            "SELECT coalesce(sum(amount_micro), 0) FROM usage_charges "
            "WHERE account_id = :a AND posted_at IS NULL"
        )
        residue = await self._scalar(
            "SELECT usage_residue_micro FROM billing_accounts WHERE id = :a"
        )
        posted = -await self.ledger_usage_minor()
        return int(charged) == posted * 10_000 + int(residue) + int(pending)


@pytest_asyncio.fixture
async def flow(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres), pool_size=25, max_overflow=25)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"conc-{plan_id.hex[:8]}"},
        )
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                currency="INR",
                usage_mode="per_question",
                per_question_minor=PER_QUESTION_MINOR,
            )
        )
        tenant = Tenant(name=f"conc-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await s.commit()
        out = Flow(factory, tenant.id, corpus.id, account.id)
    yield out
    await engine.dispose()


async def test_same_request_id_charged_once_under_concurrency(flow: Flow) -> None:
    rid = uuid.uuid4()
    await asyncio.gather(*(flow.record(request_id=rid, questions=1) for _ in range(5)))
    assert await flow.count("usage_charges", request_id=rid) == 1
    assert await flow.usage_requests_today() == 1  # duplicates skip the rollup too
    await flow.post()
    assert await flow.ledger_usage_minor() == -PER_QUESTION_MINOR  # Rs 2.00 once


async def test_parallel_top_ups_and_postings_keep_the_ledger_consistent(flow: Flow) -> None:
    await asyncio.gather(
        *(flow.top_up(1_000, key=f"t{i}") for i in range(20)),
        *(flow.record(request_id=uuid.uuid4(), questions=1) for _ in range(50)),
        flow.post(),
        flow.post(),
    )
    await flow.post()
    assert await flow.balance() == 20 * 1_000 - 50 * PER_QUESTION_MINOR
    assert await flow.ledger_is_consistent()  # I1 by SQL window, gapless seq
    assert await flow.conservation_holds()  # I6


async def test_two_posting_workers_post_each_charge_once(flow: Flow) -> None:
    for _ in range(300):
        await flow.record(request_id=uuid.uuid4(), questions=1)
    await asyncio.gather(flow.post(), flow.post(), flow.post())
    assert await flow.unposted() == 0
    assert await flow.ledger_usage_minor() == -300 * PER_QUESTION_MINOR
    assert await flow.conservation_holds()


async def test_same_top_up_key_concurrently_is_one_entry_and_a_different_amount_conflicts(
    flow: Flow,
) -> None:
    await asyncio.gather(*(flow.top_up(500, key="topup:same") for _ in range(6)))
    assert await flow.count("wallet_ledger", account_id=flow.account_id) == 1
    assert await flow.balance() == 500
    with pytest.raises(IdempotencyConflict):
        await flow.top_up(501, key="topup:same")
    assert await flow.balance() == 500
    assert await flow.ledger_is_consistent()


async def test_a_batch_below_one_minor_unit_is_carried(flow: Flow) -> None:
    # charges of 3,334 micro each: the third batch crosses one whole minor unit
    async with flow.factory() as s:
        for _ in range(3):
            await s.execute(
                text(
                    "INSERT INTO usage_charges (request_id, account_id, tenant_id, corpus_id, "
                    "source, payer, model, questions, cost_usd_micro, cost_known, usage_mode, "
                    "fx_ppm, amount_micro, currency, day) VALUES (:r, :a, :t, :c, 'api', "
                    "'operator', 'm', 1, 0, true, 'per_question', 1000000, 3334, 'INR', "
                    "current_date)"
                ),
                {"r": uuid.uuid4(), "a": flow.account_id, "t": flow.tenant_id, "c": flow.corpus_id},
            )
            await s.commit()
            await post_pending(flow.factory)
    assert await flow.ledger_usage_minor() == -1
    assert await flow._scalar("SELECT usage_residue_micro FROM billing_accounts WHERE id = :a") == 2
    assert await flow.conservation_holds()
