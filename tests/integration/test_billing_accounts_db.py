"""phase14_002 on PostgreSQL: pricing versions, lazy billing accounts, cascades. Needs Docker."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.pricing import PricingFields, effective_pricing
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _alembic(url: str, target: str, *, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    (command.downgrade if down else command.upgrade)(cfg, target)


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('plan_pricing', 'billing_accounts')"
                )
            )
            return {r.table_name for r in rows}
    finally:
        await engine.dispose()


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase14_001")
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase14_002")
        assert asyncio.run(_tables(url)) == {"plan_pricing", "billing_accounts"}
        _alembic(url, "phase14_001", down=True)
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase14_002")
        assert asyncio.run(_tables(url)) == {"plan_pricing", "billing_accounts"}


async def _default_plan_id(session) -> uuid.UUID:  # type: ignore[no-untyped-def]
    return (await session.execute(text("SELECT id FROM plans WHERE is_default"))).scalar_one()


async def _tenant(session, plan_id: uuid.UUID) -> uuid.UUID:  # type: ignore[no-untyped-def]
    tenant = Tenant(name=f"bil2-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
    session.add(tenant)
    await session.flush()
    return tenant.id


def _pricing(plan_id: uuid.UUID, version: int, *, current: bool) -> PlanPricing:
    return PlanPricing(
        plan_id=plan_id, version=version, is_current=current, currency="INR", markup_bp=0
    )


async def test_ensure_account_is_lazy_and_idempotent(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tenant_id = await _tenant(s, await _default_plan_id(s))
        await s.commit()
    async with factory() as s:
        first = await ensure_account(s, tenant_id)
        await s.commit()
        again = await ensure_account(s, tenant_id)
        assert again.id == first.id
        assert first.currency == "INR" and first.balance_minor == 0 and first.billing_day == 1


async def test_two_concurrent_ensure_account_make_one_row(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tenant_id = await _tenant(s, await _default_plan_id(s))
        await s.commit()

    async def run() -> uuid.UUID:
        async with factory() as s:
            account = await ensure_account(s, tenant_id)
            await s.commit()
            return account.id

    ids = await asyncio.gather(run(), run(), run())
    assert len(set(ids)) == 1
    async with factory() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM billing_accounts WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
        ).scalar_one()
    assert count == 1


async def test_two_current_pricing_rows_for_a_plan_are_refused(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"p-{plan_id.hex[:8]}"},
        )
        s.add(_pricing(plan_id, 1, current=True))
        await s.flush()
        s.add(_pricing(plan_id, 2, current=True))
        with pytest.raises(IntegrityError):
            await s.flush()
        await s.rollback()


async def test_effective_pricing_reads_the_current_version_and_account(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"p-{plan_id.hex[:8]}"},
        )
        s.add(_pricing(plan_id, 1, current=False))
        await s.flush()
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=2,
                is_current=True,
                currency="USD",
                usage_mode="per_question",
                per_question_minor=7,
            )
        )
        tenant_id = await _tenant(s, plan_id)
        await s.commit()
    async with factory() as s:
        eff = await effective_pricing(s, tenant_id)
        assert eff.currency == "USD" and eff.fields.per_question_minor == 7
        assert eff.pricing_id is not None and not eff.exempt and eff.overridden == frozenset()
        account = await ensure_account(s, tenant_id)
        account.overrides = {"per_question_minor": 3}
        await s.commit()
    async with factory() as s:
        eff = await effective_pricing(s, tenant_id)
        assert eff.fields.per_question_minor == 3 and eff.overridden == {"per_question_minor"}


async def test_plan_without_pricing_is_all_defaults(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tenant_id = await _tenant(s, await _default_plan_id(s))
        await s.commit()
    async with factory() as s:
        eff = await effective_pricing(s, tenant_id)
    assert eff.pricing_id is None and eff.fields == PricingFields(currency=eff.currency)
    assert not eff.chargeable("operator") and not eff.chargeable("byok")


async def test_tenant_delete_cascades_the_account(db_engine) -> None:  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tenant_id = await _tenant(s, await _default_plan_id(s))
        await ensure_account(s, tenant_id)
        await s.commit()
    async with factory() as s:
        await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})
        await s.commit()
        left = (await s.execute(select_account(tenant_id))).scalar_one_or_none()
    assert left is None


def select_account(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return select(BillingAccount).where(BillingAccount.tenant_id == tenant_id)


async def test_save_pricing_versions_and_currency_guard_on_postgres(db_engine) -> None:  # type: ignore[no-untyped-def]
    from fastapi import HTTPException

    from callobuzz_rag.billing.pricing import (
        check_plan_currency,
        current_pricing,
        pricing_history,
        save_pricing,
    )
    from callobuzz_rag.db.models.plan import Plan

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    plan_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"p-{plan_id.hex[:8]}"},
        )
        tenant_id = await _tenant(s, plan_id)
        await s.commit()
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        row1, changed1 = await save_pricing(
            s, plan, PricingFields(per_question_minor=5, usage_mode="per_question"), created_by="t"
        )
        await s.commit()
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        row2, changed2 = await save_pricing(
            s, plan, PricingFields(per_question_minor=6, usage_mode="per_question"), created_by="t"
        )
        await s.commit()
    assert (row1.version, row2.version) == (1, 2)
    assert set(changed1) == {"per_question_minor", "usage_mode"} and changed2 == [
        "per_question_minor"
    ]
    async with factory() as s:
        assert (await current_pricing(s, plan_id)).version == 2  # type: ignore[union-attr]
        assert [r.version for r in await pricing_history(s, plan_id)] == [2, 1]
        account = await ensure_account(s, tenant_id)
        account.ledger_seq = 1  # a transaction locks the currency
        await s.commit()
    async with factory() as s:
        plan = await s.get(Plan, plan_id)
        with pytest.raises(HTTPException) as exc:
            await save_pricing(s, plan, PricingFields(currency="USD"), created_by="t")
        assert exc.value.status_code == 409 and exc.value.code == "currency_mismatch"  # type: ignore[attr-defined]
        await s.rollback()
        other = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": other, "n": f"p-{other.hex[:8]}"},
        )
        s.add(PlanPricing(plan_id=other, version=1, is_current=True, currency="USD"))
        await s.flush()
        with pytest.raises(HTTPException):
            await check_plan_currency(s, tenant_id, other)
        await s.rollback()


async def test_admin_account_services_on_postgres(db_engine) -> None:  # type: ignore[no-untyped-def]
    from fastapi import HTTPException

    from callobuzz_rag.billing import accounts

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    plan_id = uuid.uuid4()
    name = f"Zed-{uuid.uuid4().hex[:8]}"
    async with factory() as s:
        await s.execute(
            text("INSERT INTO plans (id, name) VALUES (:i, :n)"),
            {"i": plan_id, "n": f"p-{plan_id.hex[:8]}"},
        )
        s.add(PlanPricing(plan_id=plan_id, version=1, is_current=True, currency="INR"))
        tenant = Tenant(name=name, plan_id=plan_id)
        s.add(tenant)
        await s.commit()
        tenant_id = tenant.id

    async with factory() as s:
        rows, total = await accounts.list_rows(
            s, q=name[:6].lower(), payment_type=None, offset=0, limit=10
        )
        assert total == 1 and rows[0]["has_account"] is False and rows[0]["tenant_name"] == name
        ctx = await accounts.load_context(s, tenant_id)
        changed = await accounts.update_account(
            s,
            ctx,
            {"overrides": {"per_question_minor": 9, "payment_type": "postpaid"}, "notes": "n"},
            request=None,
        )
        await s.commit()
        assert set(changed) == {"overrides.per_question_minor", "overrides.payment_type", "notes"}
    async with factory() as s:
        ctx = await accounts.load_context(s, tenant_id)
        assert ctx.effective.fields.per_question_minor == 9
        assert ctx.effective.overridden == {"per_question_minor", "payment_type"}
        await accounts.update_account(s, ctx, {"overrides": {"payment_type": None}}, request=None)
        await s.commit()
        rows, _ = await accounts.list_rows(s, q=name, payment_type="prepaid", offset=0, limit=10)
        assert [r["overrides_count"] for r in rows] == [1] and rows[0]["has_account"] is True
        ctx = await accounts.load_context(s, tenant_id)
        ctx.account.ledger_seq = 2
        with pytest.raises(HTTPException) as exc:
            await accounts.update_account(s, ctx, {"currency": "USD"}, request=None)
        assert exc.value.code == "currency_locked"  # type: ignore[attr-defined]
        await s.rollback()
    async with factory() as s:
        audits = (
            await s.execute(
                text(
                    "SELECT details FROM audit_log WHERE action = 'billing.account_updated' "
                    "AND tenant_id = :t"
                ),
                {"t": tenant_id},
            )
        ).all()
        assert len(audits) == 2
