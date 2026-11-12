"""phase15_001 on PostgreSQL (BIZ1). Needs Docker (skipped with the rest of the integration suite
when it is down).

- the migration round-trips, existing plans read the new defaults;
- a second open `first`/`full` run for one site violates the partial unique index, a finished one
  does not, a `check` run is not limited;
- deleting the knowledge base cascades every website row and its articles.

Sync tests (alembic's env.py calls asyncio.run()).
"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

TABLES = {
    "website_settings",
    "website_sources",
    "website_pages",
    "website_runs",
    "knowledge_articles",
}


def _sql(url: str, statement: str, **params: Any) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(go())


def _tables(url: str) -> set[str]:
    rows = _sql(
        url,
        "SELECT table_name FROM information_schema.tables WHERE table_name = ANY(:names)",
        names=sorted(TABLES),
    )
    return {r[0] for r in rows}


def test_migration_round_trip_and_plan_defaults() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase14_005")
        assert _tables(url) == set()
        _alembic(url, "phase15_001")
        assert _tables(url) == TABLES
        row = _sql(
            url,
            "SELECT website_learning_enabled, website_pages_max, dashboard_features FROM plans "
            "WHERE is_default",
        )[0]
        assert tuple(row) == (True, None, {})
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE plans SET website_pages_max = 0")
        _alembic(url, "phase14_005", down=True)
        assert _tables(url) == set()
        _alembic(url, "phase15_001")
        assert _tables(url) == TABLES


def _chain(url: str) -> tuple[Any, Any, Any]:
    tenant = _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT 'Shree Dental Clinic', '', 'active', id FROM plans WHERE is_default RETURNING id",
    )[0][0]
    corpus = _sql(
        url,
        "INSERT INTO corpora (name, api_key_hash, qdrant_collection, tenant_id) "
        "VALUES ('Shree Dental', '', 'corpus_5d5d5d5d', :t) RETURNING id",
        t=tenant,
    )[0][0]
    _sql(
        url,
        "INSERT INTO website_settings (corpus_id, tenant_id) VALUES (:c, :t)",
        c=corpus,
        t=tenant,
    )
    source = _sql(
        url,
        "INSERT INTO website_sources (corpus_id, tenant_id, origin, scope) "
        "VALUES (:c, :t, 'https://shreedental.in', 'shreedental.in') RETURNING id",
        c=corpus,
        t=tenant,
    )[0][0]
    _sql(
        url,
        "INSERT INTO website_pages (source_id, corpus_id, url, url_hash) "
        "VALUES (:s, :c, 'https://shreedental.in/pricing', 'abc')",
        s=source,
        c=corpus,
    )
    _sql(
        url,
        "INSERT INTO knowledge_articles (corpus_id, tenant_id, external_id, kind, title, body, "
        "created_by) VALUES (:c, :t, 'faq:1', 'faq', 'Timings', 'Mon-Sat 10-7', 'a@b.in')",
        c=corpus,
        t=tenant,
    )
    return corpus, source, tenant


def test_one_open_discovery_run_per_site_and_cascade() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_001")
        corpus, source, _tenant = _chain(url)
        insert = (
            "INSERT INTO website_runs (source_id, corpus_id, kind, trigger) "
            "VALUES (:s, :c, :k, 'manual')"
        )
        _sql(url, insert, s=source, c=corpus, k="first")
        with pytest.raises(IntegrityError):
            _sql(url, insert, s=source, c=corpus, k="full")
        _sql(url, insert, s=source, c=corpus, k="check")  # not a discovery run
        _sql(url, insert, s=source, c=corpus, k="check")
        _sql(url, "UPDATE website_runs SET finished_at = now() WHERE kind = 'first'")
        _sql(url, insert, s=source, c=corpus, k="full")  # the first one is finished

        with pytest.raises(IntegrityError):  # one source per knowledge base and site
            _sql(
                url,
                "INSERT INTO website_sources (corpus_id, tenant_id, origin, scope) "
                "SELECT corpus_id, tenant_id, origin, scope FROM website_sources",
            )

        _sql(url, "DELETE FROM corpora WHERE id = :c", c=corpus)
        for table in sorted(TABLES):
            assert _sql(url, f"SELECT 1 FROM {table}") == [], table
