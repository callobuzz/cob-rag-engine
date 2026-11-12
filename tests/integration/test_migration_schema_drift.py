"""phase12_008: models and migrations agree. Requires Docker.

A sync test: alembic's env.py calls asyncio.run(). `alembic check` fails when autogenerate would
emit any operation, so a model change without its migration (or the reverse) fails here.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _cfg(url: str):  # type: ignore[no-untyped-def]
    # The Content Portal models register only behind RAG_ENABLE_CONTENT_PORTAL, but the
    # migrations always create their tables; load them so autogenerate sees the whole schema.
    from alembic.config import Config

    import callobuzz_rag.content_portal.models  # noqa: F401

    cfg = Config(str(RAG_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _sql(url: str, statement: str, params: dict[str, object] | None = None) -> None:
    async def go() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as c:
                await c.execute(text(statement), params or {})
        finally:
            await engine.dispose()

    asyncio.run(go())


def test_models_match_migrated_schema() -> None:
    from alembic import command

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        command.upgrade(_cfg(url), "head")
        command.check(_cfg(url))  # raises CommandError on any drift


def test_drift_migration_backfills_null_timestamps_and_round_trips() -> None:
    from alembic import command

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        command.upgrade(_cfg(url), "phase12_004")
        plan = uuid.uuid4()
        _sql(
            url,
            "INSERT INTO plans (id, name, created_at, updated_at) VALUES (:i, 'Old', NULL, NULL)",
            {"i": plan},
        )
        command.upgrade(_cfg(url), "head")

        async def read() -> tuple[object, object]:
            engine = create_async_engine(url)
            try:
                async with engine.connect() as c:
                    row = (
                        await c.execute(
                            text("SELECT created_at, updated_at FROM plans WHERE id = :i"),
                            {"i": plan},
                        )
                    ).one()
                    return row[0], row[1]
            finally:
                await engine.dispose()

        created, updated = asyncio.run(read())
        assert created is not None
        assert updated == created
        command.downgrade(_cfg(url), "phase12_004")
        command.upgrade(_cfg(url), "head")
        command.check(_cfg(url))
