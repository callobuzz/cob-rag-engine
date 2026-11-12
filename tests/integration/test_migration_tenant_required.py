"""phase11_001 (accounts A1): every corpus belongs to a tenant.

Tenant-less corpora move into one new tenant named Default (only when there are any),
`usage_daily.tenant_id` is backfilled, both columns become NOT NULL and the corpus FK
becomes RESTRICT. Downgrade relaxes the schema again and leaves the data where it is.

Runs on its own PostgreSQL, since it walks the schema from phase10_005 forward and back.
A sync test: alembic's env.py calls asyncio.run(). Requires Docker.
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
    """Run `work` in one committed transaction on a fresh engine."""

    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as c:
                return await work(c)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _fails(url: str, sql: str, params: dict[str, Any]) -> bool:
    async def attempt(c: AsyncConnection) -> None:
        await c.execute(text(sql), params)

    try:
        _run(url, attempt)
    except IntegrityError:
        return True
    return False


def test_orphans_move_to_one_default_tenant_and_the_schema_tightens() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase10_005")
        owner, owned, orphan_a, orphan_b = (uuid.uuid4() for _ in range(4))

        async def seed(c: AsyncConnection) -> None:
            await c.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, 'Acme')"), {"id": owner}
            )
            await c.execute(
                text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'owned', :t)"),
                {"id": owned, "t": owner},
            )
            for cid in (orphan_a, orphan_b):
                await c.execute(
                    text("INSERT INTO corpora (id, name) VALUES (:id, 'orphan')"), {"id": cid}
                )
            await c.execute(
                text(
                    "INSERT INTO usage_daily (day, corpus_id, tenant_id, source) "
                    "VALUES (CURRENT_DATE, :c, NULL, 'api')"
                ),
                {"c": orphan_a},
            )

        _run(url, seed)
        _alembic(url, "phase11_001")

        async def state(c: AsyncConnection) -> dict[str, Any]:
            defaults = (
                await c.execute(
                    text("SELECT id, max_corpora, api_key_hash FROM tenants WHERE name = 'Default'")
                )
            ).all()
            corpora = dict((await c.execute(text("SELECT id, tenant_id FROM corpora"))).all())
            usage = (await c.execute(text("SELECT tenant_id FROM usage_daily"))).scalars().all()
            return {"defaults": defaults, "corpora": corpora, "usage": list(usage)}

        s = _run(url, state)
        assert len(s["defaults"]) == 1
        default_id, max_corpora, key_hash = s["defaults"][0]
        assert max_corpora == 10
        assert key_hash == ""  # no working tk_ key; the admin rotates one
        assert s["corpora"] == {owned: owner, orphan_a: default_id, orphan_b: default_id}
        assert s["usage"] == [default_id]

        # A corpus with no tenant is refused; a tenant that owns corpora cannot be deleted.
        assert _fails(url, "INSERT INTO corpora (id, name) VALUES (:id, 'x')", {"id": uuid.uuid4()})
        assert _fails(url, "DELETE FROM tenants WHERE id = :id", {"id": owner})
        assert _fails(
            url,
            "INSERT INTO usage_daily (day, corpus_id, tenant_id, source) "
            "VALUES (CURRENT_DATE, :c, NULL, 'stream')",
            {"c": owned},
        )

        async def fk(c: AsyncConnection) -> list[tuple[str, str]]:
            rows = await c.execute(
                text(
                    "SELECT conname, confdeltype::text FROM pg_constraint "
                    "WHERE conrelid = 'corpora'::regclass AND contype = 'f' "
                    "AND conkey = ARRAY[(SELECT attnum FROM pg_attribute "
                    "WHERE attrelid = 'corpora'::regclass AND attname = 'tenant_id')]"
                )
            )
            return [(r[0], r[1]) for r in rows]

        assert _run(url, fk) == [("fk_corpora_tenant_id", "r")]  # r = RESTRICT

        # Downgrade round-trip: the schema relaxes, corpora stay in Default.
        _alembic(url, "phase10_005", down=True)
        assert _run(url, fk) == [("corpora_tenant_id_fkey", "n")]  # n = SET NULL
        loose = uuid.uuid4()

        async def insert_loose(c: AsyncConnection) -> None:
            await c.execute(
                text("INSERT INTO corpora (id, name) VALUES (:id, 'loose')"), {"id": loose}
            )

        _run(url, insert_loose)
        assert _run(url, state)["corpora"][orphan_a] == default_id

        # And forward again: the new orphan gets a second Default of its own.
        _alembic(url, "head")
        again = _run(url, state)
        assert len(again["defaults"]) == 2
        assert again["corpora"][loose] not in (None, default_id)
        assert _run(url, fk) == [("fk_corpora_tenant_id", "r")]


def test_fresh_database_gets_no_default_tenant() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")

        async def tenants(c: AsyncConnection) -> int:
            return int((await c.execute(text("SELECT count(*) FROM tenants"))).scalar_one())

        assert _run(url, tenants) == 0
