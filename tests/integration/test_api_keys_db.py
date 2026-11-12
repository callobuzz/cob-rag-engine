"""Named API keys against real PostgreSQL and the real key middleware.

A customer and a knowledge base are created through the API with the admin key; each gets a
"Default key" (full). Then: a read key is made through the API and may ask but not ingest or
delete; a delete with a key needs `confirm_name`; the preview counts what would go; a rotated
or revoked key stops at once; `last_used_at` is written. Requires Docker.
"""

import os
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.api_key import ApiKey

pytestmark = pytest.mark.integration

ADMIN_KEY = "integration-api-keys-admin-key"


def _app(db_engine):
    from callobuzz_rag.api.app import create_app

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
        app = create_app()
    app.state.settings = Settings(_env_file=None, admin_api_key=ADMIN_KEY)
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app.state.vector_store = AsyncMock()
    app.state.embedder = AsyncMock()
    return app


async def _call(app, key: str, method: str, path: str, **kw):
    headers = {"X-API-Key": key, **kw.pop("headers", {})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, **kw)


async def test_keys_end_to_end(db_engine) -> None:
    app = _app(db_engine)
    suffix = uuid.uuid4().hex[:6]
    tenant = await _call(
        app, ADMIN_KEY, "POST", "/api/v1/admin/tenants", json={"name": f"Shree {suffix}"}
    )
    assert tenant.status_code == 201, tenant.text
    tk, tenant_id = tenant.json()["api_key"], tenant.json()["id"]
    corpus = await _call(app, tk, "POST", "/api/v1/corpora", json={"name": f"FAQ {suffix}"})
    assert corpus.status_code == 201, corpus.text
    ck, corpus_id = corpus.json()["api_key"], corpus.json()["id"]

    # The default keys are rows of api_keys: full, with a lookup segment, never the plaintext.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        rows = (
            (
                await session.execute(
                    select(ApiKey).where(
                        or_(
                            ApiKey.tenant_id == uuid.UUID(tenant_id),
                            ApiKey.corpus_id == uuid.UUID(corpus_id),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows and all(r.permission == "full" and r.lookup for r in rows)
    assert tk not in {r.key_hash for r in rows}

    # A read key, made by the customer's own key.
    made = await _call(
        app,
        tk,
        "POST",
        f"/api/v1/corpora/{corpus_id}/keys",
        json={"name": "Agent - Claude Code", "permission": "read"},
    )
    assert made.status_code == 201, made.text
    agent, agent_id = made.json()["api_key"], made.json()["id"]

    me = await _call(app, agent, "GET", "/api/v1/whoami")
    assert me.status_code == 200 and me.json()["kind"] == "corpus"
    ingest = await _call(app, agent, "POST", "/api/v1/ingest")
    assert ingest.status_code == 403 and ingest.json()["code"] == "key_permission_denied"
    assert ingest.json()["required"] == "write"
    deleted = await _call(app, agent, "DELETE", f"/api/v1/documents/{uuid.uuid4()}")
    assert deleted.status_code == 403 and deleted.json()["required"] == "full"

    # last_used_at is written on first use (and at most once a minute after).
    listed = await _call(app, ck, "GET", f"/api/v1/corpora/{corpus_id}/keys")
    assert listed.status_code == 200, listed.text
    by_name = {k["name"]: k for k in listed.json()["keys"]}
    assert by_name["Agent - Claude Code"]["last_used_at"] is not None
    assert by_name["Default key"]["permission"] == "full"

    # The delete guard, with the customer key.
    path = f"/api/v1/corpora/{corpus_id}"
    preview = await _call(app, tk, "GET", f"{path}/delete-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["confirm_name"] == f"FAQ {suffix}"
    assert preview.json()["counts"]["documents"] == 0
    bare = await _call(app, tk, "DELETE", path)
    assert bare.status_code == 422 and bare.json()["code"] == "confirmation_required"
    wrong = await _call(app, tk, "DELETE", path, params={"confirm_name": "nope"})
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    still = await _call(app, tk, "GET", path)
    assert still.status_code == 200

    # Rotate: the old secret stops at once, the new one works, the default key is unaffected.
    rotated = await _call(app, tk, "POST", f"{path}/keys/{agent_id}/rotate")
    assert rotated.status_code == 200, rotated.text
    assert (await _call(app, agent, "GET", "/api/v1/whoami")).status_code == 401
    assert (await _call(app, rotated.json()["api_key"], "GET", "/api/v1/whoami")).status_code == 200
    assert (await _call(app, ck, "GET", "/api/v1/whoami")).status_code == 200

    # Revoke.
    gone = await _call(app, tk, "DELETE", f"{path}/keys/{agent_id}")
    assert gone.status_code == 204
    assert (await _call(app, rotated.json()["api_key"], "GET", "/api/v1/whoami")).status_code == 401

    # The confirmed delete takes the knowledge base and its keys with it.
    ok = await _call(app, tk, "DELETE", path, params={"confirm_name": f"FAQ {suffix}"})
    assert ok.status_code == 204, ok.text
    async with factory() as session:
        left = (
            (await session.execute(select(ApiKey).where(ApiKey.corpus_id == uuid.UUID(corpus_id))))
            .scalars()
            .all()
        )
    assert left == []
    assert (await _call(app, ck, "GET", "/api/v1/whoami")).status_code == 401

    # And the customer: preview, then the typed name.
    tpath = f"/api/v1/admin/tenants/{tenant_id}"
    tprev = await _call(app, ADMIN_KEY, "GET", f"{tpath}/delete-preview")
    assert tprev.status_code == 200 and tprev.json()["counts"]["api_keys"] >= 1
    tdel = await _call(app, ADMIN_KEY, "DELETE", tpath, params={"confirm_name": f"Shree {suffix}"})
    assert tdel.status_code == 204, tdel.text
