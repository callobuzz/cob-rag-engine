"""phase13_002: the usage key includes the tenant, and COB conversations carry a kind. Docker.

A sync test: alembic's env.py calls asyncio.run().
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration

DAY = date(2026, 10, 4)  # asyncpg binds a date parameter only from a `date`


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


async def _seed(c: AsyncConnection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    t1, t2, corpus = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for t in (t1, t2):
        await c.execute(
            text(
                "INSERT INTO tenants (id, name, plan_id) "
                "SELECT :id, :n, id FROM plans WHERE is_default LIMIT 1"
            ),
            {"id": t, "n": f"ws-{t}"},
        )
    await c.execute(
        text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'sys', :t)"),
        {"id": corpus, "t": t1},
    )
    return t1, t2, corpus


async def _usage(c: AsyncConnection, corpus: uuid.UUID, tenant: uuid.UUID, n: int) -> None:
    await c.execute(
        text(
            "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, requests, payer, model) "
            "VALUES (:d, :c, :t, 'cob', :n, 'operator', 'm') "
            "ON CONFLICT ON CONSTRAINT uq_usage_daily_day_corpus_tenant_source_payer_model "
            "DO UPDATE SET requests = usage_daily.requests + :n"
        ),
        {"d": DAY, "c": corpus, "t": tenant, "n": n},
    )


def test_upgrade_per_tenant_rows_and_downgrade_merge() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase13_002")

        t1, t2, corpus = _run(url, _seed)
        # one transaction each: `updated_at` is now(), the transaction's start, and the downgrade
        # keeps the earliest row; in one transaction the rows would tie and the id would decide
        for tenant, n in ((t1, 1), (t2, 2), (t1, 3)):
            _run(url, lambda c, tenant=tenant, n=n: _usage(c, corpus, tenant, n))

        async def check(c: AsyncConnection) -> None:
            rows = (
                await c.execute(
                    text("SELECT tenant_id, requests FROM usage_daily WHERE corpus_id = :c"),
                    {"c": corpus},
                )
            ).all()
            assert {r.tenant_id: r.requests for r in rows} == {t1: 4, t2: 2}
            await c.execute(
                text("INSERT INTO conversations (id, corpus_id) VALUES (:i, :c)"),
                {"i": uuid.uuid4(), "c": corpus},
            )
            kinds = (await c.execute(text("SELECT DISTINCT kind FROM conversations"))).all()
            assert [k.kind for k in kinds] == ["assistant"]

        _run(url, check)

        _alembic(url, "phase13_001", down=True)

        async def merged(c: AsyncConnection) -> None:
            rows = (
                await c.execute(
                    text("SELECT requests, tenant_id FROM usage_daily WHERE corpus_id = :c"),
                    {"c": corpus},
                )
            ).all()
            assert len(rows) == 1 and rows[0].requests == 6 and rows[0].tenant_id == t1
            cols = await c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'conversations' AND column_name IN ('kind','user_id')"
                )
            )
            assert cols.all() == []

        _run(url, merged)
