"""The daily reconciliation against real data (BIL3). A fresh database, so no other test's
leftovers: a clean book is ok, then each injected fault is found and named. Needs Docker."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing import reconcile
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.billing_reconciliation import BillingReconciliation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url
from tests.integration.test_billing_accounts_db import _alembic

pytestmark = pytest.mark.integration

TODAY = datetime.now(UTC).date()


class Customer:
    def __init__(
        self, factory: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID, account_id: uuid.UUID
    ):
        self.factory, self.tenant_id, self.corpus_id, self.account_id = (
            factory,
            tenant_id,
            corpus_id,
            account_id,
        )

    async def sql(self, statement: str, **params: Any) -> None:
        async with self.factory() as s:
            await s.execute(text(statement), {"a": self.account_id, "t": self.tenant_id, **params})
            await s.commit()

    async def answer(self, n: int = 1) -> None:
        for _ in range(n):
            async with self.factory() as s:
                await record_usage(
                    s,
                    corpus_id=self.corpus_id,
                    tenant_id=self.tenant_id,
                    source="api",
                    prompt_tokens=1,
                    completion_tokens=1,
                    model="m",
                    request_id=uuid.uuid4(),
                )


async def new_customer(factory: Any, name: str) -> Customer:
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"{name}-{plan_id.hex[:6]}"},
        )
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                currency="INR",
                usage_mode="per_question",
                per_question_minor=200,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            )
        )
        tenant = Tenant(name=f"{name}-{uuid.uuid4().hex[:6]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await append_entries(
            s,
            account.id,
            [NewEntry(EntryType.TOP_UP, 10_000, f"seed-{uuid.uuid4().hex[:8]}")],
            actor=SYSTEM_ACTOR,
        )
        await s.commit()
        return Customer(factory, tenant.id, corpus.id, account.id)


def find(
    result: reconcile.RunResult,
    check: str,
    account_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> list[reconcile.Finding]:
    return [
        f
        for f in result.findings
        if f.check == check
        and (account_id is None or f.account_id == account_id)
        and (tenant_id is None or f.tenant_id == tenant_id)
    ]


async def scenario(url: str) -> None:
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # ---- a clean book
        clean = await new_customer(factory, "clean")
        await clean.answer(3)
        await post_pending(factory)
        result = await reconcile.run(factory, day=TODAY)
        assert result is not None and result.ok and result.findings == []
        assert result.checked_accounts == 1
        async with factory() as s:
            stored = (await s.execute(select(BillingReconciliation))).scalars().all()
            assert len(stored) == 1 and stored[0].ok and stored[0].findings == []
            audits = (
                await s.execute(
                    text(
                        "SELECT count(*) FROM audit_log WHERE action = 'billing.reconciliation_failed'"
                    )
                )
            ).scalar_one()
            assert audits == 0

        # ---- R1: the cached balance no longer equals the ledger
        r1 = await new_customer(factory, "r1")
        await r1.sql("UPDATE billing_accounts SET balance_minor = balance_minor + 5 WHERE id = :a")
        # ---- R1: a ledger row whose running balance is wrong (names the first bad seq)
        r1b = await new_customer(factory, "r1b")
        await r1b.sql(
            "INSERT INTO wallet_ledger (account_id, tenant_id, seq, entry_type, amount_minor, "
            "currency, balance_after_minor, idempotency_key, period, actor_kind, actor_label) "
            "VALUES (:a, :t, 2, 'top_up', 100, 'INR', 999999, 'bad-row-1', '2026-10-01', 'system', 'system')"
        )
        # ---- R2: a charge changed after it was posted
        r2 = await new_customer(factory, "r2")
        await r2.answer(2)
        await post_pending(factory)
        await r2.sql(
            "UPDATE usage_charges SET amount_micro = amount_micro + 7 WHERE account_id = :a"
        )
        # ---- R3: an answer that was metered but never charged
        r3 = await new_customer(factory, "r3")
        await r3.sql(
            "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, payer, model, requests, "
            "prompt_tokens, completion_tokens, reasoning_tokens, cost_micro, unpriced_requests) "
            "VALUES (current_date, :c, :t, 'api', 'operator', 'm', 4, 1, 1, 0, 0, 0)",
            c=r3.corpus_id,
        )
        # ---- R3 informational: a deleted knowledge base's charges outlive its usage rows
        gone = await new_customer(factory, "gone")
        await gone.answer(2)
        await post_pending(factory)
        await gone.sql("DELETE FROM corpora WHERE id = :c", c=gone.corpus_id)
        # ---- R3 informational: a price changed during the day
        changed = await new_customer(factory, "changed")
        await changed.sql(
            "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, payer, model, requests, "
            "prompt_tokens, completion_tokens, reasoning_tokens, cost_micro, unpriced_requests) "
            "VALUES (current_date, :c, :t, 'api', 'operator', 'm', 1, 1, 1, 0, 0, 0)",
            c=changed.corpus_id,
        )
        await changed.sql(
            "UPDATE plan_pricing SET created_at = now() WHERE plan_id = (SELECT plan_id FROM tenants WHERE id = :t)"
        )
        # ---- R5 informational: credit that outlives the balance
        r5 = await new_customer(factory, "r5")
        async with factory() as s:
            await append_entries(
                s,
                r5.account_id,
                [
                    NewEntry(
                        EntryType.INCLUDED_CREDIT,
                        5_000,
                        "inc-1",
                        expires_at=datetime(2031, 1, 1, tzinfo=UTC),
                    )
                ],
                actor=SYSTEM_ACTOR,
            )
            await append_entries(
                s,
                r5.account_id,
                [NewEntry(EntryType.ADJUSTMENT, -14_000, "adj-1")],
                actor=SYSTEM_ACTOR,
            )
            await s.commit()

        # ---- R4: a charge nobody posted for half an hour
        r4 = await new_customer(factory, "r4")
        await r4.answer(1)
        await r4.sql(
            "UPDATE usage_charges SET created_at = now() - interval '30 minutes' WHERE account_id = :a"
        )
        result = await reconcile.run(factory, day=TODAY)
        assert result is not None and not result.ok
        assert [
            f.detail.get("first_bad_seq") for f in find(result, "ledger_integrity", r1b.account_id)
        ] == [2]
        assert len(find(result, "ledger_integrity", r1.account_id)) == 1
        assert len(find(result, "conservation", r2.account_id)) == 1
        assert len(find(result, "stale_unposted", r4.account_id)) == 1
        [coverage] = find(result, "coverage", tenant_id=r3.tenant_id)
        assert coverage.detail["usage_requests"] == 4 and coverage.detail["charge_questions"] == 0
        [deleted] = find(result, "knowledge_base_deleted", tenant_id=gone.tenant_id)
        assert deleted.informational
        [pricing] = find(result, "pricing_changed", tenant_id=changed.tenant_id)
        assert pricing.informational
        [lots] = find(result, "lot_exceeds_balance", r5.account_id)
        assert lots.informational
        # the clean customer stays clean among all of that
        assert not [
            f
            for f in result.findings
            if f.account_id == clean.account_id or f.tenant_id == clean.tenant_id
        ]
        async with factory() as s:
            audits = (
                (
                    await s.execute(
                        text(
                            "SELECT details FROM audit_log WHERE action = 'billing.reconciliation_failed'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(audits) == 1 and audits[0]["findings"] == len(
                [f for f in result.findings if not f.informational]
            )
            assert audits[0]["run_id"] == str(result.run_id)
    finally:
        await engine.dispose()


def test_the_reconciliation_finds_every_fault_and_only_those() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(scenario(url))
