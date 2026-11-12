"""phase19_004 on PostgreSQL: an existing plan reads byok_allowed = true, the new add-on kinds and
their checks, one service order per grant, round trip. Needs Docker."""

import asyncio
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration

_BASE = (
    "INSERT INTO add_on_catalogue (kind, name, currency, price_minor, {cols}) "
    "VALUES (:kind, :name, 'INR', :price, {vals}) RETURNING id"
)


def _alembic(url: str, target: str, *, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    (command.downgrade if down else command.upgrade)(cfg, target)


async def _existing_plan_keeps_byok(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            flags = (await c.execute(text("SELECT byok_allowed FROM plans"))).scalars().all()
            assert flags and all(flags)
            shop = (
                (await c.execute(text("SELECT sold_to_owners FROM add_on_catalogue")))
                .scalars()
                .all()
            )
            assert not any(shop)
    finally:
        await engine.dispose()


async def _add_on(c, kind: str, name: str, cols: str, vals: str, **params):  # type: ignore[no-untyped-def]
    return (
        await c.execute(
            text(_BASE.format(cols=cols, vals=vals)),
            {"kind": kind, "name": name, "price": 49900} | params,
        )
    ).scalar_one()


async def _exercise(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            byok = await _add_on(
                c,
                "feature_unlock",
                "Own AI key",
                "feature, duration, renews",
                "'byok', 'period', true",
            )
            await _add_on(
                c,
                "service",
                "Installation help",
                "sac_code, includes_support_months",
                "'998313', 3",
            )
        for bad in (
            ("feature_unlock", "no feature", "duration", "'period'"),
            ("feature_unlock", "no duration", "feature", "'byok'"),
            ("service", "with credit", "credit_minor", "5"),
            ("service", "bad sac", "sac_code", "'12345'"),
            ("credit_pack", "renewing pack", "credit_minor, renews", "100, true"),
            ("service", "support months 99", "includes_support_months", "99"),
        ):
            with pytest.raises(IntegrityError):
                async with engine.begin() as c:
                    await _add_on(c, bad[0], bad[1], bad[2], bad[3])
        async with engine.begin() as c:
            plan = (await c.execute(text("SELECT id FROM plans WHERE is_default"))).scalar_one()
            tenant = (
                await c.execute(
                    text(
                        "INSERT INTO tenants (name, plan_id, api_key_hash) "
                        "VALUES ('Shree Labs', :p, '') RETURNING id"
                    ),
                    {"p": plan},
                )
            ).scalar_one()
            grant = (
                await c.execute(
                    text(
                        "INSERT INTO add_on_grants (id, tenant_id, add_on_id, kind, name, currency, "
                        "price_minor, feature, renews, payment, granted_by) VALUES "
                        "(gen_random_uuid(), :t, :a, 'feature_unlock', 'Own AI key', 'INR', 49900, "
                        "'byok', true, 'free', 'sv') RETURNING id"
                    ),
                    {"t": tenant, "a": byok},
                )
            ).scalar_one()
            await c.execute(
                text(
                    "INSERT INTO service_orders (tenant_id, grant_id, name) "
                    "VALUES (:t, :g, 'Own AI key')"
                ),
                {"t": tenant, "g": grant},
            )
        with pytest.raises(IntegrityError):  # one order per grant
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO service_orders (tenant_id, grant_id, name) "
                        "VALUES (:t, :g, 'again')"
                    ),
                    {"t": tenant, "g": grant},
                )
        with pytest.raises(IntegrityError):  # an unknown status
            async with engine.begin() as c:
                await c.execute(text("UPDATE service_orders SET status = 'lost'"))
        with pytest.raises(IntegrityError):  # the grant under an order is never deleted
            async with engine.begin() as c:
                await c.execute(text("DELETE FROM add_on_grants WHERE id = :g"), {"g": grant})
        async with engine.begin() as c:  # the order goes with the workspace
            await c.execute(text("DELETE FROM service_orders"))
            await c.execute(text("DELETE FROM add_on_grants"))
            await c.execute(
                text("DELETE FROM add_on_catalogue WHERE kind IN ('feature_unlock','service')")
            )
    finally:
        await engine.dispose()


async def _has_table(url: str) -> bool:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            return bool(
                (
                    await c.execute(text("SELECT to_regclass('public.service_orders') IS NOT NULL"))
                ).scalar_one()
            )
    finally:
        await engine.dispose()


async def _seed_old(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO add_on_catalogue (kind, name, currency, price_minor, credit_minor) "
                    "VALUES ('credit_pack', 'Pack', 'INR', 50000, 50000)"
                )
            )
    finally:
        await engine.dispose()


def test_saas_plans_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_003")
        asyncio.run(_seed_old(url))
        _alembic(url, "phase19_004")
        assert asyncio.run(_has_table(url))
        asyncio.run(_existing_plan_keeps_byok(url))
        asyncio.run(_exercise(url))
        _alembic(url, "phase19_003", down=True)
        assert not asyncio.run(_has_table(url))
        _alembic(url, "phase19_004")
        assert asyncio.run(_has_table(url))


# ------------------------------------------------------------------------- renewals, for real

RENEW_NOW = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)


async def _renewals(url: str) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from callobuzz_rag.billing import accounts, addon_renewals, ledger

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, Any] = {}
    try:
        async with factory() as session:
            plan = (
                await session.execute(text("SELECT id FROM plans WHERE is_default"))
            ).scalar_one()
            tenant = (
                await session.execute(
                    text(
                        "INSERT INTO tenants (name, plan_id, api_key_hash) "
                        "VALUES ('Shree Labs', :p, '') RETURNING id"
                    ),
                    {"p": plan},
                )
            ).scalar_one()
            account = await accounts.ensure_account(session, tenant)
            await ledger.append_entries(
                session,
                account.id,
                [ledger.NewEntry(ledger.EntryType.TOP_UP, 160_000, "topup:seed-1")],
                actor=ledger.SYSTEM_ACTOR,
                now=RENEW_NOW,
            )
            add_on = (
                await session.execute(
                    text(
                        "INSERT INTO add_on_catalogue (kind, name, currency, price_minor, feature, "
                        "duration, renews) VALUES ('feature_unlock', 'Own AI key', 'INR', 49900, "
                        "'byok', 'period', true) RETURNING id"
                    )
                )
            ).scalar_one()
            grant = (
                await session.execute(
                    text(
                        "INSERT INTO add_on_grants (id, tenant_id, add_on_id, kind, name, currency, "
                        "price_minor, feature, renews, payment, granted_by, starts_at, ends_at) "
                        "VALUES (gen_random_uuid(), :t, :a, 'feature_unlock', 'Own AI key', 'INR', "
                        "49900, 'byok', true, 'free', 'sv', :s, :e) RETURNING id"
                    ),
                    {
                        "t": tenant,
                        "a": add_on,
                        "s": datetime(2026, 8, 1, tzinfo=UTC),
                        "e": datetime(2026, 9, 1, tzinfo=UTC),  # three periods missed
                    },
                )
            ).scalar_one()
            await session.commit()
        first = await addon_renewals.renew_due(factory, now=RENEW_NOW)
        again = await addon_renewals.renew_due(factory, now=RENEW_NOW)
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT idempotency_key, amount_minor FROM wallet_ledger "
                        "WHERE entry_type = 'add_on_purchase' ORDER BY seq"
                    )
                )
            ).all()
            g = (
                await session.execute(
                    text("SELECT ends_at, renewed_through FROM add_on_grants WHERE id = :g"),
                    {"g": grant},
                )
            ).one()
            balance = (
                await session.execute(
                    text("SELECT balance_minor FROM billing_accounts WHERE tenant_id = :t"),
                    {"t": tenant},
                )
            ).scalar_one()
        out.update(
            first=first.renewed,
            again=again.renewed,
            keys=[r.idempotency_key for r in rows],
            amounts=[r.amount_minor for r in rows],
            ends_at=g.ends_at,
            renewed_through=g.renewed_through,
            balance=balance,
            grant=str(grant),
        )
        # the balance (160,000 - 3 x 49,900 = 10,300) cannot cover the next period: the grant ends
        # without a charge, and the workspace is not paused by it
        low = await addon_renewals.renew_due(factory, now=datetime(2026, 12, 1, 1, tzinfo=UTC))
        out["low_ended"] = [e.reason for e in low.ended]
        async with factory() as session:
            out["cancelled"] = (
                await session.execute(
                    text("SELECT cancelled_at FROM add_on_grants WHERE id = :g"), {"g": grant}
                )
            ).scalar_one()
            out["entries_after"] = (
                await session.execute(
                    text("SELECT count(*) FROM wallet_ledger WHERE entry_type = 'add_on_purchase'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    return out


def test_renewals_charge_each_missed_period_once_and_end_on_a_low_balance() -> None:
    from callobuzz_rag.billing import addon_renewals, config

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")  # the flow uses today's models, so today's schema
        with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "true"}):
            config.reset_billing_config_cache()
            try:
                out = asyncio.run(_renewals(url))
            finally:
                config.reset_billing_config_cache()
    grant = out["grant"]
    assert out["first"] == 3 and out["again"] == 0
    assert out["keys"] == [
        f"addon:{grant}:2026-09-01",
        f"addon:{grant}:2026-10-01",
        f"addon:{grant}:2026-11-01",
    ]
    assert out["amounts"] == [-49_900] * 3
    assert out["balance"] == 160_000 - 3 * 49_900  # I1: the balance is the sum of the ledger
    assert out["ends_at"] == datetime(2026, 12, 1, tzinfo=UTC)
    assert str(out["renewed_through"]) == "2026-11-01"
    assert out["low_ended"] == [addon_renewals.LOW_BALANCE] and out["cancelled"] is not None
    assert out["entries_after"] == 3


# ----------------------------------------------------------------------------- the seed command


async def _seed_flow(url: str) -> dict[str, Any]:
    import copy

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from callobuzz_rag.billing import seed_saas

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, Any] = {}

    async def counts() -> tuple[int, ...]:
        found = []
        async with factory() as session:
            for table in ("plans", "plan_pricing", "add_on_catalogue"):
                found.append(
                    int((await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())
                )
        return tuple(found)

    try:
        bundled = seed_saas.load_seed()
        out["before"] = await counts()
        dry = await seed_saas.run(factory, bundled, apply=False)
        out["after_dry"] = await counts()
        out["dry"] = (dry.made, dry.left)
        # the engine already has its own "Free" and "Standard" plans: they are left exactly as they
        # are (with the numbers the seed would have used), only what is missing is made
        first = await seed_saas.run(factory, bundled, apply=True)
        out["first"] = (first.made, first.left, first.failed)
        out["first_lines"] = first.lines
        out["after_first"] = await counts()
        again = await seed_saas.run(factory, bundled, apply=True)
        out["again"] = (again.made, again.left, again.failed)
        out["after_again"] = await counts()

        # a seed of plans that do not exist yet: made with their pricing, once
        fresh = copy.deepcopy(bundled)
        for item in fresh["plans"]:
            item["name"] = item["plan"]["name"] = f"Hosted {item['name']}"
        fresh["add_ons"] = []
        made = await seed_saas.run(factory, fresh, apply=True)
        out["fresh"] = (made.made, made.left, made.failed)
        redo = await seed_saas.run(factory, fresh, apply=True)
        out["redo"] = (redo.made, redo.left, redo.failed)
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT p.name, p.byok_allowed, p.monthly_questions, p.fair_use_limits, "
                        "pp.created_by, pp.subscription_fee_minor, pp.included_credit_minor, "
                        "pp.per_question_minor, pp.is_current FROM plans p "
                        "JOIN plan_pricing pp ON pp.plan_id = p.id WHERE p.name LIKE 'Hosted %'"
                    )
                )
            ).all()
            out["plans"] = {r.name: r for r in rows}
            out["add_ons"] = {
                r.name: (r.placeholder, r.sold_to_owners, r.kind)
                for r in (
                    await session.execute(
                        text("SELECT name, placeholder, sold_to_owners, kind FROM add_on_catalogue")
                    )
                ).all()
            }
            out["audit_actions"] = {
                r.action
                for r in (await session.execute(text("SELECT action FROM audit_log"))).all()
            }
    finally:
        await engine.dispose()
    return out


def test_the_seed_writes_once_and_never_changes_what_exists() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")  # the seed writes through today's models, so today's schema
        out = asyncio.run(_seed_flow(url))
    assert out["after_dry"] == out["before"]  # a dry run writes nothing
    # the engine's own "Free" and "Standard" exist: only "Pro" and the 7 add-ons are new
    assert out["dry"] == (8, 2), out["first_lines"]
    plans_before, pricing_before, add_ons_before = out["before"]
    assert out["first"] == (8, 2, 0)
    assert out["after_first"] == (plans_before + 1, pricing_before + 1, add_ons_before + 7)
    assert any("Standard: exists, left as it is" in line for line in out["first_lines"])
    assert any("the seed would use fee 999.00" in line for line in out["first_lines"])
    assert out["again"] == (0, 10, 0) and out["after_again"] == out["after_first"]

    assert out["fresh"] == (3, 0, 0) and out["redo"] == (0, 3, 0)
    free = out["plans"]["Hosted Free"]
    assert free.byok_allowed is False and free.monthly_questions is None
    assert free.fair_use_limits["questions_per_hour"] and free.fair_use_limits["questions_per_day"]
    assert free.created_by == "seed:placeholder" and free.is_current is True
    assert (free.subscription_fee_minor, free.included_credit_minor, free.per_question_minor) == (
        0,
        5_000,
        50,
    )
    assert out["plans"]["Hosted Standard"].byok_allowed is False
    pro = out["plans"]["Hosted Pro"]
    assert pro.byok_allowed is True and pro.subscription_fee_minor == 499_900
    assert out["plans"]["Hosted Standard"].subscription_fee_minor == 99_900
    assert len(out["add_ons"]) == 7 and all(v[0] and v[1] for v in out["add_ons"].values())
    assert {v[2] for v in out["add_ons"].values()} == {"credit_pack", "feature_unlock", "service"}
    assert {"plan.created", "billing.add_on_created"} <= out["audit_actions"]
