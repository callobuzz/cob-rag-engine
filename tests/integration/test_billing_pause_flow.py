"""The pause flow on real PostgreSQL (BIL4). Needs Docker.

A prepaid workspace with Rs 4 at Rs 2 a question: two answers are fine, the third is refused
both once posted and before posting (usage not yet posted counts), a top-up resumes it with an
audit pair and one paused notice, and two hooks at once send one notice (the account row lock).
"""

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing import hooks
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.enforcement import billing_state
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration


class Flow:
    def __init__(self, factory: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID, account_id: Any):
        self.factory = factory
        self.tenant_id = tenant_id
        self.corpus_id = corpus_id
        self.account_id = account_id
        self.mails: list[tuple[str, str]] = []
        self.webhooks: list[str] = []

    async def answer(self) -> None:
        async with self.factory() as s:
            await record_usage(
                s,
                corpus_id=self.corpus_id,
                tenant_id=self.tenant_id,
                source="api",
                prompt_tokens=1,
                completion_tokens=1,
                model="m",
                requests=1,
                request_id=uuid.uuid4(),
            )

    async def top_up(self, minor: int, key: str) -> None:
        async with self.factory() as s:
            await append_entries(
                s, self.account_id, [NewEntry(EntryType.TOP_UP, minor, key)], actor=SYSTEM_ACTOR
            )
            await s.execute(
                text("UPDATE billing_accounts SET last_top_up_minor = :m WHERE id = :a"),
                {"m": minor, "a": self.account_id},
            )
            await s.commit()

    async def paused(self) -> bool:
        async with self.factory() as s:
            return (await billing_state(s, self.tenant_id, payer="operator")).paused

    async def hook(self) -> None:
        async def dispatch(event: Any, data: dict[str, Any]) -> None:
            self.webhooks.append(event.value)

        def enqueue(template: str, to: str, context: Any, **_: Any) -> None:
            self.mails.append((template, to))

        await hooks.after_balance_change(
            self.factory, [self.account_id], dispatch=dispatch, enqueue=enqueue
        )

    async def audit(self) -> list[str]:
        async with self.factory() as s:
            rows = await s.execute(
                text(
                    "SELECT action FROM audit_log WHERE tenant_id = :t "
                    "AND action IN ('billing.paused', 'billing.resumed') ORDER BY created_at"
                ),
                {"t": self.tenant_id},
            )
            return [r[0] for r in rows]


@pytest_asyncio.fixture
async def flow(migrated_postgres, monkeypatch):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres), pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"pause-{plan_id.hex[:8]}"},
        )
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                currency="INR",
                usage_mode="per_question",
                per_question_minor=200,
            )
        )
        tenant = Tenant(name=f"pause-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await s.commit()
    out = Flow(factory, tenant.id, corpus.id, account.id)

    async def owners(session: Any, tenant_id: Any) -> list[str]:
        return ["owner@pause.example"]

    monkeypatch.setattr(hooks, "_owner_emails", owners)
    yield out
    await engine.dispose()


async def test_prepaid_runs_out_is_refused_before_and_after_posting_and_resumes(
    flow: Flow,
) -> None:
    await flow.top_up(400, "topup:first")  # Rs 4: two answers at Rs 2
    await flow.hook()
    assert not await flow.paused()
    await flow.answer()
    await flow.answer()
    assert await flow.paused()  # pending usage counts: refused before the minute posting
    await flow.hook()
    assert await flow.audit() == ["billing.paused"]
    await post_pending(flow.factory)
    assert await flow.paused()  # and still once posted
    await flow.hook()
    assert await flow.audit() == ["billing.paused"]  # no second row for the same pause
    assert [m for m in flow.mails if m[0] == "billing_paused"]

    await flow.top_up(1_000, "topup:second")
    await flow.hook()
    assert not await flow.paused()
    assert await flow.audit() == ["billing.paused", "billing.resumed"]
    assert flow.webhooks.count("billing.paused") >= 1


async def test_two_hooks_at_once_send_one_paused_notice(flow: Flow) -> None:
    await flow.top_up(400, "topup:first")
    await flow.answer()
    await flow.answer()
    await asyncio.gather(flow.hook(), flow.hook())
    assert flow.webhooks.count("billing.paused") == 1
    assert await flow.audit() == ["billing.paused"]
