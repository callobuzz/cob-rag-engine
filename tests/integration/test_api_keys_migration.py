"""phase11_012 on a real PostgreSQL: each existing single key becomes its owner's "Default key".

Same hash, `full` permission, no lookup segment, the old columns blanked; an owner with no key
gets no row; the id segment of a knowledge-base key is the one in its collection name. The
new table accepts several keys per owner but one default, refuses a row with two owners or
none, and cascades with the owner. Downgrade puts each default key's hash back.

A sync test: alembic's env.py calls asyncio.run(). Requires Docker.
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

TK_HASH = "$2b$12$tenantkeyhashtenantkeyhashtenantkeyhashtenantkeyhash"
CK_HASH = "$2b$12$corpuskeyhashcorpuskeyhashcorpuskeyhashcorpuskeyhash"


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


def _seed(url: str) -> tuple[Any, Any, Any]:
    """Shree (with a key) and Pune (none, a sign-up's tenant); Shree's FAQ knowledge base with
    a key and a second knowledge base without one. Returns (shree id, pune id, faq id)."""
    shree = _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT 'Shree Labs', :h, 'active', id FROM plans WHERE is_default RETURNING id",
        h=TK_HASH,
    )[0][0]
    pune = _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT 'Pune Agency', '', 'active', id FROM plans WHERE is_default RETURNING id",
    )[0][0]
    faq = _sql(
        url,
        "INSERT INTO corpora (name, api_key_hash, qdrant_collection, tenant_id) "
        "VALUES ('Support FAQ', :h, 'corpus_abcd1234', :t) RETURNING id",
        h=CK_HASH,
        t=shree,
    )[0][0]
    _sql(
        url,
        "INSERT INTO corpora (name, api_key_hash, qdrant_collection, tenant_id) "
        "VALUES ('Empty', '', 'corpus_eeee0000', :t)",
        t=shree,
    )
    return shree, pune, faq


def test_existing_keys_become_default_keys_and_back() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase11_011")
        shree, pune, faq = _seed(url)

        _alembic(url, "phase11_012")
        rows = _sql(
            url,
            "SELECT corpus_id, tenant_id, id_short, lookup, name, key_prefix, key_hash, "
            "permission, is_default, created_by FROM api_keys ORDER BY name, key_prefix",
        )
        assert len(rows) == 2  # Pune and the empty knowledge base had none
        by_owner = {(r[0] or r[1]): r for r in rows}
        tk = by_owner[shree]
        assert tk[0] is None and tk[2] == str(shree)[:8] and tk[3] is None
        assert (tk[4], tk[6], tk[7], tk[8], tk[9]) == (
            "Default key",
            TK_HASH,
            "full",
            True,
            "migrated",
        )
        assert tk[5] == f"tk_{str(shree)[:8]}_..."
        ck = by_owner[faq]
        assert ck[1] is None and ck[2] == "abcd1234" and ck[3] is None  # from the collection name
        assert (ck[4], ck[6], ck[7], ck[8]) == ("Default key", CK_HASH, "full", True)
        assert ck[5] == "ck_abcd1234_..."
        assert pune not in by_owner

        # One source of truth: the old columns are blank.
        assert _sql(url, "SELECT count(*) FROM tenants WHERE api_key_hash <> ''") == [(0,)]
        assert _sql(url, "SELECT count(*) FROM corpora WHERE api_key_hash <> ''") == [(0,)]

        _alembic(url, "phase11_011", down=True)
        assert _sql(url, "SELECT api_key_hash FROM tenants WHERE id = :i", i=shree) == [(TK_HASH,)]
        assert _sql(url, "SELECT api_key_hash FROM corpora WHERE id = :i", i=faq) == [(CK_HASH,)]
        assert _sql(url, "SELECT api_key_hash FROM tenants WHERE id = :i", i=pune) == [("",)]
        assert _sql(url, "SELECT to_regclass('api_keys')") == [(None,)]

        _alembic(url, "head")
        assert _sql(url, "SELECT count(*) FROM api_keys") == [(2,)]


def test_the_table_enforces_its_rules() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        shree, _, faq = _seed(url)

        def insert(owner: str, name: str, **extra: Any) -> None:
            columns = {"id_short": "abcd1234", "name": name, "key_prefix": "x", "key_hash": "h"}
            columns.update(extra)
            cols = ", ".join([owner, *columns])
            vals = ", ".join([":owner", *(f":{c}" for c in columns)])
            _sql(url, f"INSERT INTO api_keys ({cols}) VALUES ({vals})", owner=faq, **columns)

        insert("corpus_id", "Agent one")
        insert("corpus_id", "Agent two")  # several keys per owner
        insert("corpus_id", "Default", is_default=True)
        with pytest.raises(IntegrityError, match="uq_api_keys_default_corpus"):
            insert("corpus_id", "Second default", is_default=True)
        with pytest.raises(IntegrityError, match="ck_api_keys_permission"):
            insert("corpus_id", "Odd", permission="admin")
        with pytest.raises(IntegrityError, match="ck_api_keys_one_owner"):
            _sql(
                url,
                "INSERT INTO api_keys (corpus_id, tenant_id, id_short, name, key_prefix, key_hash) "
                "VALUES (:c, :t, 'abcd1234', 'Both', 'x', 'h')",
                c=faq,
                t=shree,
            )
        with pytest.raises(IntegrityError, match="ck_api_keys_one_owner"):
            _sql(
                url,
                "INSERT INTO api_keys (id_short, name, key_prefix, key_hash) "
                "VALUES ('abcd1234', 'Neither', 'x', 'h')",
            )
        assert _sql(url, "SELECT permission FROM api_keys WHERE name = 'Agent one'") == [("full",)]

        _sql(url, "DELETE FROM corpora WHERE id = :i", i=faq)
        assert _sql(url, "SELECT count(*) FROM api_keys WHERE corpus_id = :i", i=faq) == [(0,)]
