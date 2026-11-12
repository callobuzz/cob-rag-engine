"""phase12_003 round-trip on PostgreSQL (testcontainers, conversation C4). Requires Docker.

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

PARENT = "phase12_006"


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


def _columns(table: str) -> Callable[[AsyncConnection], Awaitable[set[str]]]:
    async def work(c: AsyncConnection) -> set[str]:
        rows = await c.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
            {"t": table},
        )
        return {r[0] for r in rows}

    return work


async def _exists(c: AsyncConnection, table: str) -> bool:
    return (
        await c.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"})
    ).scalar() is not None


def test_leads_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, PARENT)
        _alembic(url, "phase12_003")

        leads = _run(url, _columns("leads"))
        assert {"fields_encrypted", "phone_hash", "retention_until", "notified_at"} <= leads
        assert not {"phone", "email", "name"} & leads
        assert "corpus_id" in _run(url, _columns("webhook_config"))

        tenant, corpus, hook = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        async def seed(c: AsyncConnection) -> None:
            await c.execute(
                text(
                    "INSERT INTO tenants (id, name, plan_id) "
                    "SELECT :id, 'Acme', id FROM plans WHERE is_default LIMIT 1"
                ),
                {"id": tenant},
            )
            await c.execute(
                text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'kb', :t)"),
                {"id": corpus, "t": tenant},
            )
            await c.execute(
                text(
                    "INSERT INTO webhook_config (id, url, secret, corpus_id) "
                    "VALUES (:id, 'https://example.com/h', 'x', :c)"
                ),
                {"id": hook, "c": corpus},
            )

        _run(url, seed)

        _alembic(url, PARENT, down=True)

        async def after_down(c: AsyncConnection) -> tuple[bool, bool, int]:
            count = (await c.execute(text("SELECT count(*) FROM webhook_config"))).scalar_one()
            return await _exists(c, "leads"), await _exists(c, "lead_consents"), count

        assert _run(url, after_down) == (False, False, 0)
        assert "corpus_id" not in _run(url, _columns("webhook_config"))

        _alembic(url, "phase12_003")
        assert "corpus_id" in _run(url, _columns("webhook_config"))
