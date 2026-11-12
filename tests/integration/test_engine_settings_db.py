"""phase11_002 (accounts A7): engine_settings and audit_log on a real PostgreSQL.

The migration seeds row 1; a second row violates the single-row check; a settings
PATCH round-trips through JSONB and writes its audit row; downgrade drops both tables.
A sync test: alembic's env.py calls asyncio.run(). Requires Docker.
"""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
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


def _j(value: Any) -> Any:
    """JSONB through a raw text() query may come back as its JSON text."""
    return json.loads(value) if isinstance(value, str) else value


def test_migration_seeds_one_row_enforces_it_and_round_trips() -> None:
    from callobuzz_rag.accounts.audit import record_audit
    from callobuzz_rag.accounts.settings_store import (
        get_engine_settings,
        invalidate_engine_settings_cache,
        update_engine_settings,
    )

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_002")

        async def rows(c: AsyncConnection) -> list[Any]:
            result = await c.execute(text("SELECT id, value FROM engine_settings"))
            return [(row_id, _j(value)) for row_id, value in result.all()]

        assert _run(url, rows) == [(1, {})]

        async def second(c: AsyncConnection) -> None:
            await c.execute(text("INSERT INTO engine_settings (id, value) VALUES (2, '{}')"))

        with pytest.raises(IntegrityError, match="ck_engine_settings_single_row"):
            _run(url, second)

        _alembic(url, "head")  # the ORM model has every later column too

        async def round_trip() -> tuple[Any, Any, list[Any]]:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                invalidate_engine_settings_cache()
                async with factory() as session:
                    _, changed = await update_engine_settings(
                        session,
                        {"signup_mode": "open", "branding": {"product_name": "Sahayak AI"}},
                        actor_label="admin key",
                    )
                    await record_audit(
                        session,
                        None,
                        "settings.updated",
                        target_type="settings",
                        details={"changed": changed, "token": "never stored"},
                    )
                    await session.commit()
                invalidate_engine_settings_cache()
                settings = await get_engine_settings(factory)
                async with engine.connect() as c:
                    stored = (
                        await c.execute(text("SELECT value, updated_by FROM engine_settings"))
                    ).one()
                    audit = (
                        await c.execute(text("SELECT action, actor_kind, details FROM audit_log"))
                    ).all()
                return settings, stored, list(audit)
            finally:
                invalidate_engine_settings_cache()
                await engine.dispose()

        settings, stored, audit = asyncio.run(round_trip())
        assert settings.signup_mode == "open"
        assert settings.branding.product_name == "Sahayak AI"
        assert _j(stored.value)["branding"]["product_name"] == "Sahayak AI"
        assert stored.updated_by == "admin key"
        ((action, kind, details),) = audit
        details = _j(details)
        assert (action, kind) == ("settings.updated", "system")
        assert details["changed"]["signup_mode"] == {"from": "approval", "to": "open"}
        assert "token" not in details

        async def orphan_ids(c: AsyncConnection) -> None:
            # No FKs: an audit row may name a tenant and user that do not exist.
            await c.execute(
                text(
                    "INSERT INTO audit_log (actor_kind, actor_label, action, tenant_id, "
                    "actor_user_id) VALUES ('user', 'x@y.example', 'tenant.deleted', :t, :u)"
                ),
                {"t": uuid.uuid4(), "u": uuid.uuid4()},
            )

        _run(url, orphan_ids)

        _alembic(url, "phase11_001", down=True)

        async def tables(c: AsyncConnection) -> set[str]:
            names = await c.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE tablename IN ('engine_settings', 'audit_log')"
                )
            )
            return set(names.scalars().all())

        assert _run(url, tables) == set()
