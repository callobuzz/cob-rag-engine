"""One request id arriving many times at once is charged once (BIL7). Needs Docker.

`record_usage` is called with the same `request_id` from five sessions at the same moment (a
client retry, a double delivery): exactly one `usage_charges` row, one metered question, and
after the minute posting one deduction from the balance. The unit property test cannot show
this: it needs the `ON CONFLICT (request_id) DO NOTHING` of `billing/charging.py` on a real
database.
"""

import asyncio
import uuid
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration

PRICE = 200
TOP_UP = 10_000
SESSIONS = 5


class Setup:
    def __init__(self, factory: Any, tenant_id: uuid.UUID, corpus_id: uuid.UUID, account_id: Any):
        self.factory = factory
        self.tenant_id = tenant_id
        self.corpus_id = corpus_id
        self.account_id = account_id

    async def scalar(self, sql: str, **params: Any) -> Any:
        async with self.factory() as session:
            return (await session.execute(text(sql), params)).scalar_one()

    async def answer(self, request_id: uuid.UUID) -> None:
        async with self.factory() as session:
            await record_usage(
                session,
                corpus_id=self.corpus_id,
                tenant_id=self.tenant_id,
                source="api",
                prompt_tokens=1,
                completion_tokens=1,
                model="m",
                request_id=request_id,
            )


@pytest_asyncio.fixture
async def setup(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres), pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"replay-{plan_id.hex[:8]}"},
        )
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                currency="INR",
                usage_mode="per_question",
                per_question_minor=PRICE,
            )
        )
        tenant = Tenant(name=f"replay-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="KB", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await append_entries(
            s, account.id, [NewEntry(EntryType.TOP_UP, TOP_UP, "topup:first")], actor=SYSTEM_ACTOR
        )
        await s.commit()
    yield Setup(factory, tenant.id, corpus.id, account.id)
    await engine.dispose()


async def test_the_same_request_id_from_five_sessions_is_charged_once(setup: Setup) -> None:
    request_id = uuid.uuid4()
    await asyncio.gather(*(setup.answer(request_id) for _ in range(SESSIONS)))

    assert (
        await setup.scalar("SELECT count(*) FROM usage_charges WHERE request_id = :r", r=request_id)
        == 1
    )
    # one metered request: the daily row and the month's count saw it once
    assert (
        await setup.scalar(
            "SELECT coalesce(sum(requests), 0) FROM usage_daily WHERE tenant_id = :t",
            t=setup.tenant_id,
        )
        == 1
    )
    assert (
        await setup.scalar(
            "SELECT coalesce(sum(questions), 0) FROM tenant_question_counts WHERE tenant_id = :t",
            t=setup.tenant_id,
        )
        == 1
    )

    await post_pending(setup.factory)
    assert (
        await setup.scalar(
            "SELECT balance_minor FROM billing_accounts WHERE id = :a", a=setup.account_id
        )
        == TOP_UP - PRICE
    )
    assert (
        await setup.scalar(
            "SELECT count(*) FROM wallet_ledger WHERE account_id = :a "
            "AND entry_type = 'usage_charge'",
            a=setup.account_id,
        )
        == 1
    )


async def test_distinct_requests_at_once_are_each_charged(setup: Setup) -> None:
    """The guard above is the request id, not 'one charge at a time'."""
    await asyncio.gather(*(setup.answer(uuid.uuid4()) for _ in range(SESSIONS)))
    await post_pending(setup.factory)
    assert (
        await setup.scalar(
            "SELECT balance_minor FROM billing_accounts WHERE id = :a", a=setup.account_id
        )
        == TOP_UP - SESSIONS * PRICE
    )
