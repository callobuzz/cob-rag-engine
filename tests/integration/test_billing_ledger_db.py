"""phase14_003 on PostgreSQL: the append-only ledger trigger, sign checks, the tenant-delete
cascade. Needs Docker."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.accounts.lifecycle import delete_tenant
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration

_TABLES = ("wallet_ledger", "usage_charges", "credit_lots", "billing_reconciliations")


def _alembic(url: str, target: str, *, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    (command.downgrade if down else command.upgrade)(cfg, target)


async def _present(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('wallet_ledger','usage_charges','credit_lots',"
                    "'billing_reconciliations')"
                )
            )
            names = {r.table_name for r in rows}
            fn = (
                await c.execute(
                    text("SELECT count(*) FROM pg_proc WHERE proname = 'wallet_ledger_append_only'")
                )
            ).scalar_one()
            return names | ({"fn"} if fn else set())
    finally:
        await engine.dispose()


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase14_002")
        assert asyncio.run(_present(url)) == set()
        _alembic(url, "phase14_003")
        assert asyncio.run(_present(url)) == {*_TABLES, "fn"}
        _alembic(url, "phase14_002", down=True)
        assert asyncio.run(_present(url)) == set()
        _alembic(url, "phase14_003")
        assert asyncio.run(_present(url)) == {*_TABLES, "fn"}


async def _account(factory) -> tuple[uuid.UUID, uuid.UUID]:  # type: ignore[no-untyped-def]
    async with factory() as s:
        plan_id = (await s.execute(text("SELECT id FROM plans WHERE is_default"))).scalar_one()
        tenant = Tenant(name=f"bil3-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        account = await ensure_account(s, tenant.id)
        await s.commit()
        return tenant.id, account.id


async def _entry(
    s,
    account_id: uuid.UUID,
    tenant_id: uuid.UUID,
    seq: int,
    kind: str,
    amount: int,  # type: ignore[no-untyped-def]
) -> uuid.UUID:
    return (
        await s.execute(
            text(
                "INSERT INTO wallet_ledger (account_id, tenant_id, seq, entry_type, amount_minor, "
                "currency, balance_after_minor, idempotency_key, period, actor_kind, actor_label) "
                "VALUES (:a, :t, :seq, :k, :amt, 'INR', :amt, :key, '2026-10-01', 'system', "
                "'system') RETURNING id"
            ),
            {
                "a": account_id,
                "t": tenant_id,
                "seq": seq,
                "k": kind,
                "amt": amount,
                "key": f"k-{uuid.uuid4().hex}",
            },
        )
    ).scalar_one()


async def test_ledger_rows_cannot_be_updated_or_deleted(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id, account_id = await _account(factory)
    async with factory() as s:
        entry = await _entry(s, account_id, tenant_id, 1, "top_up", 500)
        await s.commit()
    async with factory() as s:
        with pytest.raises(DBAPIError, match="append-only"):
            await s.execute(text("UPDATE wallet_ledger SET memo = 'x' WHERE id = :i"), {"i": entry})
    async with factory() as s:
        with pytest.raises(DBAPIError, match="deleted only with their workspace"):
            await s.execute(text("DELETE FROM wallet_ledger WHERE id = :i"), {"i": entry})


async def test_sign_and_amount_checks(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id, account_id = await _account(factory)
    for kind, amount in (("top_up", -1), ("usage_charge", 1), ("top_up", 0), ("adjustment", 0)):
        async with factory() as s:
            with pytest.raises(IntegrityError):
                await _entry(s, account_id, tenant_id, 1, kind, amount)
    async with factory() as s:  # either sign for an adjustment, a credit pack
        await _entry(s, account_id, tenant_id, 1, "adjustment", -5)
        await _entry(s, account_id, tenant_id, 2, "adjustment", 5)
        await _entry(s, account_id, tenant_id, 3, "add_on_purchase", 9)
        await s.commit()


async def test_deleting_a_workspace_cascades_through_the_append_only_ledger(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id, account_id = await _account(factory)
    async with factory() as s:
        entry = await _entry(s, account_id, tenant_id, 1, "top_up", 1000)
        await s.execute(
            text(
                "INSERT INTO credit_lots (account_id, entry_id, amount_minor, remaining_minor, "
                "expires_at) VALUES (:a, :e, 1000, 1000, now() + interval '30 days')"
            ),
            {"a": account_id, "e": entry},
        )
        await s.execute(
            text(
                "INSERT INTO usage_charges (request_id, account_id, tenant_id, corpus_id, source, "
                "payer, model, questions, cost_usd_micro, cost_known, usage_mode, fx_ppm, "
                "amount_micro, currency, day, posted_at, posted_entry_id) VALUES (:r, :a, :t, "
                ":c, 'api', 'operator', 'm', 1, 0, true, 'per_question', 1000000, 20000, 'INR', "
                "current_date, now(), :e)"
            ),
            {"r": uuid.uuid4(), "a": account_id, "t": tenant_id, "c": uuid.uuid4(), "e": entry},
        )
        await s.commit()
    async with factory() as s:
        tenant = await s.get(Tenant, tenant_id)
        assert tenant is not None
        await delete_tenant(s, tenant, confirm_name=tenant.name, request=None)
        await s.commit()
    async with factory() as s:
        for table in ("wallet_ledger", "credit_lots", "usage_charges"):
            count = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE account_id = :a"), {"a": account_id}
                )
            ).scalar_one()
            assert count == 0, table
