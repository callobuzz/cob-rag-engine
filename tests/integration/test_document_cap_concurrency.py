"""The document limit holds under concurrency and while jobs are queued (accounts A6 fix).

On real PostgreSQL, a tenant on a plan with `max_documents: 1`:

- five concurrent `POST /ingest/records` calls, each with one new record: exactly one is
  accepted (the tenant's advisory lock serialises the checks, and each accepted job's
  reservation is committed before the next check reads the count);
- a later call while that job is still queued is refused with `queued: 1` (the live bug:
  queued jobs were not counted);
- the worker backstop sees the other jobs' reservations but not its own, and refuses
  when an active document already fills the limit;
- a job older than the in-flight window stops holding the allowance;
- a tenant with no document limit reserves nothing (`pending_new_documents` NULL);
- the phase11_007 migration backfills in-flight jobs and downgrades.

Sync test (alembic's env.py calls asyncio.run()); the async flow runs in its own loop.
Requires Docker.
"""

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

ADMIN = "admin-key-integration"


async def _sql(factory: Any, sql: str, **params: Any) -> list[Any]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        rows = list(result.all()) if result.returns_rows else []
        await session.commit()
        return rows


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
    task = MagicMock()

    try:
        with (
            patch("callobuzz_rag.worker.tasks.ingest_records_task", task),
            patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                plan = await c.post(
                    "/api/v1/admin/plans", json={"name": "One", "max_documents": 1}, headers=admin
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

                # Five concurrent imports, one new record each, at a limit of 1. A barrier
                # after the plan lookup makes all five reach the check together (without
                # the lock every one of them would read "0 active, 0 queued").
                barrier = asyncio.Barrier(5)
                real_limits = caps.effective_limits

                async def together(session: Any, tenant_id: uuid.UUID) -> Any:
                    limits = await real_limits(session, tenant_id)
                    await asyncio.wait_for(barrier.wait(), timeout=30)
                    return limits

                with patch.object(caps, "effective_limits", together):
                    racing = await asyncio.gather(
                        *(
                            c.post("/api/v1/ingest/records", json=_records(f"r{n}"), headers=ck)
                            for n in range(5)
                        )
                    )
                out["race"] = sorted(r.status_code for r in racing)
                out["race_codes"] = sorted(
                    r.json().get("code") for r in racing if r.status_code == 409
                )
                accepted = next(r for r in racing if r.status_code == 202).json()
                job_id = uuid.UUID(accepted["job_id"])

                # The accepted job is still queued (no worker ran): a new record waits.
                later = await c.post("/api/v1/ingest/records", json=_records("late"), headers=ck)
                out["later"] = (later.status_code, later.json())
                out["dispatched"] = task.delay.call_count

                # Another tenant on the default (unlimited) plan reserves nothing.
                free = await c.post("/api/v1/admin/tenants", json={"name": "Free"}, headers=admin)
                free_kb = await c.post(
                    "/api/v1/corpora",
                    json={"name": "Free KB"},
                    headers={"X-API-Key": free.json()["api_key"]},
                )
                free_ck = {"X-API-Key": free_kb.json()["api_key"]}
                free_job = await c.post(
                    "/api/v1/ingest/records", json=_records("a", "b"), headers=free_ck
                )
                out["free"] = free_job.status_code
                free_id = free_job.json()["job_id"]

        out["jobs"] = [
            (str(r[0]), r[1], r[2])
            for r in await _sql(
                factory,
                "SELECT j.id, j.status, j.pending_new_documents FROM ingest_jobs j "
                "JOIN corpora c ON c.id = j.corpus_id WHERE c.tenant_id = :t",
                t=tid,
            )
        ]
        out["free_pending"] = (
            await _sql(
                factory, "SELECT pending_new_documents FROM ingest_jobs WHERE id = :j", j=free_id
            )
        )[0][0]

        tenant_id = uuid.UUID(tid)
        async with factory() as session:
            out["usage"] = await caps.document_usage(session, tenant_id)
            out["usage_without_own"] = await caps.document_usage(
                session, tenant_id, exclude_job_id=job_id
            )
            # The worker for the accepted job: its own reservation is not counted.
            await caps.ensure_document_room_for_job(session, corpus_id, job_id, 1)
            out["own_job_passes"] = True
            # Any other job would see the accepted job's reservation.
            try:
                await caps.ensure_document_room_for_job(session, corpus_id, uuid.uuid4(), 1)
                out["other_job"] = "passed"
            except DocumentLimitReachedError as e:
                out["other_job"] = e.details()

        # An active document appears meanwhile (e.g. the limit was reached another way):
        # the accepted job's worker is refused before creating its document.
        doc = uuid.uuid4()
        await _sql(
            factory,
            "INSERT INTO documents (id, corpus_id, file_name) VALUES (:d, :c, 'x.txt')",
            d=doc,
            c=corpus_id,
        )
        await _sql(
            factory,
            "INSERT INTO document_versions (document_id, status) VALUES (:d, 'active')",
            d=doc,
        )
        async with factory() as session:
            try:
                await caps.ensure_document_room_for_job(session, corpus_id, job_id, 1)
                out["backstop"] = "passed"
            except DocumentLimitReachedError as e:
                out["backstop"] = e.details()

        # A job "in flight" for more than a day stops holding the allowance.
        await _sql(
            factory,
            "UPDATE ingest_jobs SET created_at = now() - interval '25 hours' WHERE id = :j",
            j=job_id,
        )
        async with factory() as session:
            out["usage_stale"] = await caps.document_usage(session, tenant_id)
    finally:
        await engine.dispose()
        invalidate_engine_settings_cache()
    return out


async def _seed_jobs(url: str) -> dict[str, uuid.UUID]:
    """At phase11_006: one corpus with jobs in every relevant state."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            tid = (
                await conn.execute(
                    text(
                        "INSERT INTO tenants (name, plan_id) "
                        "SELECT 'Old', id FROM plans WHERE is_default RETURNING id"
                    )
                )
            ).scalar_one()
            cid = (
                await conn.execute(
                    text("INSERT INTO corpora (name, tenant_id) VALUES ('KB', :t) RETURNING id"),
                    {"t": tid},
                )
            ).scalar_one()
            ids: dict[str, uuid.UUID] = {}
            for name, status, mode, target, total in (
                ("upload", "parsing", "auto", None, 1),
                ("replace", "queued", "replace", uuid.uuid4(), 1),
                ("records", "queued", "records", None, 7),
                ("done", "completed", "new", None, 1),
                ("review", "pending_review", "auto", None, 1),
            ):
                ids[name] = (
                    await conn.execute(
                        text(
                            "INSERT INTO ingest_jobs (corpus_id, status, mode, "
                            "target_document_id, total_documents) "
                            "VALUES (:c, :s, :m, :t, :n) RETURNING id"
                        ),
                        {"c": cid, "s": status, "m": mode, "t": target, "n": total},
                    )
                ).scalar_one()
            return ids
    finally:
        await engine.dispose()


async def _read(url: str, sql: str) -> list[Any]:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql))
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


def test_document_limit_holds_under_concurrency_and_queued_jobs() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)

        _alembic(url, "phase11_006")
        seeded = asyncio.run(_seed_jobs(url))
        _alembic(url, "phase11_007")
        backfill = {
            str(r[0]): r[1]
            for r in asyncio.run(_read(url, "SELECT id, pending_new_documents FROM ingest_jobs"))
        }
        _alembic(url, "phase11_006", down=True)
        dropped = asyncio.run(
            _read(
                url,
                "SELECT count(*) FROM information_schema.columns WHERE table_name = "
                "'ingest_jobs' AND column_name = 'pending_new_documents'",
            )
        )
        _alembic(url, "head")
        asyncio.run(_read(url, "DELETE FROM corpora"))
        asyncio.run(_read(url, "DELETE FROM tenants"))

        out = asyncio.run(_flow(url))

    # Migration
    assert backfill == {
        str(seeded["upload"]): 1,
        str(seeded["replace"]): 0,
        str(seeded["records"]): 7,
        str(seeded["done"]): None,
        str(seeded["review"]): None,
    }
    assert dropped == [(0,)]

    # Concurrency: exactly one of five accepted, the others refused with the code.
    assert out["race"] == [202, 409, 409, 409, 409]
    assert out["race_codes"] == ["document_limit_reached"] * 4
    status, body = out["later"]
    assert status == 409
    assert body == {
        "detail": "Document limit reached (1)",
        "code": "document_limit_reached",
        "limit": 1,
        "used": 0,
        "queued": 1,
        "adding": 1,
    }
    assert out["dispatched"] == 1  # only the accepted job reached the task queue
    assert len(out["jobs"]) == 1
    assert out["jobs"][0][1:] == ("queued", 1)
    assert out["free"] == 202 and out["free_pending"] is None

    # Counting and the worker backstop
    assert out["usage"] == (0, 1)
    assert out["usage_without_own"] == (0, 0)
    assert out["own_job_passes"] is True
    assert out["other_job"] == {
        "code": "document_limit_reached",
        "limit": 1,
        "used": 0,
        "queued": 1,
        "adding": 1,
    }
    assert out["backstop"] == {
        "code": "document_limit_reached",
        "limit": 1,
        "used": 1,
        "queued": 0,
        "adding": 1,
    }
    assert out["usage_stale"] == (1, 0)
