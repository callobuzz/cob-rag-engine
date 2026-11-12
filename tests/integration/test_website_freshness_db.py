"""The freshness scheduler on PostgreSQL (BIZ2). Needs Docker (skipped with the rest of the
integration suite when it is down).

Two beat ticks at the same moment must hand each due page out once (`FOR UPDATE SKIP LOCKED`
plus the committed `next_check_at` move), a stale run is closed and a site that never ran is
started. Sync test (alembic's env.py calls asyncio.run()).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.core.config import Settings
from callobuzz_rag.website.check import schedule_checks
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic
from tests.integration.test_website_learning_db import _chain, _sql

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def test_two_ticks_hand_each_due_page_out_once() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")  # the scheduler runs on today's models, so today's schema
        corpus, source, _tenant = _chain(url)
        _sql(url, "DELETE FROM website_pages")
        _sql(url, "UPDATE website_sources SET status = 'ready'")
        for i in range(30):
            _sql(
                url,
                "INSERT INTO website_pages (source_id, corpus_id, url, url_hash, status, "
                "interval_seconds, next_check_at) VALUES (:s, :c, :u, :h, 'learned', 86400, :n)",
                s=source,
                c=corpus,
                u=f"https://shreedental.in/p{i}",
                h=f"{i:064d}",
                n=NOW - timedelta(minutes=1),
            )
        _sql(
            url,
            "INSERT INTO website_runs (source_id, corpus_id, kind, trigger, heartbeat_at) "
            "VALUES (:s, :c, 'check', 'schedule', :h)",
            s=source,
            c=corpus,
            h=NOW - timedelta(minutes=16),
        )

        sent: list[str] = []

        async def go() -> None:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            settings = Settings(_env_file=None)  # type: ignore[call-arg]
            try:
                await asyncio.gather(
                    *(
                        schedule_checks(
                            factory,
                            settings,
                            dispatch_check=lambda s, ids, t: sent.extend(ids),
                            dispatch_learn=lambda s, k, t: None,
                            now=NOW,
                        )
                        for _ in range(2)
                    )
                )
            finally:
                await engine.dispose()

        asyncio.run(go())
        assert len(sent) == 30 and len(set(sent)) == 30
        due = _sql(url, "SELECT count(*) FROM website_pages WHERE next_check_at <= :n", n=NOW)
        assert due[0][0] == 0  # every page was moved forward before it was handed out
        run = _sql(url, "SELECT status, error_code FROM website_runs")[0]
        assert tuple(run) == ("failed", "stalled")
