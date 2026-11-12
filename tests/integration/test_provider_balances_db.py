"""phase14_001: provider_balance_settings, its partial unique index and checks. Needs Docker.

A sync test: alembic's env.py calls asyncio.run().
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
    async def go() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as c:
                return await work(c)
        finally:
            await engine.dispose()

    return asyncio.run(go())


async def _insert(c: AsyncConnection, provider: str, label: str, **extra: Any) -> None:
    cols = {"id": uuid.uuid4(), "provider": provider, "label": label, **extra}
    names = ", ".join(cols)
    values = ", ".join(f":{k}" for k in cols)
    await c.execute(
        text(f"INSERT INTO provider_balance_settings ({names}) VALUES ({values})"), cols
    )


async def _table_exists(c: AsyncConnection) -> bool:
    row = await c.execute(text("SELECT to_regclass('provider_balance_settings')"))
    return row.scalar() is not None


def _violation(url: str, work: Callable[[AsyncConnection], Awaitable[Any]]) -> None:
    with pytest.raises(IntegrityError):
        _run(url, work)


def test_round_trip_unique_and_checks() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase14_001")
        assert _run(url, _table_exists) is True

        async def two_openai(c: AsyncConnection) -> None:
            await _insert(c, "openai", "OpenAI")
            await _insert(c, "openai", "OpenAI again")

        _violation(url, two_openai)

        async def two_manual(c: AsyncConnection) -> None:
            await _insert(c, "manual", "Groq")
            await _insert(c, "manual", "Gemini")

        _run(url, two_manual)

        async def balance_without_date(c: AsyncConnection) -> None:
            await _insert(c, "manual", "Mistral", manual_balance_usd_micro=5_000_000)

        _violation(url, balance_without_date)

        async def bad_state(c: AsyncConnection) -> None:
            await _insert(c, "anthropic", "Anthropic", alert_state="loud")

        _violation(url, bad_state)

        _alembic(url, "phase13_003", down=True)
        assert _run(url, _table_exists) is False
