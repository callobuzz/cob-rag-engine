"""phase11_008 (accounts A8) on a real PostgreSQL: `plans.is_builtin` marks exactly the plan
phase11_006 seeded, even when the operator already added plans (one of them named first) or
renamed the seeded one; at most one plan can be built in; downgrade drops the column.

A sync test: alembic's env.py calls asyncio.run(). Requires Docker.
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


def test_the_seeded_plan_is_marked_and_only_it() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_007")
        # The operator's own plans, created later, one sorting before "Standard"; and the
        # seeded plan renamed (its description is what identifies it).
        _sql(
            url, "INSERT INTO plans (name, created_at) VALUES ('Aardvark', now() + interval '1 s')"
        )
        _sql(url, "INSERT INTO plans (name, created_at) VALUES ('Pro', now() + interval '2 s')")
        _sql(url, "UPDATE plans SET name = 'Unlimited' WHERE name = 'Standard'")

        _alembic(url, "phase11_008")
        rows = _sql(url, "SELECT name, is_builtin, is_default FROM plans ORDER BY name")
        assert rows == [
            ("Aardvark", False, False),
            ("Pro", False, False),
            ("Unlimited", True, True),
        ]
        with pytest.raises(IntegrityError, match="uq_plans_builtin"):
            _sql(url, "UPDATE plans SET is_builtin = true WHERE name = 'Pro'")
        new = _sql(url, "INSERT INTO plans (name) VALUES ('Later') RETURNING is_builtin")
        assert new == [(False,)]

        _alembic(url, "phase11_007", down=True)
        columns = _sql(
            url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'plans' AND column_name = 'is_builtin'",
        )
        assert columns == []
        _alembic(url, "head")
        assert _sql(url, "SELECT name FROM plans WHERE is_builtin") == [("Unlimited",)]


def test_without_the_description_the_earliest_standard_is_marked() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_007")
        _sql(url, "UPDATE plans SET description = 'Our free tier' WHERE name = 'Standard'")
        _alembic(url, "phase11_008")
        assert _sql(url, "SELECT name FROM plans WHERE is_builtin") == [("Standard",)]
