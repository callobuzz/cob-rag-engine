"""P1 on a real PostgreSQL: the migration round trip and the integrity rules of the payment tables.

A sync test for the migration (alembic's env.py calls asyncio.run()). Requires Docker.
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

PAYMENT_TABLES = (
    "payment_provider_settings",
    "billing_profiles",
    "payment_orders",
    "payments",
    "payment_events",
    "payment_refunds",
)


def _sql(url: str, statement: str, **params: Any) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _tables(url: str) -> set[str]:
    rows = _sql(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    return {r[0] for r in rows}


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_001")
        assert set(PAYMENT_TABLES) <= _tables(url)

        _alembic(url, "phase18_001", down=True)
        assert not set(PAYMENT_TABLES) & _tables(url)

        _alembic(url, "phase19_001")
        assert set(PAYMENT_TABLES) <= _tables(url)

        # Financial rows have no tenant foreign key: an order for an unknown tenant is accepted.
        _sql(
            url,
            "INSERT INTO payment_orders (tenant_id, account_id, purpose, currency, taxable_minor,"
            " gross_minor, tax_kind, place_of_supply, description, sac_code, provider,"
            " idempotency_key, created_by) VALUES (gen_random_uuid(), gen_random_uuid(),"
            " 'top_up', 'INR', 100, 100, 'no_gst', '27', 'x', '998315', 'razorpay', 'k1', 'a@b.in')",
        )
        # gross must equal the sum of its parts
        with pytest.raises(IntegrityError):
            _sql(
                url,
                "INSERT INTO payment_orders (tenant_id, account_id, purpose, currency,"
                " taxable_minor, gross_minor, tax_kind, place_of_supply, description, sac_code,"
                " provider, idempotency_key, created_by) VALUES (gen_random_uuid(),"
                " gen_random_uuid(), 'top_up', 'INR', 100, 150, 'no_gst', '27', 'x', '998315',"
                " 'razorpay', 'k2', 'a@b.in')",
            )


def test_downgrade_refuses_gateway_grants() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_001")
        constraint = _sql(
            url,
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_add_on_grant_payment'",
        )
        assert "gateway" in constraint[0][0]
