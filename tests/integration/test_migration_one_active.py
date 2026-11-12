"""phase10_004 resolves duplicate active versions before it creates its unique index.

Runs on its own PostgreSQL, since it walks the schema back to phase10_003 and forward
again. A sync test: alembic's env.py calls asyncio.run(). Requires Docker.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _alembic(url: str, target: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, target)


def test_duplicate_active_versions_keep_the_newest() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase10_003")
        corpus, doc = uuid.uuid4(), uuid.uuid4()
        old, new, single_doc, single = (uuid.uuid4() for _ in range(4))

        async def seed() -> None:
            engine = create_async_engine(url)
            async with engine.begin() as c:
                await c.execute(
                    text("INSERT INTO corpora (id, name) VALUES (:id, 'c')"), {"id": corpus}
                )
                for d in (doc, single_doc):
                    await c.execute(
                        text("INSERT INTO documents (id, corpus_id) VALUES (:id, :c)"),
                        {"id": d, "c": corpus},
                    )
                for v, d, when in (
                    (old, doc, datetime(2026, 1, 1, tzinfo=UTC)),
                    (new, doc, datetime(2026, 2, 1, tzinfo=UTC)),
                    (single, single_doc, datetime(2026, 1, 1, tzinfo=UTC)),
                ):
                    await c.execute(
                        text(
                            "INSERT INTO document_versions (id, document_id, status, created_at) "
                            "VALUES (:id, :d, 'active', :t)"
                        ),
                        {"id": v, "d": d, "t": when},
                    )
            await engine.dispose()

        async def statuses() -> dict[uuid.UUID, str]:
            engine = create_async_engine(url)
            async with engine.connect() as c:
                rows = (await c.execute(text("SELECT id, status FROM document_versions"))).all()
            await engine.dispose()
            return {r.id: r.status for r in rows}

        asyncio.run(seed())
        _alembic(url, "head")

        assert asyncio.run(statuses()) == {old: "inactive", new: "active", single: "active"}
