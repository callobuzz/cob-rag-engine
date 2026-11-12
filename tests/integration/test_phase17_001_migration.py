"""phase17_001 on PostgreSQL (TV2/TV3/TV4 schema). Needs Docker (skipped with the integration suite).

Sync tests (alembic's env.py calls asyncio.run()).
"""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration


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


def _tenant(url: str) -> uuid.UUID:
    return _sql(
        url,
        "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
        "SELECT 'Shree Dental Clinic', '', 'active', id FROM plans WHERE is_default RETURNING id",
    )[0][0]


def _kb(url: str, tenant: uuid.UUID) -> uuid.UUID:
    return _sql(
        url, "INSERT INTO corpora (name, tenant_id) VALUES ('KB', :t) RETURNING id", t=tenant
    )[0][0]


def _conversation(url: str, corpus: uuid.UUID) -> uuid.UUID:
    return _sql(url, "INSERT INTO conversations (corpus_id) VALUES (:c) RETURNING id", c=corpus)[0][
        0
    ]


def test_ref_code_is_unique_per_knowledge_base_and_money_checks() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_001")
        tenant = _tenant(url)
        kb1, kb2 = _kb(url, tenant), _kb(url, tenant)
        c1, c2, c3 = _conversation(url, kb1), _conversation(url, kb1), _conversation(url, kb2)
        update = "UPDATE conversations SET ref_code = 'SD-4F7K' WHERE id = :i"
        _sql(url, update, i=c1)
        _sql(url, update, i=c3)  # another knowledge base: fine
        with pytest.raises(IntegrityError):
            _sql(url, update, i=c2)
        # many conversations without a reference are fine (partial index)
        assert _sql(url, "SELECT count(*) FROM conversations WHERE ref_code IS NULL") == [(1,)]

        assert _sql(url, "SELECT count(*) FROM plan_pricing WHERE effective_at IS NOT NULL") == [
            (0,)
        ]

        _sql(
            url,
            "INSERT INTO billing_accounts (tenant_id, currency) VALUES (:t, 'INR')",
            t=tenant,
        )
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE billing_accounts SET spend_cap_minor = -1")
        _sql(url, "UPDATE billing_accounts SET spend_cap_minor = 50000")

        insert = (
            "INSERT INTO value_settings (tenant_id, currency, minutes_per_answer) "
            "VALUES (:t, 'INR', :m)"
        )
        with pytest.raises(IntegrityError):
            _sql(url, insert, t=tenant, m=121)
        _sql(url, insert, t=tenant, m=3)


def test_notices_unique_and_support_numbers_from_the_sequence() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_001")
        tenant = _tenant(url)
        notice = "INSERT INTO billing_notices (tenant_id, kind, key) VALUES (:t, 'renewal', :k)"
        _sql(url, notice, t=tenant, k="2026-11-05")
        with pytest.raises(IntegrityError):
            _sql(url, notice, t=tenant, k="2026-11-05")
        _sql(url, notice, t=tenant, k="2026-12-05")

        insert = (
            "INSERT INTO support_requests (tenant_id, subject, created_by_email) "
            "VALUES (:t, 'Widget not showing', 'owner@shreedental.in') RETURNING number"
        )
        first = _sql(url, insert, t=tenant)[0][0]
        second = _sql(url, insert, t=tenant)[0][0]
        assert second == first + 1
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE support_requests SET status = 'broken'")


def test_downgrade_then_upgrade_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_001")
        _alembic(url, "phase16_002", down=True)
        tables = {
            r[0]
            for r in _sql(
                url,
                "SELECT table_name FROM information_schema.tables WHERE table_name IN "
                "('value_settings','value_reports','billing_notices','support_requests',"
                "'support_messages')",
            )
        }
        assert tables == set()
        assert _sql(
            url,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'assistant_turns' AND column_name = 'office_open'",
        ) == [(0,)]
        _alembic(url, "phase17_001")
        assert _sql(url, "SELECT count(*) FROM value_settings") == [(0,)]
