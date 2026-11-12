"""The owner's summary and the header meter on real data (BIL6): this month's usage and charged
amount, the unpriced count, the statements list (also for an account with no entries), the
top-up request hour read from the audit trail, and the meter for prepaid and postpaid workspaces
without ever creating an account. Needs Docker."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing import summary
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import _async_url
from tests.integration.test_billing_accounts_db import _alembic

pytestmark = pytest.mark.integration


async def make_tenant(s: Any, name: str, **pricing: Any) -> tuple[Tenant, Corpus]:
    plan_id = uuid.uuid4()
    await s.execute(
        text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
        {"i": plan_id, "n": f"{name}-{plan_id.hex[:6]}"},
    )
    if pricing:
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
                **pricing,
            )
        )
    tenant = Tenant(name=name, plan_id=plan_id)
    s.add(tenant)
    await s.flush()
    kb = Corpus(name=f"{name} KB", tenant_id=tenant.id)
    s.add(kb)
    await s.flush()
    return tenant, kb


async def scenario(url: str) -> None:
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with factory() as s:
            prepaid, prepaid_kb = await make_tenant(
                s,
                "Prepaid",
                currency="INR",
                usage_mode="per_question",
                per_question_minor=200,
                payment_type="prepaid",
                alert_thresholds_pct=[20, 5, 0],
            )
            postpaid, _ = await make_tenant(
                s,
                "Postpaid",
                currency="USD",
                usage_mode="per_question",
                per_question_minor=5,
                payment_type="postpaid",
                credit_limit_minor=10_000,
            )
            free, _ = await make_tenant(s, "Free")  # a plan without pricing: nothing billed
            acct = await ensure_account(s, prepaid.id)
            post_acct = await ensure_account(s, postpaid.id)
            await append_entries(
                s,
                acct.id,
                [NewEntry(EntryType.TOP_UP, 100_000, "t1")],
                actor=SYSTEM_ACTOR,
            )
            await s.commit()
            ids = (prepaid.id, prepaid_kb.id, postpaid.id, free.id, acct.id, post_acct.id)
        prepaid_id, prepaid_kb_id, postpaid_id, free_id, acct_id, post_acct_id = ids

        for _ in range(3):  # three answers at Rs 2.00: 600 paise, one still unposted
            async with factory() as s:
                await record_usage(
                    s,
                    corpus_id=prepaid_kb_id,
                    tenant_id=prepaid_id,
                    source="api",
                    prompt_tokens=1,
                    completion_tokens=1,
                    model="m",
                    request_id=uuid.uuid4(),
                )
        await post_pending(factory)
        async with factory() as s:
            await record_usage(
                s,
                corpus_id=prepaid_kb_id,
                tenant_id=prepaid_id,
                source="api",
                prompt_tokens=1,
                completion_tokens=1,
                model="m",
                request_id=uuid.uuid4(),
            )

        async with factory() as s:
            account = await s.get(BillingAccount, acct_id)
            assert account is not None
            usage = await summary.usage_this_period(s, account, now)
            assert usage.questions == 4
            assert usage.charged_minor == 800  # four answers, posted or not
            assert usage.unpriced == 0
            refs = await summary.recent_statements(s, account, now)
            assert [r["live"] for r in refs] == [True]
            assert refs[0]["closing_minor"] == account.balance_minor
            # an account that never had a transaction lists no statements
            empty = await s.get(BillingAccount, post_acct_id)
            assert empty is not None
            assert await summary.recent_statements(s, empty, now) == []

        # the meter: balance counts usage the minute posting has not written yet
        async with factory() as s:
            meter = await summary.billing_meter(s, prepaid_id, is_member=False)
            assert meter is not None
            assert meter["payment_type"] == "prepaid" and meter["currency"] == "INR"
            assert meter["available_minor"] == 100_000 - 800 and meter["paused"] is False
            hidden = await summary.billing_meter(s, prepaid_id, is_member=True)
            assert hidden is not None and hidden["available_minor"] is None
            assert hidden["money_visible"] is False
            credit = await summary.billing_meter(s, postpaid_id, is_member=False)
            assert credit is not None
            assert credit["payment_type"] == "postpaid" and credit["credit_limit_minor"] == 10_000
            assert credit["available_minor"] == 0
            assert await summary.billing_meter(s, free_id, is_member=False) is None
            # reading the meter never creates an account for an unbilled workspace
            count = (
                await s.execute(
                    select(func.count())
                    .select_from(BillingAccount)
                    .where(BillingAccount.tenant_id == free_id)
                )
            ).scalar_one()
            assert count == 0

        # the top-up request hour is read from the audit trail
        async with factory() as s:
            assert await summary.last_top_up_request(s, prepaid_id, now) is None
            await record_audit(
                s,
                None,
                summary.TOP_UP_ACTION,
                tenant_id=prepaid_id,
                tenant_label="Prepaid",
                details={"amount_minor": 1, "currency": "INR"},
            )
            await s.commit()
        async with factory() as s:
            last = await summary.last_top_up_request(s, prepaid_id)
            assert last is not None
            assert await summary.last_top_up_request(s, postpaid_id) is None  # another workspace
            assert (
                await summary.last_top_up_request(
                    s, prepaid_id, datetime.now(UTC) + timedelta(hours=2)
                )
                is None
            )
            assert 1 <= summary.retry_after_seconds(last) <= 3600
    finally:
        await engine.dispose()


def test_the_owner_summary_and_the_meter_on_real_data() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(scenario(url))
