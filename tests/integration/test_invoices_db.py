"""P2 on a real PostgreSQL: the frozen tax documents, gap-free numbering. Requires Docker."""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

_INSERT = (
    "INSERT INTO tax_documents (doc_type, number, fy, issued_at, tenant_id, account_id,"
    " payment_id, kind, place_of_supply, currency, taxable_minor, cgst_minor, sgst_minor,"
    " igst_minor, total_minor, seller, buyer, lines) VALUES ('invoice', :number, '26-27', now(),"
    " :tenant, gen_random_uuid(), gen_random_uuid(), 'intra', '27', 'INR', 100000, 9000, 9000,"
    " 0, 118000, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb) RETURNING id"
)


def _run(url: str, *statements: tuple[str, dict[str, Any]]) -> list[Any]:
    async def go() -> list[Any]:
        engine = create_async_engine(url)
        try:
            out: list[Any] = []
            async with engine.begin() as conn:
                for sql, params in statements:
                    result = await conn.execute(text(sql), params)
                    out = list(result.all()) if result.returns_rows else []
            return out
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_document_is_frozen_and_survives_its_tenant() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_003")

        tenant = _run(
            url,
            (
                "INSERT INTO tenants (name, plan_id) SELECT 't', id FROM plans LIMIT 1 RETURNING id",
                {},
            ),
        )[0][0]
        doc = _run(url, (_INSERT, {"number": "INV/26-27/00001", "tenant": tenant}))[0][0]

        with pytest.raises(DBAPIError):
            _run(url, ("UPDATE tax_documents SET total_minor = 1 WHERE id = :i", {"i": doc}))
        with pytest.raises(DBAPIError):
            _run(url, ("DELETE FROM tax_documents WHERE id = :i", {"i": doc}))

        # The PDF reference and the mail stamp may be set once, from NULL.
        _run(
            url,
            (
                "UPDATE tax_documents SET pdf_key = 'k', pdf_sha256 = 'abc' WHERE id = :i",
                {"i": doc},
            ),
        )
        with pytest.raises(DBAPIError):
            _run(url, ("UPDATE tax_documents SET pdf_key = 'other' WHERE id = :i", {"i": doc}))
        _run(url, ("UPDATE tax_documents SET emailed_at = now() WHERE id = :i", {"i": doc}))
        with pytest.raises(DBAPIError):
            _run(url, ("UPDATE tax_documents SET emailed_at = now() WHERE id = :i", {"i": doc}))

        # Deleting the workspace leaves the document (no foreign key).
        _run(url, ("DELETE FROM tenants WHERE id = :t", {"t": tenant}))
        assert _run(url, ("SELECT count(*) FROM tax_documents", {}))[0][0] == 1

        # Number unique per series; a credit note needs its links and a negative total.
        with pytest.raises(IntegrityError):
            _run(url, (_INSERT, {"number": "INV/26-27/00001", "tenant": tenant}))
        with pytest.raises(IntegrityError):
            _run(
                url,
                (
                    _INSERT.replace("'invoice'", "'credit_note'"),
                    {"number": "CN/26-27/00001", "tenant": tenant},
                ),
            )


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase19_003")
        names = {r[0] for r in _run(url, ("SELECT tablename FROM pg_tables", {}))}
        assert {"tax_documents", "tax_document_counters"} <= names
        _alembic(url, "phase19_002", down=True)
        names = {r[0] for r in _run(url, ("SELECT tablename FROM pg_tables", {}))}
        assert not {"tax_documents", "tax_document_counters"} & names
        cols = {
            r[0]
            for r in _run(
                url,
                (
                    "SELECT column_name FROM information_schema.columns"
                    " WHERE table_name = 'payment_refunds'",
                    {},
                ),
            )
        }
        assert "credit_note_skipped" not in cols
