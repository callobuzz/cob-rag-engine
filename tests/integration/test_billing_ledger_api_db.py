"""The transactions API end to end on PostgreSQL (BIL3): manual entries, replays, filters,
paging, `available` counting usage not yet posted, and what the customer is allowed to see.
Needs Docker."""

import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.analytics.usage import record_usage
from callobuzz_rag.billing.posting import post_pending
from tests.integration.conftest import _async_url

pytestmark = pytest.mark.integration

ADMIN = "admin-key-ledger-api"


async def _app(url: str) -> tuple[Any, Any, Any]:
    from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings

    invalidate_engine_settings_cache()
    engine = create_async_engine(url, pool_size=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(_env_file=None, admin_api_key=ADMIN)  # type: ignore[call-arg, arg-type]
    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = settings
    app.state.session_factory = factory
    app.state.vector_store = AsyncMock()
    app.state.embedder = None
    app.state.reranker = None
    return app, factory, engine


async def test_the_transactions_api(migrated_postgres) -> None:  # type: ignore[no-untyped-def]
    app, factory, engine = await _app(_async_url(migrated_postgres))
    admin = {"X-API-Key": ADMIN}
    try:
        with patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
                plan = (
                    await c.post(
                        "/api/v1/admin/plans",
                        json={"name": f"Ledger {uuid.uuid4().hex[:6]}"},
                        headers=admin,
                    )
                ).json()
                priced = await c.put(
                    f"/api/v1/admin/billing/plans/{plan['id']}/pricing",
                    json={"usage_mode": "per_question", "per_question_minor": 200},
                    headers=admin,
                )
                assert priced.status_code == 200, priced.text
                made = (
                    await c.post("/api/v1/admin/tenants", json={"name": "Ledger Co"}, headers=admin)
                ).json()
                tid = made["id"]
                tk = {"X-API-Key": made["api_key"]}
                # a dearer plan waits for the notice period unless the business asked (TV3)
                moved = await c.post(
                    f"/api/v1/admin/tenants/{tid}/plan",
                    json={
                        "plan_id": plan["id"],
                        "business_agreed": True,
                        "reason": "The business asked for per-question billing.",
                    },
                    headers=admin,
                )
                assert moved.status_code == 200 and moved.json()["plan_id"] == plan["id"]
                url = f"/api/v1/admin/billing/accounts/{tid}/ledger"

                top = {
                    "entry_type": "top_up",
                    "amount_minor": 50_000,
                    "idempotency_key": "first-top-up",
                }
                first = await c.post(url, json=top, headers=admin)
                again = await c.post(url, json=top, headers=admin)
                other = await c.post(url, json={**top, "amount_minor": 50_001}, headers=admin)
                assert (first.status_code, again.status_code, other.status_code) == (201, 200, 409)
                assert again.json()["id"] == first.json()["id"]
                assert other.json()["code"] == "idempotency_conflict"

                adj = await c.post(
                    url,
                    json={
                        "entry_type": "adjustment",
                        "amount_minor": -1_000,
                        "memo": "secret admin note",
                        "memo_public": "Correction",
                        "idempotency_key": "adjustment-1",
                    },
                    headers=admin,
                )
                assert adj.status_code == 201 and adj.json()["balance_after_minor"] == 49_000

                # three answered questions, posted by the minute job; one more still unposted
                async with factory() as s:
                    from callobuzz_rag.db.models.corpus import Corpus

                    corpus = Corpus(name="KB", tenant_id=uuid.UUID(tid))
                    s.add(corpus)
                    await s.commit()
                    corpus_id = corpus.id
                for _ in range(3):
                    async with factory() as s:
                        await record_usage(
                            s,
                            corpus_id=corpus_id,
                            tenant_id=uuid.UUID(tid),
                            source="api",
                            prompt_tokens=1,
                            completion_tokens=1,
                            model="m",
                            request_id=uuid.uuid4(),
                        )
                await post_pending(factory)
                async with factory() as s:
                    await record_usage(
                        s,
                        corpus_id=corpus_id,
                        tenant_id=uuid.UUID(tid),
                        source="api",
                        prompt_tokens=1,
                        completion_tokens=1,
                        model="m",
                        request_id=uuid.uuid4(),
                    )

                listed = (await c.get(url, headers=admin)).json()
                assert [e["entry_type"] for e in listed["items"]] == [
                    "usage_charge",
                    "adjustment",
                    "top_up",
                ]
                assert listed["total"] == 3
                assert listed["items"][0]["amount_minor"] == -600
                assert listed["items"][0]["memo_public"] == "Usage: 3 answers"
                assert listed["balance_minor"] == 50_000 - 1_000 - 600
                assert (
                    listed["available_minor"] == listed["balance_minor"] - 200
                )  # the unposted one

                only = (await c.get(url, params={"entry_type": "top_up"}, headers=admin)).json()
                assert only["total"] == 1 and only["items"][0]["amount_minor"] == 50_000
                page = (await c.get(url, params={"limit": 1, "offset": 1}, headers=admin)).json()
                assert [e["entry_type"] for e in page["items"]] == ["adjustment"]
                assert page["total"] == 3
                bad = await c.get(
                    url, params={"start": "2026-02-01", "end": "2026-01-01"}, headers=admin
                )
                assert bad.status_code == 422 and bad.json()["code"] == "invalid_range"
                none = await c.get(url, params={"end": "2000-01-01"}, headers=admin)
                assert none.json()["total"] == 0

                # a refund is limited to what is still available
                over = await c.post(
                    url,
                    json={
                        "entry_type": "refund",
                        "amount_minor": 900_000,
                        "memo": "too much",
                        "idempotency_key": "refund-too-big",
                    },
                    headers=admin,
                )
                assert over.status_code == 409 and over.json()["code"] == "refund_exceeds_balance"
                assert over.json()["refundable_minor"] == 48_200

                # the customer sees descriptions, never the admin's memo or who made an entry
                mine = await c.get("/api/v1/billing/ledger", headers=tk)
                assert mine.status_code == 200, mine.text
                assert "secret admin note" not in mine.text and "actor" not in mine.text
                assert "memo" not in mine.text
                assert [i["description"] for i in mine.json()["items"]][:2] == [
                    "Usage: 3 answers",
                    "Correction",
                ]
                from callobuzz_rag.billing import reconcile

                await reconcile.run(factory)
                runs = (await c.get("/api/v1/admin/billing/reconciliation", headers=admin)).json()
                assert len(runs["items"]) == 1
                run = runs["items"][0]
                assert set(run) == {
                    "id",
                    "ran_at",
                    "day",
                    "ok",
                    "checked_accounts",
                    "findings",
                    "duration_ms",
                }
                assert run["checked_accounts"] >= 1
                assert (
                    await c.get("/api/v1/admin/billing/reconciliation", headers=tk)
                ).status_code == 403
                account = (await c.get("/api/v1/billing/account", headers=tk)).json()
                assert account["balance_minor"] == 48_400 and account["available_minor"] == 48_200
    finally:
        await engine.dispose()
