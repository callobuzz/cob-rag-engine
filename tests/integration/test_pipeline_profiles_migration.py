"""phase16_001 on PostgreSQL (PIP1). Needs Docker (skipped with the integration suite when down).

- the three seeded profiles, one default, the Internal plan on Premium, a seeded "Free" plan;
- a pre-existing plan named "Free" is left alone and no second one is made;
- a profile a plan uses cannot be deleted (RESTRICT); deleting a business cascades its settings;
- the migration round-trips.

Sync tests (alembic's env.py calls asyncio.run()).
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.tiers.seeds import SEEDS
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration


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
    rows = _sql(
        url,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name IN ('pipeline_profiles', 'tenant_pipeline_settings')",
    )
    return {r[0] for r in rows}


def test_seeds_and_plans() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        _alembic(url, "phase16_001")
        rows = _sql(
            url,
            "SELECT builtin_key, name, label, is_default, config FROM pipeline_profiles "
            "ORDER BY builtin_key",
        )
        assert [r[0] for r in rows] == ["free", "premium", "standard"]
        for key, name, label, is_default, config in rows:
            assert (name, label, is_default, config) == (
                SEEDS[key]["name"],
                SEEDS[key]["label"],
                SEEDS[key]["is_default"],
                SEEDS[key]["config"],
            )
        assert [r[0] for r in rows if r[3]] == ["standard"]

        internal = _sql(
            url,
            "SELECT p.builtin_key FROM plans pl JOIN pipeline_profiles p "
            "ON p.id = pl.pipeline_profile_id WHERE pl.is_internal",
        )
        assert internal == [("premium",)]
        free = _sql(
            url,
            "SELECT p.builtin_key, pl.website_pages_max, pl.is_default, pl.is_active "
            "FROM plans pl JOIN pipeline_profiles p ON p.id = pl.pipeline_profile_id "
            "WHERE pl.name = 'Free'",
        )
        assert free == [("free", 200, False, False)]  # inactive until the local model is ready
        # every other plan keeps the default profile (NULL)
        assert _sql(
            url,
            "SELECT count(*) FROM plans WHERE pipeline_profile_id IS NOT NULL "
            "AND NOT is_internal AND name <> 'Free'",
        ) == [(0,)]


def test_existing_free_plan_is_untouched() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        _sql(url, "INSERT INTO plans (name, monthly_questions) VALUES ('Free', 50)")
        _alembic(url, "phase16_001")
        rows = _sql(
            url, "SELECT monthly_questions, pipeline_profile_id FROM plans WHERE name = 'Free'"
        )
        assert rows == [(50, None)]


def test_restrict_cascade_and_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase16_001")
        # RESTRICT: the Free plan uses the free profile
        with pytest.raises(IntegrityError):
            _sql(url, "DELETE FROM pipeline_profiles WHERE builtin_key = 'free'")
        # two defaults are refused
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE pipeline_profiles SET is_default = true WHERE builtin_key = 'free'")

        tenant = _sql(
            url,
            "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
            "SELECT 'Shree Dental Clinic', '', 'active', id FROM plans WHERE is_default "
            "RETURNING id",
        )[0][0]
        _sql(
            url,
            "INSERT INTO tenant_pipeline_settings (tenant_id, profile_id) "
            "SELECT :t, id FROM pipeline_profiles WHERE builtin_key = 'premium'",
            t=tenant,
        )
        with pytest.raises(IntegrityError):  # in use by a business
            _sql(url, "DELETE FROM pipeline_profiles WHERE builtin_key = 'premium'")
        _sql(url, "DELETE FROM tenants WHERE id = :t", t=tenant)
        assert _sql(url, "SELECT count(*) FROM tenant_pipeline_settings") == [(0,)]

        _alembic(url, "phase15_002", down=True)
        assert _tables(url) == set()
        assert _sql(url, "SELECT count(*) FROM plans WHERE name = 'Free'") == [(0,)]
        _alembic(url, "phase16_001")
        assert _tables(url) == {"pipeline_profiles", "tenant_pipeline_settings"}
