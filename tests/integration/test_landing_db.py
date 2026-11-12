"""phase20_001 on PostgreSQL: upgrade, downgrade, upgrade; one site row; deleting the knowledge
base only clears the "Ask it yourself" link. Needs Docker."""

import asyncio
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
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


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('landing_sites','landing_versions','landing_assets')"
                )
            )
            return {r.table_name for r in rows}
    finally:
        await engine.dispose()


async def _exercise(url: str) -> None:
    engine = create_async_engine(url)
    insert = text(
        "INSERT INTO landing_sites (id, draft, draft_theme, ask_corpus_id) "
        "VALUES (:id, '{}', '{}', :corpus)"
    )
    try:
        async with engine.begin() as c:
            tenant = (
                await c.execute(
                    text(
                        "INSERT INTO tenants (name, api_key_hash, status, plan_id) SELECT 'Shree', '', 'active', id FROM plans WHERE is_default RETURNING id"
                    )
                )
            ).scalar_one()
            corpus = uuid.uuid4()
            await c.execute(
                text(
                    "INSERT INTO corpora (id, name, api_key_hash, tenant_id, qdrant_collection) "
                    "VALUES (:id, 'kb', '', :t, 'corpus_aaaa0000')"
                ),
                {"id": corpus, "t": tenant},
            )
            await c.execute(insert, {"id": 1, "corpus": corpus})
            await c.execute(text("DELETE FROM corpora WHERE id = :id"), {"id": corpus})
            link = (
                await c.execute(text("SELECT ask_corpus_id FROM landing_sites WHERE id = 1"))
            ).scalar_one()
            assert link is None
        with pytest.raises(IntegrityError):
            async with engine.begin() as c:
                await c.execute(insert, {"id": 2, "corpus": None})
    finally:
        await engine.dispose()


def test_landing_tables_round_trip_and_one_row() -> None:
    try:
        container = PostgresContainer("postgres:16-alpine")
        container.start()
    except Exception as exc:  # Docker is not running
        pytest.skip(f"Docker is not available: {exc}")
    try:
        url = _async_url(container)
        _alembic(url, "phase20_001")
        assert asyncio.run(_tables(url)) == {"landing_sites", "landing_versions", "landing_assets"}
        asyncio.run(_exercise(url))
        _alembic(url, "phase19_004", down=True)
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase20_001")
        assert len(asyncio.run(_tables(url))) == 3
    finally:
        container.stop()
