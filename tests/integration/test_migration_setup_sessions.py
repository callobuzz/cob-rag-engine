"""phase12_006 round-trip on PostgreSQL (testcontainers, conversation C8). Requires Docker.

A sync test: alembic's env.py calls asyncio.run().
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration

PARENT = "phase12_005"


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


def test_upgrade_constraints_cascade_and_downgrade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, PARENT)
        tenant, corpus, gset, run = (uuid.uuid4() for _ in range(4))

        async def seed(c: AsyncConnection) -> None:
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
                    "INSERT INTO golden_sets (id, corpus_id, name, source)"
                    " VALUES (:g, :c, 'Setup checks', 'setup')"
                ),
                {"g": gset, "c": corpus},
            )
            await c.execute(
                text(
                    "INSERT INTO eval_run (id, corpus_id, triggered_by, status, sample_size)"
                    " VALUES (:r, :c, 'setup', 'completed', 0)"
                ),
                {"r": run, "c": corpus},
            )

        _run(url, seed)
        _alembic(url, "phase12_006")

        async def after(c: AsyncConnection) -> None:
            fks = (
                await c.execute(
                    text(
                        "SELECT confrelid::regclass::text, confdeltype::text FROM pg_constraint"
                        " WHERE conrelid = 'assistant_setup_sessions'::regclass AND contype = 'f'"
                    )
                )
            ).all()
            assert dict(fks) == {"corpora": "c", "golden_sets": "n", "eval_run": "n"}
            await c.execute(
                text(
                    "INSERT INTO assistant_setup_sessions"
                    " (corpus_id, created_by, golden_set_id, eval_run_id)"
                    " VALUES (:c, 'owner', :g, :r)"
                ),
                {"c": corpus, "g": gset, "r": run},
            )
            await c.execute(text("DELETE FROM golden_sets WHERE id = :g"), {"g": gset})
            await c.execute(text("DELETE FROM eval_run WHERE id = :r"), {"r": run})
            row = (
                await c.execute(
                    text("SELECT golden_set_id, eval_run_id, status FROM assistant_setup_sessions")
                )
            ).one()
            assert tuple(row) == (None, None, "started")  # SET NULL keeps the session
            await c.execute(text("DELETE FROM corpora WHERE id = :c"), {"c": corpus})
            left = (
                await c.execute(text("SELECT count(*) FROM assistant_setup_sessions"))
            ).scalar_one()
            assert left == 0  # corpus delete cascades

        _run(url, after)

        async def bogus(c: AsyncConnection) -> None:
            await c.execute(
                text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'B', :t)"),
                {"id": corpus, "t": tenant},
            )
            await c.execute(
                text(
                    "INSERT INTO assistant_setup_sessions (corpus_id, created_by, status)"
                    " VALUES (:c, 'owner', 'bogus')"
                ),
                {"c": corpus},
            )

        with pytest.raises(DBAPIError):
            _run(url, bogus)

        _alembic(url, PARENT, down=True)

        async def present(c: AsyncConnection) -> bool:
            return bool(
                (
                    await c.execute(text("SELECT to_regclass('assistant_setup_sessions')"))
                ).scalar_one()
            )

        assert _run(url, present) is False
