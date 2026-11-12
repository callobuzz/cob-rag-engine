"""phase20_002 on PostgreSQL: existing plans read `self_service = false`, a deleted plan only
clears a workspace's requested plan, and the migration goes down and up again. Needs Docker."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_landing_db import _alembic

pytestmark = pytest.mark.integration


async def _columns(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT table_name || '.' || column_name AS name FROM "
                    "information_schema.columns WHERE (table_name, column_name) IN "
                    "(('plans', 'self_service'), ('tenants', 'requested_plan_id'))"
                )
            )
            return {r.name for r in rows}
    finally:
        await engine.dispose()


async def _exercise(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            flags = (await c.execute(text("SELECT DISTINCT self_service FROM plans"))).scalars()
            assert set(flags.all()) == {False}  # every plan that existed stays operator-assigned
            plan, tenant = uuid.uuid4(), uuid.uuid4()
            await c.execute(
                text("INSERT INTO plans (id, name, self_service) VALUES (:i, 'Pro P6', true)"),
                {"i": plan},
            )
            await c.execute(
                text(
                    "INSERT INTO tenants (id, name, plan_id, requested_plan_id) VALUES "
                    "(:t, 'Kaveri', (SELECT id FROM plans WHERE is_default), :p)"
                ),
                {"t": tenant, "p": plan},
            )
            await c.execute(text("DELETE FROM plans WHERE id = :i"), {"i": plan})
            left = await c.execute(
                text("SELECT requested_plan_id FROM tenants WHERE id = :t"), {"t": tenant}
            )
            assert left.scalar_one() is None
    finally:
        await engine.dispose()


def test_plan_choice_columns_round_trip() -> None:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # Docker is not running
        pytest.skip(f"Docker is not available: {exc}")
    try:
        url = _async_url(container)
        _alembic(url, "phase20_002")
        assert asyncio.run(_columns(url)) == {"plans.self_service", "tenants.requested_plan_id"}
        asyncio.run(_exercise(url))
        _alembic(url, "phase20_001", down=True)
        assert asyncio.run(_columns(url)) == set()
        _alembic(url, "phase20_002")
        assert len(asyncio.run(_columns(url))) == 2
    finally:
        container.stop()
