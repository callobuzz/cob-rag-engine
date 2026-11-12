"""P2 T2 on a real PostgreSQL: gap-free financial-year numbers. Requires Docker."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration


def test_numbers_are_gap_free_under_concurrency() -> None:
    from callobuzz_rag.billing.invoicing.numbering import next_number

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_003")

        async def go() -> list[str | None]:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)

            async def one(rollback: bool) -> str | None:
                async with factory() as s:
                    _, number = await next_number(s, series="invoice", prefix="INV", fy="26-27")
                    if rollback:
                        await s.rollback()
                        return None
                    await s.commit()
                    return number

            try:
                return list(await asyncio.gather(*(one(i % 5 == 0) for i in range(30))))
            finally:
                await engine.dispose()

        results = asyncio.run(go())
        kept = sorted(n for n in results if n)
        assert kept == [f"INV/26-27/{i:05d}" for i in range(1, len(kept) + 1)]


def test_new_year_and_new_prefix_start_their_own_counters() -> None:
    from callobuzz_rag.billing.invoicing.numbering import next_number

    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_003")

        async def go() -> list[str]:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            out: list[str] = []
            try:
                for series, prefix, fy in (
                    ("invoice", "INV", "26-27"),
                    ("invoice", "INV", "26-27"),
                    ("invoice", "INV", "27-28"),
                    ("invoice", "CB", "27-28"),
                    ("credit_note", "CN", "26-27"),
                ):
                    async with factory() as s:
                        out.append((await next_number(s, series=series, prefix=prefix, fy=fy))[1])
                        await s.commit()
            finally:
                await engine.dispose()
            return out

        assert asyncio.run(go()) == [
            "INV/26-27/00001",
            "INV/26-27/00002",
            "INV/27-28/00001",
            "CB/27-28/00001",
            "CN/26-27/00001",
        ]
