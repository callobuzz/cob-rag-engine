"""phase12_004: the handoffs table, its cascades and a clean downgrade. Requires Docker.

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


def test_upgrade_check_cascade_and_downgrade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase12_004")
        tenant, corpus = uuid.uuid4(), uuid.uuid4()

        async def work(c: AsyncConnection) -> None:
            await c.execute(
                text(
                    "INSERT INTO tenants (id, name, plan_id) "
                    "SELECT :id, 'Shree Labs', id FROM plans WHERE is_default LIMIT 1"
                ),
                {"id": tenant},
            )
            await c.execute(
                text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'Help', :t)"),
                {"id": corpus, "t": tenant},
            )
            await c.execute(
                text(
                    "INSERT INTO handoffs (corpus_id, tenant_id, channel, reason)"
                    " VALUES (:c, :t, 'whatsapp', 'keyword')"
                ),
                {"c": corpus, "t": tenant},
            )
            status = (await c.execute(text("SELECT status FROM handoffs"))).scalar_one()
            assert status == "new"
            with pytest.raises(IntegrityError):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "INSERT INTO handoffs (corpus_id, tenant_id, channel, reason, status)"
                            " VALUES (:c, :t, 'call', 'keyword', 'open')"
                        ),
                        {"c": corpus, "t": tenant},
                    )
            await c.execute(text("DELETE FROM corpora WHERE id = :c"), {"c": corpus})
            left = (await c.execute(text("SELECT count(*) FROM handoffs"))).scalar_one()
            assert left == 0

        _run(url, work)
        _alembic(url, "phase12_007", down=True)

        async def gone(c: AsyncConnection) -> bool:
            row = (await c.execute(text("SELECT to_regclass('handoffs')"))).scalar_one()
            return row is None

        assert _run(url, gone)
