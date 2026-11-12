"""phase15_002 on PostgreSQL (BIZ4). Needs Docker (skipped with the integration suite when down).

- the migration round-trips;
- deleting a widget key cascades its sightings;
- deleting the knowledge base sets the wizard's `kb_id` NULL and cascades sightings and assets;
- deleting the workspace cascades its onboarding row and assets.

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

TABLES = {"onboarding_states", "widget_origin_seen", "public_assets"}


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


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_001")
        assert _tables(url) == set()
        _alembic(url, "phase15_002")
        assert _tables(url) == TABLES
        _alembic(url, "phase15_001", down=True)
        assert _tables(url) == set()
        _alembic(url, "phase15_002")
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
    key = _sql(
        url,
        "INSERT INTO widget_keys (corpus_id, name, public_key, allowed_origins) "
        "VALUES (:c, 'Website', 'pk_test_one', '[\"https://shreedental.in\"]'::jsonb) RETURNING id",
        c=corpus,
    )[0][0]
    _sql(
        url,
        "INSERT INTO onboarding_states (tenant_id, kb_id, widget_key_id, started_by, website) "
        "VALUES (:t, :c, :k, 'owner@shreedental.in', 'https://shreedental.in')",
        t=tenant,
        c=corpus,
        k=key,
    )
    _sql(
        url,
        "INSERT INTO widget_origin_seen (widget_key_id, origin, corpus_id, allowed) "
        "VALUES (:k, 'https://shreedental.in', :c, true)",
        k=key,
        c=corpus,
    )
    _sql(
        url,
        "INSERT INTO public_assets (id, tenant_id, corpus_id, kind, content_type, bytes, sha256, "
        "storage_key, created_by) VALUES (gen_random_uuid(), :t, :c, 'avatar', 'image/png', 10, "
        "'ab', 'corpus/x/avatars/y.png', 'owner@shreedental.in')",
        t=tenant,
        c=corpus,
    )
    return tenant, corpus, key


def test_checks_reject_bad_values() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        tenant, _corpus, _key = _chain(url)
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE onboarding_states SET step = 4")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE onboarding_states SET status = 'bogus'")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE public_assets SET kind = 'logo' WHERE tenant_id = :t", t=tenant)


def test_deleting_a_widget_key_cascades_its_sightings() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        _tenant, _corpus, key = _chain(url)
        _sql(url, "DELETE FROM widget_keys WHERE id = :k", k=key)
        assert _sql(url, "SELECT 1 FROM widget_origin_seen") == []
        assert _sql(url, "SELECT widget_key_id FROM onboarding_states")[0][0] is None


def test_deleting_the_knowledge_base_clears_the_pointer_and_cascades() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        _tenant, corpus, _key = _chain(url)
        _sql(url, "DELETE FROM corpora WHERE id = :c", c=corpus)
        assert _sql(url, "SELECT kb_id FROM onboarding_states")[0][0] is None
        assert _sql(url, "SELECT 1 FROM widget_origin_seen") == []
        assert _sql(url, "SELECT 1 FROM public_assets") == []


def test_deleting_the_workspace_cascades_everything() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        tenant, corpus, _key = _chain(url)
        _sql(url, "DELETE FROM corpora WHERE id = :c", c=corpus)
        _sql(url, "DELETE FROM tenants WHERE id = :t", t=tenant)
        for table in sorted(TABLES):
            assert _sql(url, f"SELECT 1 FROM {table}") == [], table


def test_a_refused_origin_that_becomes_allowed_flips_within_the_hour() -> None:
    """The www mismatch fix: refused, then the key allows it, then a reload inside the hour."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.onboarding.service import record_sighting

    www = "https://www.shreedental.in"
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase15_002")
        _tenant, corpus, key = _chain(url)

        async def go() -> tuple[bool, bool, bool, bool, list[Any]]:
            engine = create_async_engine(url)
            try:
                async with AsyncSession(engine) as s:
                    refused = await record_sighting(
                        s, key_id=key, corpus_id=corpus, origin=www, allowed=False
                    )
                    again = await record_sighting(
                        s, key_id=key, corpus_id=corpus, origin=www, allowed=False
                    )
                    flipped = await record_sighting(
                        s, key_id=key, corpus_id=corpus, origin=www, allowed=True
                    )
                    repeat = await record_sighting(
                        s, key_id=key, corpus_id=corpus, origin=www, allowed=True
                    )
                    rows = list(
                        (
                            await s.execute(
                                text(
                                    "SELECT allowed, first_seen_at = last_seen_at FROM "
                                    "widget_origin_seen WHERE origin = :o"
                                ),
                                {"o": www},
                            )
                        ).all()
                    )
                return refused, again, flipped, repeat, rows
            finally:
                await engine.dispose()

        refused, again, flipped, repeat, rows = asyncio.run(go())
    assert refused is True  # a new row
    assert again is False  # inside the hour, still refused: nothing to do
    assert flipped is True  # now allowed: counts as the first allowed load
    assert repeat is False  # a plain repeat is not
    assert [tuple(r) for r in rows] == [(True, True)]
