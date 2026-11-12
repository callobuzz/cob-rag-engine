"""phase12_002 round-trip on PostgreSQL (testcontainers). Requires Docker.

A sync test: alembic's env.py calls asyncio.run().
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
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


def test_upgrade_cascade_and_downgrade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase12_001")
        tenant, corpus, conv = (uuid.uuid4() for _ in range(3))

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
                text("INSERT INTO conversations (id, corpus_id) VALUES (:id, :c)"),
                {"id": conv, "c": corpus},
            )

        _run(url, seed)
        _alembic(url, "phase12_002")

        async def after(c: AsyncConnection) -> int:
            channel = (await c.execute(text("SELECT channel FROM conversations"))).scalar_one()
            assert channel == "api"
            await c.execute(
                text(
                    "INSERT INTO assistant_turns (corpus_id, tenant_id, conversation_id, channel,"
                    " label, label_source, language, script, route, question_masked) VALUES"
                    " (:c, :t, :v, 'widget', 'smalltalk', 'rules', 'en', 'latin', 'smalltalk', 'hi')"
                ),
                {"c": corpus, "t": tenant, "v": conv},
            )
            await c.execute(text("DELETE FROM conversations WHERE id = :v"), {"v": conv})
            kept = (await c.execute(text("SELECT conversation_id FROM assistant_turns"))).all()
            assert kept == [(None,)]  # SET NULL keeps the counts
            await c.execute(text("DELETE FROM corpora WHERE id = :c"), {"c": corpus})
            return int((await c.execute(text("SELECT count(*) FROM assistant_turns"))).scalar_one())

        assert _run(url, after) == 0  # corpus delete cascades

        _alembic(url, "phase12_001", down=True)

        async def tables(c: AsyncConnection) -> bool:
            return bool(
                (await c.execute(text("SELECT to_regclass('assistant_turns')"))).scalar_one()
            )

        assert _run(url, tables) is False
