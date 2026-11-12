"""Tampering with the books is caught (BIL7). Needs Docker.

A fresh database with four customers, each with answered and posted usage. The clean book
reconciles. Then, as the table owner, three hand edits a bug or a careless admin could make:
a ledger amount changed behind the append-only trigger, a usage charge deleted before it was
posted, and a posting mark cleared on a charge that was already written to the ledger. The next
reconciliation names exactly those three customers and the fourth stays clean.
"""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.billing import reconcile
from callobuzz_rag.billing.posting import post_pending
from tests.integration.conftest import _async_url
from tests.integration.test_billing_accounts_db import _alembic
from tests.integration.test_billing_reconcile_db import find, new_customer

pytestmark = pytest.mark.integration


def today() -> date:
    """Read when asked, never at import: a run that crosses midnight UTC must look at the day
    its usage was really metered on."""
    return datetime.now(UTC).date()


async def scenario(url: str) -> None:
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        edited = await new_customer(factory, "edited")
        deleted = await new_customer(factory, "deleted")
        unmarked = await new_customer(factory, "unmarked")
        clean = await new_customer(factory, "clean")
        for customer in (edited, unmarked, clean):
            await customer.answer(3)
        await post_pending(factory)  # edited, unmarked and clean are posted
        await deleted.answer(3)  # deleted stays unposted: its charges are still pending

        before = await reconcile.run(factory, day=today())
        assert before is not None and before.ok and before.findings == []

        # 1. one ledger amount changed behind the append-only trigger
        async with engine.begin() as conn:
            await conn.execute(
                text("ALTER TABLE wallet_ledger DISABLE TRIGGER wallet_ledger_append_only")
            )
            await conn.execute(
                text(
                    "UPDATE wallet_ledger SET amount_minor = amount_minor + 5 "
                    "WHERE account_id = :a AND seq = 1"
                ),
                {"a": edited.account_id},
            )
            await conn.execute(
                text("ALTER TABLE wallet_ledger ENABLE TRIGGER wallet_ledger_append_only")
            )
        # 2. one usage charge deleted
        await deleted.sql(
            "DELETE FROM usage_charges WHERE request_id = (SELECT request_id FROM usage_charges "
            "WHERE account_id = :a AND posted_at IS NULL LIMIT 1)"
        )
        # 3. one posting mark skipped: a charge that is in the ledger is "unposted" again
        await unmarked.sql(
            "UPDATE usage_charges SET posted_at = NULL, posted_entry_id = NULL "
            "WHERE request_id = (SELECT request_id FROM usage_charges WHERE account_id = :a LIMIT 1)"
        )

        after = await reconcile.run(factory, day=today())
        assert after is not None and not after.ok
        serious = [f for f in after.findings if not f.informational]
        named = {(f.check, f.tenant_id) for f in serious}
        assert named == {
            ("ledger_integrity", edited.tenant_id),
            ("coverage", deleted.tenant_id),
            ("conservation", unmarked.tenant_id),
        }, named
        assert len(find(after, "ledger_integrity", edited.account_id)) == 1
        assert len(find(after, "conservation", unmarked.account_id)) == 1
        assert not [
            f
            for f in after.findings
            if f.account_id == clean.account_id or f.tenant_id == clean.tenant_id
        ]
    finally:
        await engine.dispose()


def test_three_hand_edits_are_named_and_the_clean_customer_stays_clean() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        asyncio.run(scenario(url))
