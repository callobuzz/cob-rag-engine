"""phase14_004 and the add-on grants on PostgreSQL (BIL5). Needs Docker.

- the migration round-trips, and the database refuses an add-on or a grant whose fields do not
  fit its kind;
- a tenant at `max_documents` gets one more record ingest by a grant (under the advisory lock),
  the worker backstop agrees, and a revoke refuses the next one;
- question packs count for the month they were granted in, an account override replaces the
  plan's limit with the packs on top;
- five concurrent clicks with one idempotency key make one grant and one ledger entry.

Sync tests (alembic's env.py calls asyncio.run()); the async flow runs in its own loop.
"""

import asyncio
import os
import uuid
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

ADMIN = "admin-key-integration"


async def _tables(url: str) -> set[str]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_name IN ('add_on_catalogue', 'add_on_grants')"
                )
            )
            return {r.table_name for r in rows}
    finally:
        await engine.dispose()


def test_migration_round_trip() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "phase14_003")
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase14_004")
        assert asyncio.run(_tables(url)) == {"add_on_catalogue", "add_on_grants"}
        _alembic(url, "phase14_003", down=True)
        assert asyncio.run(_tables(url)) == set()
        _alembic(url, "phase14_004")
        assert asyncio.run(_tables(url)) == {"add_on_catalogue", "add_on_grants"}


async def _sql(factory: Any, sql: str, **params: Any) -> list[Any]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        rows = list(result.all()) if result.returns_rows else []
        await session.commit()
        return rows


async def _constraints(url: str) -> dict[str, str]:
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    out: dict[str, str] = {}
    insert = (
        "INSERT INTO add_on_catalogue (kind, name, currency, price_minor, credit_minor, "
        "limit_field, limit_delta, questions, duration) VALUES "
        "(:kind, :name, 'INR', 100, :credit, :field, :delta, :questions, :duration)"
    )
    cases = {
        "credit_without_credit": dict(kind="credit_pack", credit=None),
        "credit_with_questions": dict(kind="credit_pack", credit=5, questions=3),
        "limit_on_questions_field": dict(
            kind="extra_limit", field="monthly_questions", delta=1, duration="period"
        ),
        "limit_without_duration": dict(kind="extra_limit", field="max_documents", delta=1),
        "question_with_duration": dict(kind="question_pack", questions=5, duration="period"),
        "fine": dict(kind="question_pack", questions=5),
    }
    try:
        for label, values in cases.items():
            params = {
                "kind": None,
                "credit": None,
                "field": None,
                "delta": None,
                "questions": None,
                "duration": None,
            } | values
            try:
                await _sql(factory, insert, name=label, **params)
                out[label] = "ok"
            except IntegrityError as exc:
                out[label] = "refused"
                assert "ck_add_on" in str(exc.orig), exc.orig
        # an add-on that was granted cannot be deleted (RESTRICT); the grant dies with its tenant
        tenant = uuid.uuid4()
        await _sql(
            factory,
            "INSERT INTO tenants (id, name, plan_id, api_key_hash) "
            "SELECT :id, 'T', id, '' FROM plans WHERE is_default",
            id=tenant,
        )
        [row] = await _sql(factory, "SELECT id FROM add_on_catalogue WHERE name = 'fine'")
        grant = uuid.uuid4()
        await _sql(
            factory,
            "INSERT INTO add_on_grants (id, tenant_id, add_on_id, kind, name, currency, "
            "price_minor, questions, payment, granted_by) VALUES "
            "(:g, :t, :a, 'question_pack', 'fine', 'INR', 100, 5, 'external', 'test')",
            g=grant,
            t=tenant,
            a=row.id,
        )
        try:
            await _sql(factory, "DELETE FROM add_on_catalogue WHERE id = :a", a=row.id)
            out["delete_granted"] = "deleted"
        except IntegrityError:
            out["delete_granted"] = "restricted"
        await _sql(factory, "DELETE FROM tenants WHERE id = :t", t=tenant)
        out["grants_after_tenant_delete"] = str(
            (await _sql(factory, "SELECT count(*) AS n FROM add_on_grants"))[0].n
        )
    finally:
        await engine.dispose()
    return out


def test_the_database_refuses_fields_that_do_not_fit_the_kind() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        out = asyncio.run(_constraints(url))
    assert out == {
        "credit_without_credit": "refused",
        "credit_with_questions": "refused",
        "limit_on_questions_field": "refused",
        "limit_without_duration": "refused",
        "question_with_duration": "refused",
        "fine": "ok",
        "delete_granted": "restricted",
        "grants_after_tenant_delete": "0",
    }


def _records(*ids: str) -> dict[str, Any]:
    return {"records": [{"id": i, "title": i.upper(), "text": f"About {i}."} for i in ids]}


async def _flow(db_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts import caps
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.exceptions import DocumentLimitReachedError

    invalidate_engine_settings_cache()
    engine = create_async_engine(db_url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        admin_api_key=ADMIN,  # type: ignore[arg-type]
    )
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.vector_store = AsyncMock()
    app.state.embedder = None
    app.state.reranker = None
    admin = {"X-API-Key": ADMIN}
    out: dict[str, Any] = {}
    base = "/api/v1/admin/billing"

    try:
        with (
            patch("callobuzz_rag.worker.tasks.ingest_records_task", MagicMock()),
            patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()),
            patch("callobuzz_rag.billing.hooks.after_balance_change", AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                plan = await c.post(
                    "/api/v1/admin/plans",
                    json={"name": "One", "max_documents": 1, "monthly_questions": 100},
                    headers=admin,
                )
                made = await c.post("/api/v1/admin/tenants", json={"name": "Shree"}, headers=admin)
                tid = made.json()["id"]
                await c.post(
                    f"/api/v1/admin/tenants/{tid}/plan",
                    json={"plan_id": plan.json()["id"]},
                    headers=admin,
                )
                kb = await c.post(
                    "/api/v1/corpora",
                    json={"name": "Help"},
                    headers={"X-API-Key": made.json()["api_key"]},
                )
                assert kb.status_code == 201, kb.text
                ck = {"X-API-Key": kb.json()["api_key"]}
                corpus_id = uuid.UUID(kb.json()["id"])
                tenant_id = uuid.UUID(tid)

                async def add_on(**fields: Any) -> dict[str, Any]:
                    res = await c.post(
                        f"{base}/add-ons",
                        json={"currency": "INR", "price_minor": 100, **fields},
                        headers=admin,
                    )
                    assert res.status_code == 201, res.text
                    return dict(res.json())

                async def apply(row: dict[str, Any], key: str, **kw: Any) -> Any:
                    return await c.post(
                        f"{base}/accounts/{tid}/grants",
                        json={"add_on_id": row["id"], "idempotency_key": key, **kw},
                        headers=admin,
                    )

                # --- the document limit follows an add-on, and the worker agrees
                first = await c.post("/api/v1/ingest/records", json=_records("a"), headers=ck)
                second = await c.post("/api/v1/ingest/records", json=_records("b"), headers=ck)
                out["before"] = (first.status_code, second.status_code)
                extra_doc = await add_on(
                    kind="extra_limit",
                    name="+1 document",
                    limit_field="max_documents",
                    limit_delta=1,
                    duration="permanent",
                )
                granted = await apply(extra_doc, "doc-grant-0001")
                job = uuid.UUID(first.json()["job_id"])
                third = await c.post("/api/v1/ingest/records", json=_records("c"), headers=ck)
                fourth = await c.post("/api/v1/ingest/records", json=_records("d"), headers=ck)
                out["granted"] = (granted.status_code, third.status_code, fourth.status_code)
                async with factory() as session:
                    out["limits_with"] = (
                        await caps.effective_limits(session, tenant_id)
                    ).max_documents
                    await caps.ensure_document_room_for_job(session, corpus_id, job, 1)  # 1+1 <= 2
                revoked = await c.post(
                    f"{base}/accounts/{tid}/grants/{granted.json()['id']}/revoke",
                    json={"memo": "test"},
                    headers=admin,
                )
                fifth = await c.post("/api/v1/ingest/records", json=_records("e"), headers=ck)
                out["revoked"] = (revoked.status_code, fifth.status_code)
                async with factory() as session:
                    out["limits_without"] = (
                        await caps.effective_limits(session, tenant_id)
                    ).max_documents
                    try:
                        await caps.ensure_document_room_for_job(session, corpus_id, job, 2)
                        out["worker_after_revoke"] = "ok"
                    except DocumentLimitReachedError:
                        out["worker_after_revoke"] = "refused"

                # --- question packs: this month only; an override replaces, packs add
                this_month = caps.current_month()
                last_month = date(2000, 1, 1)
                pack = await add_on(kind="question_pack", name="+50 questions", questions=50)
                packed = await apply(pack, "question-grant-1")
                async with factory() as session:
                    out["quota_this"] = await caps.question_quota(session, tenant_id, this_month)
                    out["quota_other"] = await caps.question_quota(session, tenant_id, last_month)
                    out["quota_next"] = await caps.question_quota(
                        session, tenant_id, date(this_month.year + 1, 1, 1)
                    )
                await c.patch(
                    f"{base}/accounts/{tid}",
                    json={"limit_overrides": {"monthly_questions": 300}},
                    headers=admin,
                )
                async with factory() as session:
                    out["quota_override"] = await caps.question_quota(
                        session, tenant_id, this_month
                    )
                await c.post(
                    f"{base}/accounts/{tid}/grants/{packed.json()['id']}/revoke",
                    json={},
                    headers=admin,
                )
                async with factory() as session:
                    out["quota_revoked"] = await caps.question_quota(session, tenant_id, this_month)

                # --- five clicks, one key: one grant, one entry
                credit = await add_on(
                    kind="credit_pack",
                    name="Credit",
                    price_minor=100_000,
                    credit_minor=100_000,
                    bonus_pct=10,
                )
                racing = await asyncio.gather(*(apply(credit, "credit-click-1") for _ in range(5)))
                out["race"] = sorted(r.status_code for r in racing)
                out["race_ids"] = len({r.json()["id"] for r in racing})

        out["grants"] = len(
            await _sql(factory, "SELECT 1 FROM add_on_grants WHERE kind = 'credit_pack'")
        )
        out["entries"] = [
            (r[0], r[1])
            for r in await _sql(
                factory, "SELECT entry_type, amount_minor FROM wallet_ledger ORDER BY seq"
            )
        ]
        out["audits"] = sorted(
            r[0]
            for r in await _sql(
                factory, "SELECT action FROM audit_log WHERE action LIKE 'billing.%grant%'"
            )
        )
    finally:
        await engine.dispose()
    return out


def test_grants_raise_the_limits_everywhere_and_a_double_click_is_one_grant() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")
        out = asyncio.run(_flow(url))
    assert out["before"] == (202, 409)  # one document at the limit
    assert out["granted"] == (201, 202, 409)  # +1 document: one more accepted, then full again
    assert out["limits_with"] == 2 and out["limits_without"] == 1
    assert out["revoked"] == (200, 409)
    assert out["worker_after_revoke"] == "refused"
    assert out["quota_this"] == (150, 0)
    assert out["quota_other"] == (100, 0) and out["quota_next"] == (100, 0)
    assert out["quota_override"] == (350, 0)  # the override 300 + the pack 50
    assert out["quota_revoked"] == (300, 0)
    assert out["race"] == [200, 200, 200, 200, 201] and out["race_ids"] == 1
    assert out["grants"] == 1
    assert out["entries"] == [("add_on_purchase", 110_000)]
    assert out["audits"].count("billing.add_on_granted") == 3  # doc, question, credit pack
    assert out["audits"].count("billing.grant_revoked") == 2
