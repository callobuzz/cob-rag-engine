"""Accounts A6 on real PostgreSQL: plans and the four limits.

A tenant on a plan with `monthly_questions=2` and `max_knowledge_bases=1`: a second
knowledge base is refused; two answered questions (the LLM is a stub orchestrator), then
the third is 429; deleting and recreating the knowledge base does not reset the month (the
counter is keyed by tenant, not by the CASCADE-deleted `usage_daily` rows); the admin
still gets answers (and they count); a new month allows again. Plus the active-document
count's semantics, the widget-key cap, plan deletion in use, the single-default index,
and the phase11_006 migration (seed, backfill from `usage_daily`, downgrade).

A sync test (alembic's env.py calls asyncio.run()); the async flow runs in its own loop.
Requires Docker.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime
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


def _orchestrator() -> MagicMock:
    """Answers every question at once, like a configured LLM would."""
    orch = MagicMock()

    async def answer(**_: Any) -> dict[str, Any]:
        return {
            "answer": "42",
            "citations": [],
            "confidence": 0.9,
            "query_id": uuid.uuid4(),
            "model_used": "stub/model",
            "latency_ms": 1,
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }

    orch.query = AsyncMock(side_effect=answer)
    return orch


async def _sql(factory: Any, sql: str, **params: Any) -> list[Any]:
    async with factory() as session:
        result = await session.execute(text(sql), params)
        rows = list(result.all()) if result.returns_rows else []
        await session.commit()
        return rows


async def _flow(db_url: str) -> dict[str, Any]:
    from callobuzz_rag.accounts import caps
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings

    invalidate_engine_settings_cache()
    engine = create_async_engine(db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        admin_api_key=ADMIN,  # type: ignore[arg-type]
        llm_api_key="k",  # type: ignore[arg-type]
    )
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.vector_store = AsyncMock()
    app.state.embedder = None
    app.state.reranker = None
    admin = {"X-API-Key": ADMIN}
    no_cache = MagicMock()
    no_cache.get = AsyncMock(return_value=None)
    no_cache.set = AsyncMock()
    out: dict[str, Any] = {}

    try:
        with (
            patch(
                "callobuzz_rag.api.routes.query._build_orchestrator",
                side_effect=lambda *a, **k: _orchestrator(),
            ),
            patch("callobuzz_rag.api.routes.query._get_query_cache", return_value=no_cache),
            patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                plans = (await c.get("/api/v1/admin/plans", headers=admin)).json()
                out["seed"] = [(p["name"], p["is_default"]) for p in plans["items"]]
                starter = await c.post(
                    "/api/v1/admin/plans",
                    json={"name": "Starter", "max_knowledge_bases": 1, "monthly_questions": 2},
                    headers=admin,
                )
                pid = starter.json()["id"]
                made = await c.post(
                    "/api/v1/admin/tenants", json={"name": "Shree Labs"}, headers=admin
                )
                tid, tk = made.json()["id"], made.json()["api_key"]
                out["created_plan"] = made.json()["plan_id"] == plans["items"][0]["id"]
                moved = await c.post(
                    f"/api/v1/admin/tenants/{tid}/plan", json={"plan_id": pid}, headers=admin
                )
                out["moved"] = (moved.status_code, moved.json()["plan_name"])
                tenant = {"X-API-Key": tk}

                kb = await c.post("/api/v1/corpora", json={"name": "Help"}, headers=tenant)
                assert kb.status_code == 201, kb.text
                ck = {"X-API-Key": kb.json()["api_key"]}
                second = await c.post("/api/v1/corpora", json={"name": "Two"}, headers=tenant)
                out["second_kb"] = (second.status_code, second.json())

                answers = [
                    (await c.post("/api/v1/query", json={"query": "hi"}, headers=ck)).status_code
                    for _ in range(2)
                ]
                out["answers"] = answers
                third = await c.post("/api/v1/query", json={"query": "hi"}, headers=ck)
                out["third"] = (third.status_code, third.json(), third.headers.get("retry-after"))

                # Delete the knowledge base and make a new one: the month is not reset.
                gone = await c.delete(
                    f"/api/v1/corpora/{kb.json()['id']}",
                    params={"confirm_name": "Help"},
                    headers=tenant,
                )
                out["deleted"] = gone.status_code
                kb2 = await c.post("/api/v1/corpora", json={"name": "Again"}, headers=tenant)
                assert kb2.status_code == 201, kb2.text
                ck2 = {"X-API-Key": kb2.json()["api_key"]}
                after = await c.post("/api/v1/query", json={"query": "hi"}, headers=ck2)
                out["after_recreate"] = (after.status_code, after.json().get("code"))

                # The admin is not capped, and its answer still counts.
                by_admin = await c.post(
                    "/api/v1/query",
                    json={"query": "hi", "corpus_id": kb2.json()["id"]},
                    headers=admin,
                )
                out["admin"] = by_admin.status_code
                usage = await c.get("/api/v1/tenants/me/plan", headers=tenant)
                out["usage"] = usage.json()
                admin_view = await c.get(f"/api/v1/admin/tenants/{tid}", headers=admin)
                out["admin_view"] = {
                    k: admin_view.json()[k]
                    for k in (
                        "plan_name",
                        "monthly_questions",
                        "questions_this_month",
                        "max_corpora",
                        "effective_max_corpora",
                    )
                }

                # A new UTC month: the count so far belongs to the previous month.
                await _sql(
                    factory,
                    "UPDATE tenant_question_counts SET month = (month - interval '1 month')::date "
                    "WHERE tenant_id = :t",
                    t=tid,
                )
                new_month = await c.post("/api/v1/query", json={"query": "hi"}, headers=ck2)
                out["new_month"] = new_month.status_code

                # Widget keys: a plan limit of 1.
                await c.patch(
                    f"/api/v1/admin/plans/{pid}", json={"max_widget_keys": 1}, headers=admin
                )
                wk_body = {"name": "Site", "allowed_origins": ["https://shree.example"]}
                path = f"/api/v1/corpora/{kb2.json()['id']}/widget-keys"
                first_key = await c.post(path, json=wk_body, headers=tenant)
                second_key = await c.post(path, json=wk_body, headers=tenant)
                rotated = await c.post(f"{path}/{first_key.json()['id']}/rotate", headers=tenant)
                out["widget_keys"] = (
                    first_key.status_code,
                    second_key.status_code,
                    second_key.json().get("code"),
                    rotated.status_code,
                )

                # Deleting a plan a tenant uses; the default.
                in_use = await c.delete(f"/api/v1/admin/plans/{pid}", headers=admin)
                out["delete_in_use"] = (in_use.status_code, in_use.json())
                default_id = plans["items"][0]["id"]
                out["delete_default"] = (
                    await c.delete(f"/api/v1/admin/plans/{default_id}", headers=admin)
                ).json()["code"]

        # Active documents: only those with an active version, only this tenant's.
        kb2_id = uuid.UUID(kb2.json()["id"])
        other = await c_other_tenant_corpus(factory)
        for corpus_id, statuses in (
            (kb2_id, ["active"]),
            (kb2_id, ["inactive", "active"]),  # one document, two versions
            (kb2_id, ["inactive"]),  # restorable, not counted
            (kb2_id, []),  # still processing its first version
            (other, ["active"]),  # another tenant's
        ):
            doc = uuid.uuid4()
            await _sql(
                factory,
                "INSERT INTO documents (id, corpus_id, file_name) VALUES (:d, :c, 'f.txt')",
                d=doc,
                c=corpus_id,
            )
            for status in statuses:
                await _sql(
                    factory,
                    "INSERT INTO document_versions (document_id, status) VALUES (:d, :s)",
                    d=doc,
                    s=status,
                )
        async with factory() as session:
            out["active_documents"] = await caps.active_document_count(session, uuid.UUID(tid))

        # At most one default plan, in the database itself.
        try:
            await _sql(factory, "INSERT INTO plans (name, is_default) VALUES ('Second', true)")
            out["second_default"] = "accepted"
        except IntegrityError:
            out["second_default"] = "refused"
        out["audits"] = sorted({r[0] for r in await _sql(factory, "SELECT action FROM audit_log")})
    finally:
        await engine.dispose()
        invalidate_engine_settings_cache()
    return out


async def c_other_tenant_corpus(factory: Any) -> uuid.UUID:
    """A corpus of a second tenant (on the default plan through the INSERT default)."""
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    async with factory() as session:
        tenant = Tenant(name="Other")
        session.add(tenant)
        await session.flush()
        corpus = Corpus(name="Other KB", tenant_id=tenant.id)
        session.add(corpus)
        await session.commit()
        return corpus.id


async def _seed_before(url: str) -> uuid.UUID:
    """At phase11_005: a tenant with this month's usage and last month's."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            tid = (
                await conn.execute(
                    text("INSERT INTO tenants (name, max_corpora) VALUES ('Old', 7) RETURNING id")
                )
            ).scalar_one()
            cid = (
                await conn.execute(
                    text("INSERT INTO corpora (name, tenant_id) VALUES ('KB', :t) RETURNING id"),
                    {"t": tid},
                )
            ).scalar_one()
            today = datetime.now(UTC).date()
            last_month = today.replace(day=1).replace(
                year=today.year - (today.month == 1), month=(today.month - 2) % 12 + 1
            )
            for day, source, n in (
                (today, "api", 5),
                (today.replace(day=1), "widget:x", 3),
                (last_month, "api", 100),
            ):
                await conn.execute(
                    text(
                        "INSERT INTO usage_daily (day, corpus_id, tenant_id, source, requests) "
                        "VALUES (:d, :c, :t, :s, :n)"
                    ),
                    {"d": day, "c": cid, "t": tid, "s": source, "n": n},
                )
            return uuid.UUID(str(tid))
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


def test_caps_flow_and_migration() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)

        _alembic(url, "phase11_005")
        old = asyncio.run(_seed_before(url))
        _alembic(url, "phase11_006")
        plans = asyncio.run(_read(url, "SELECT name, is_default, is_active FROM plans"))
        tenants = asyncio.run(
            _read(
                url, "SELECT t.max_corpora, p.name FROM tenants t JOIN plans p ON p.id = t.plan_id"
            )
        )
        counts = asyncio.run(
            _read(url, "SELECT tenant_id, month, questions FROM tenant_question_counts")
        )
        nullable = asyncio.run(
            _read(
                url,
                "SELECT column_name, is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'tenants' AND column_name IN ('max_corpora', 'plan_id') "
                "ORDER BY column_name",
            )
        )
        asyncio.run(_read(url, "UPDATE tenants SET max_corpora = NULL"))
        _alembic(url, "phase11_005", down=True)
        after_down = asyncio.run(_read(url, "SELECT max_corpora FROM tenants"))
        gone = asyncio.run(
            _read(
                url,
                "SELECT to_regclass('public.plans') IS NULL, "
                "to_regclass('public.tenant_question_counts') IS NULL",
            )
        )
        _alembic(url, "head")
        asyncio.run(_read(url, "DELETE FROM usage_daily"))
        asyncio.run(_read(url, "DELETE FROM tenant_question_counts"))
        asyncio.run(_read(url, "DELETE FROM corpora"))
        asyncio.run(_read(url, "DELETE FROM tenants"))

        out = asyncio.run(_flow(url))

    # Migration
    assert plans == [("Standard", True, True)]
    assert tenants == [(7, "Standard")]  # the old limit kept as the override
    month_start = datetime.now(UTC).date().replace(day=1)
    assert counts == [(old, month_start, 8)]  # this month only: 5 + 3
    assert nullable == [("max_corpora", "YES", None), ("plan_id", "NO", None)]
    assert after_down == [(10,)]
    assert gone == [(True, True)]

    # Plans and the question cap
    # the default first, then by name: the inactive Free plan that phase16_001 seeds, and the
    # Internal plan (operator workspace) and Pilot that phase11_009 seeds
    assert out["seed"] == [
        ("Standard", True),
        ("Free", False),
        ("Internal", False),
        ("Pilot", False),
    ]
    assert out["created_plan"] is True
    assert out["moved"] == (200, "Starter")
    assert out["second_kb"] == (
        409,
        {"detail": "Tenant corpus limit reached (1)", "code": "kb_limit_reached", "limit": 1},
    )
    assert out["answers"] == [200, 200]
    status, body, retry_after = out["third"]
    assert status == 429 and body["code"] == "question_cap_reached"
    assert body["limit"] == 2 and body["used"] == 2
    assert body["detail"] == "Monthly question limit reached — contact your provider"
    assert int(retry_after) >= 1
    assert out["deleted"] == 204
    assert out["after_recreate"] == (429, "question_cap_reached")
    assert out["admin"] == 200
    usage = out["usage"]
    assert usage["plan"]["name"] == "Starter"
    assert usage["limits"]["monthly_questions"] == 2
    assert usage["limits"]["max_knowledge_bases"] == 1
    assert usage["usage"]["questions"] == 3  # two answered, the admin's counted too
    assert usage["usage"]["knowledge_bases"] == 1
    assert out["admin_view"] == {
        "plan_name": "Starter",
        "monthly_questions": 2,
        "questions_this_month": 3,
        "max_corpora": None,
        "effective_max_corpora": 1,
    }
    assert out["new_month"] == 200

    # Widget keys, plan deletion, the default
    assert out["widget_keys"] == (201, 409, "widget_key_limit_reached", 200)
    code, body = out["delete_in_use"]
    assert code == 409 and body["code"] == "plan_in_use" and body["tenant_count"] == 1
    assert out["delete_default"] == "plan_is_default"
    assert out["second_default"] == "refused"
    assert out["active_documents"] == 2
    for action in ("plan.created", "plan.updated", "tenant.plan_changed", "tenant.created"):
        assert action in out["audits"], action
