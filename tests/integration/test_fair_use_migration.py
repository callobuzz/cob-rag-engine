"""phase16_002 on PostgreSQL (PIP3). Needs Docker (skipped with the integration suite when down).

- the new plan and settings columns, the counters and alerts tables;
- the seeded Free plan gets its limits, any other plan stays `{}`;
- `usage_charges.waived` takes 'downgraded' and refuses anything else;
- the engine-wide alert row (tenant NULL) is unique too; a business delete cascades;
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
        "WHERE table_name IN ('fair_use_counters', 'fair_use_alerts')",
    )
    return {r[0] for r in rows}


def test_free_plan_limits_and_other_plans_untouched() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase16_001")
        _sql(url, "INSERT INTO plans (name) VALUES ('Growth')")
        _alembic(url, "phase16_002")
        free = _sql(url, "SELECT fair_use_limits, fair_use_policy FROM plans WHERE name = 'Free'")
        assert free == [
            ({"questions_per_hour": 30, "questions_per_day": 300, "fetches_per_day": 50}, {})
        ]
        other = _sql(url, "SELECT fair_use_limits FROM plans WHERE name = 'Growth'")
        assert other == [({},)]


def test_constraints_cascade_and_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase16_002")
        tenant = _sql(
            url,
            "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
            "SELECT 'Shree Dental Clinic', '', 'active', id FROM plans WHERE is_default "
            "RETURNING id",
        )[0][0]
        _sql(url, "INSERT INTO tenant_pipeline_settings (tenant_id) VALUES (:t)", t=tenant)
        assert _sql(url, "SELECT state FROM tenant_pipeline_settings") == [("ok",)]
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE tenant_pipeline_settings SET state = 'broken'")

        _sql(
            url,
            "INSERT INTO fair_use_counters (tenant_id, day, metric, value) "
            "VALUES (:t, current_date, 'uploads', 2)",
            t=tenant,
        )
        alert = "INSERT INTO fair_use_alerts (tenant_id, metric, period_key, level_pct) "
        _sql(url, alert + "VALUES (:t, 'questions_per_hour', '2026-10-05T14', 80)", t=tenant)
        with pytest.raises(IntegrityError):
            _sql(url, alert + "VALUES (:t, 'questions_per_hour', '2026-10-05T14', 80)", t=tenant)
        # the engine-wide rail (tenant NULL) is unique too
        _sql(url, alert + "VALUES (NULL, 'engine_ai_spend_per_day', '2026-10-05', 80)")
        with pytest.raises(IntegrityError):
            _sql(url, alert + "VALUES (NULL, 'engine_ai_spend_per_day', '2026-10-05', 80)")

        # a downgraded charge is accepted (zero amount); anything else is refused
        _waived_check(url)

        _sql(url, "DELETE FROM tenants WHERE id = :t", t=tenant)
        assert _sql(url, "SELECT count(*) FROM fair_use_counters") == [(0,)]
        assert _sql(url, "SELECT count(*) FROM fair_use_alerts WHERE tenant_id IS NOT NULL") == [
            (0,)
        ]

        _alembic(url, "phase16_001", down=True)
        assert _tables(url) == set()
        _alembic(url, "phase16_002")
        assert _tables(url) == {"fair_use_counters", "fair_use_alerts"}


def _waived_check(url: str) -> None:
    rows = _sql(
        url,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'ck_usage_charges_waived'",
    )
    assert "downgraded" in rows[0][0]
