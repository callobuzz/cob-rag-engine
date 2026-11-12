"""phase21_001 drops `license_state`; its downgrade makes one empty row again (needs Docker)."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import RAG_ROOT, _async_url

pytestmark = pytest.mark.integration


def _query(url: str, sql: str) -> list[Any]:
    async def run() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                return [tuple(r) for r in (await conn.execute(text(sql))).all()]
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _exists(url: str) -> bool:
    return _query(url, "SELECT to_regclass('public.license_state')")[0][0] is not None


def test_upgrade_drops_the_table_and_downgrade_recreates_one_empty_row() -> None:
    from alembic import command
    from alembic.config import Config

    # a database of its own: the shared one may hold issued tax documents, and an older
    # downgrade refuses while any exist
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        cfg = Config(str(RAG_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(RAG_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "phase20_003")
        assert _exists(url)
        command.upgrade(cfg, "phase21_001")
        assert not _exists(url)
        command.downgrade(cfg, "-1")
        rows = _query(url, "SELECT id, token, refusal FROM license_state")
        assert rows == [(1, None, None)]
        command.upgrade(cfg, "head")
        assert not _exists(url)
