"""phase19_002 on PostgreSQL: published legal pages are frozen by a trigger, the draft is not,
one draft per slug, round trip. Needs Docker."""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
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
                    "WHERE table_name IN ('legal_documents','legal_acceptances')"
                )
            )
            return {r.table_name for r in rows}
    finally:
        await engine.dispose()


async def _exercise(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO legal_documents (slug, title, body_md) "
                    "VALUES ('terms', 'Terms', 'draft')"
                )
            )
            # the draft can be edited
            await c.execute(
                text("UPDATE legal_documents SET body_md = 'edited' WHERE slug='terms'")
            )
        # one draft per slug
        with pytest.raises(IntegrityError):
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO legal_documents (slug, title, body_md) "
                        "VALUES ('terms', 'Terms', 'second draft')"
                    )
                )
        # an unknown slug is refused
        with pytest.raises(IntegrityError):
            async with engine.begin() as c:
                await c.execute(
                    text("INSERT INTO legal_documents (slug, title, body_md) VALUES ('x','x','x')")
                )
        async with engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO legal_documents (slug, version, title, body_md, published_at) "
                    "VALUES ('terms', 1, 'Terms', 'v1', now())"
                )
            )
        # version without published_at is refused
        with pytest.raises(IntegrityError):
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "INSERT INTO legal_documents (slug, version, title, body_md) "
                        "VALUES ('terms', 2, 'Terms', 'v2')"
                    )
                )
        with pytest.raises(DBAPIError, match="cannot be changed"):
            async with engine.begin() as c:
                await c.execute(text("UPDATE legal_documents SET body_md = 'x' WHERE version = 1"))
        with pytest.raises(DBAPIError, match="cannot be changed"):
            async with engine.begin() as c:
                await c.execute(text("DELETE FROM legal_documents WHERE version = 1"))
        # the draft can be deleted
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM legal_documents WHERE version IS NULL"))
    finally:
        await engine.dispose()


def test_legal_tables_and_trigger() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase18_001")
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase19_002")
        assert asyncio.run(_tables(url)) == {"legal_documents", "legal_acceptances"}
        asyncio.run(_exercise(url))
        _alembic(url, "phase18_001", down=True)
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase19_002")
        assert asyncio.run(_tables(url)) == {"legal_documents", "legal_acceptances"}


async def _acceptance_links(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            plan = (await c.execute(text("SELECT id FROM plans WHERE is_default"))).scalar_one()
            user = (
                await c.execute(
                    text(
                        "INSERT INTO users (email, name) VALUES ('asha@shree.example', 'Asha') "
                        "RETURNING id"
                    )
                )
            ).scalar_one()
            tenant = (
                await c.execute(
                    text(
                        "INSERT INTO tenants (name, plan_id, api_key_hash) "
                        "VALUES ('Shree Labs', :p, '') RETURNING id"
                    ),
                    {"p": plan},
                )
            ).scalar_one()
            doc = (
                await c.execute(
                    text(
                        "INSERT INTO legal_documents (slug, version, title, body_md, published_at) "
                        "VALUES ('terms', 1, 'Terms', 'v1', now()) RETURNING id"
                    )
                )
            ).scalar_one()
            await c.execute(
                text(
                    "INSERT INTO legal_acceptances (user_id, email, tenant_id, document_id, slug, "
                    "version) VALUES (:u, 'asha@shree.example', :t, :d, 'terms', 1)"
                ),
                {"u": user, "t": tenant, "d": doc},
            )
        # a page that someone accepted cannot be deleted (and a published one never can)
        with pytest.raises(DBAPIError):
            async with engine.begin() as c:
                await c.execute(text("DELETE FROM legal_documents WHERE id = :d"), {"d": doc})
        # the person and the workspace can go: the record stays, with the e-mail
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
            await c.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant})
        async with engine.begin() as c:
            row = (
                await c.execute(text("SELECT user_id, tenant_id, email FROM legal_acceptances"))
            ).one()
        assert row.user_id is None and row.tenant_id is None
        assert row.email == "asha@shree.example"
    finally:
        await engine.dispose()


def test_acceptances_outlive_the_user_and_the_workspace() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_002")
        asyncio.run(_acceptance_links(url))
