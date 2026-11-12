"""phase17_003 on PostgreSQL (TV6 schema). Needs Docker (skipped with the integration suite).

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


def _connection(url: str, tenant: uuid.UUID, kb: uuid.UUID, number: str) -> uuid.UUID:
    return _sql(
        url,
        "INSERT INTO channel_connections (corpus_id, tenant_id, phone_number_id, waba_id, "
        "display_phone, access_token_encrypted, token_hint, setup, meta_billing, created_by) "
        "VALUES (:c, :t, :p, '5550001', '+91 98220 12345', 'gAAAA', 'x1y2', 'manual', "
        "'deployer', 'ops@x.in') RETURNING id",
        c=kb,
        t=tenant,
        p=number,
    )[0][0]


def test_a_number_belongs_to_one_knowledge_base_and_a_kb_has_one_connection() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_003")
        assert _sql(url, "SELECT channels FROM plans WHERE is_default") == [({},)]
        tenant = _tenant(url)
        kb1, kb2 = _kb(url, tenant), _kb(url, tenant)
        _connection(url, tenant, kb1, "109876543210")
        with pytest.raises(IntegrityError):  # the same number on another knowledge base
            _connection(url, tenant, kb2, "109876543210")
        with pytest.raises(IntegrityError):  # a second connection for the same knowledge base
            _connection(url, tenant, kb1, "109876543211")
        _connection(url, tenant, kb2, "109876543211")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE channel_connections SET status = 'broken'")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE channel_connections SET meta_billing = 'nobody'")


def test_a_chat_is_unique_per_contact_and_events_dedupe() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_003")
        tenant = _tenant(url)
        kb = _kb(url, tenant)
        conn = _connection(url, tenant, kb, "109876543210")
        thread = (
            "INSERT INTO channel_threads (connection_id, corpus_id, tenant_id, contact_hash, "
            "contact_encrypted) VALUES (:n, :c, :t, :h, 'gAAAA') RETURNING id"
        )
        first = _sql(url, thread, n=conn, c=kb, t=tenant, h="a" * 64)[0][0]
        with pytest.raises(IntegrityError):
            _sql(url, thread, n=conn, c=kb, t=tenant, h="a" * 64)
        _sql(url, thread, n=conn, c=kb, t=tenant, h="b" * 64)
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE channel_threads SET paused_by = 'robot' WHERE id = :i", i=first)

        event = (
            "INSERT INTO channel_events (connection_id, corpus_id, external_id, kind, "
            "payload_encrypted) VALUES (:n, :c, :e, 'message', 'gAAAA')"
        )
        _sql(url, event, n=conn, c=kb, e="wamid.IN1")
        with pytest.raises(IntegrityError):  # Meta retried the same message
            _sql(url, event, n=conn, c=kb, e="wamid.IN1")
        _sql(url, event, n=conn, c=kb, e="wamid.IN2")
        with pytest.raises(IntegrityError):
            _sql(url, "UPDATE channel_events SET status = 'lost'")


def test_deleting_a_knowledge_base_removes_its_number_chats_and_events() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_003")
        tenant = _tenant(url)
        kb = _kb(url, tenant)
        conn = _connection(url, tenant, kb, "109876543210")
        _sql(
            url,
            "INSERT INTO channel_threads (connection_id, corpus_id, tenant_id, contact_hash, "
            "contact_encrypted) VALUES (:n, :c, :t, 'a', 'x')",
            n=conn,
            c=kb,
            t=tenant,
        )
        _sql(url, "DELETE FROM corpora WHERE id = :c", c=kb)
        for table in ("channel_connections", "channel_threads", "channel_events"):
            assert _sql(url, f"SELECT count(*) FROM {table}") == [(0,)]


def test_downgrade_then_upgrade_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase17_003")
        _alembic(url, "phase17_002", down=True)
        tables = {
            r[0]
            for r in _sql(
                url,
                "SELECT table_name FROM information_schema.tables WHERE table_name IN "
                "('channel_connections','channel_threads','channel_events')",
            )
        }
        assert tables == set()
        assert _sql(
            url,
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'plans' AND column_name = 'channels'",
        ) == [(0,)]
        _alembic(url, "phase17_003")
        assert _sql(url, "SELECT count(*) FROM channel_connections") == [(0,)]
