"""phase13_001: the COB system flags, their partial uniques and the cob_status row. Needs Docker.

A sync test: alembic's env.py calls asyncio.run().
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _alembic(url: str, target: str, *, down: bool = False) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    (command.downgrade if down else command.upgrade)(cfg, target)


def _run(url: str, work: Callable[[AsyncConnection], Awaitable[Any]]) -> Any:
    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as c:
                return await work(c)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def _columns(c: AsyncConnection) -> set[tuple[str, str]]:
    rows = await c.execute(
        text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE column_name = 'is_system'"
        )
    )
    return {(r.table_name, r.column_name) for r in rows}


def test_upgrade_unique_and_downgrade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase13_001")

        async def check(c: AsyncConnection) -> None:
            assert await _columns(c) == {("tenants", "is_system"), ("corpora", "is_system")}
            row = (
                await c.execute(text("SELECT id, sync_state, document_count FROM cob_status"))
            ).one()
            assert (row.id, row.sync_state, row.document_count) == (1, "idle", 0)

        _run(url, check)

        async def two_system_tenants(c: AsyncConnection) -> None:
            for name in ("COB one", "COB two"):
                await c.execute(
                    text(
                        "INSERT INTO tenants (id, name, plan_id, is_system) "
                        "SELECT :id, :n, id, true FROM plans WHERE is_default LIMIT 1"
                    ),
                    {"id": uuid.uuid4(), "n": name},
                )

        with pytest.raises(IntegrityError):
            _run(url, two_system_tenants)

        _alembic(url, "phase12_008", down=True)

        async def gone(c: AsyncConnection) -> None:
            assert await _columns(c) == set()
            found = await c.execute(text("SELECT to_regclass('cob_status')"))
            assert found.scalar() is None

        _run(url, gone)
