"""Content Portal review API against real PostgreSQL: audit trail, list counts, coded refusals.

The audit trail used to answer 500 once a chunk had been split, merged, approved or
rejected (the entries store `details` as an object) or touched by the AI chat (whose
entries carried no timestamp); the workspace list answered null counts; submit / approve /
reject / ingest refused a transition with an uncoded 409. Requires Docker.
"""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import hash_api_key

pytestmark = pytest.mark.integration

ADMIN_KEY = "integration-portal-review-admin-key"
P = "/api/v1/portal"
EDITOR = "editor@example.com"


@pytest_asyncio.fixture
async def corpus_id(db_engine) -> AsyncIterator[str]:
    """A tenant with one corpus (rows inserted directly)."""
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id, cid = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"review {tenant_id.hex[:6]}",
                api_key_hash=hash_api_key(f"tk_{uuid.uuid4().hex}"),
            )
        )
        await session.flush()
        session.add(
            Corpus(
                id=cid,
                name=f"review corpus {cid.hex[:6]}",
                tenant_id=tenant_id,
                qdrant_collection=f"corpus_{cid.hex[:8]}",
                api_key_hash=hash_api_key(f"ck_{uuid.uuid4().hex}"),
            )
        )
        await session.commit()
    yield str(cid)
    async with factory() as session:
        await session.execute(delete(Corpus).where(Corpus.id == cid))
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.commit()


def _app(db_engine):
    from callobuzz_rag.api.app import create_app

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "true"}):
        app = create_app()
    app.state.settings = Settings(
        _env_file=None, admin_api_key=ADMIN_KEY, enable_content_portal=True
    )
    app.state.session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app.state.vector_store = AsyncMock()
    app.state.embedder = AsyncMock()
    return app


async def _call(app, method: str, path: str, **kw: Any):
    headers = {"X-API-Key": ADMIN_KEY, **kw.pop("headers", {})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, **kw)


async def _workspace(app, corpus_id: str, **body: Any) -> str:
    res = await _call(
        app,
        "POST",
        f"{P}/workspaces?corpus_id={corpus_id}",
        json={"title": "Docs", **body},
        headers={"X-User-Id": EDITOR},
    )
    assert res.status_code == 201, res.text
    return str(res.json()["id"])


async def _chunk(app, ws: str, text: str) -> str:
    res = await _call(
        app, "POST", f"{P}/workspaces/{ws}/chunks", json={"text": text, "user": EDITOR}
    )
    assert res.status_code in (200, 201), res.text
    return str(res.json()["id"])


async def test_audit_trail_serialises_every_stored_shape(db_engine, corpus_id) -> None:
    from callobuzz_rag.content_portal.models.staged_chunk import StagedChunk

    app = _app(db_engine)
    ws = await _workspace(app, corpus_id)
    lock = await _call(app, "POST", f"{P}/workspaces/{ws}/lock", json={"user": EDITOR})
    assert lock.status_code == 200, lock.text

    a = await _chunk(app, ws, "First part. Second part.")
    b = await _chunk(app, ws, "Another chunk.")
    c = await _chunk(app, ws, "Yet another chunk.")
    split = await _call(
        app, "POST", f"{P}/workspaces/{ws}/chunks/{a}/split", json={"position": 11, "user": EDITOR}
    )
    assert split.status_code == 200, split.text
    merged = await _call(
        app, "POST", f"{P}/workspaces/{ws}/chunks/merge", json={"chunk_ids": [b, c], "user": EDITOR}
    )
    assert merged.status_code == 200, merged.text
    first = split.json()["first"]["id"]
    second = split.json()["second"]["id"]
    approve = await _call(
        app,
        "POST",
        f"{P}/workspaces/{ws}/chunks/{first}/approve",
        json={"user": EDITOR, "reason": "reads well"},
    )
    assert approve.status_code == 200, approve.text
    reject = await _call(
        app,
        "POST",
        f"{P}/workspaces/{ws}/chunks/{second}/reject",
        json={"user": EDITOR, "reason": "too short"},
    )
    assert reject.status_code == 200, reject.text

    # Rows written before this fix: the AI chat's entries (no timestamp, the source ids
    # at the top level) and a `details` stored as a plain string.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            StagedChunk(
                workspace_id=uuid.UUID(ws),
                text="from the AI chat",
                chunk_index=9,
                source="manual",
                edit_history=[
                    {"user": "ai", "action": "split_from", "source_chunk_id": str(uuid.uuid4())},
                    {"user": "ai", "action": "rewrite", "old_text": "before"},
                    {
                        "user": "legacy",
                        "action": "note",
                        "timestamp": "2026-03-09T10:00:00+00:00",
                        "details": "a plain string",
                    },
                ],
            )
        )
        await session.commit()

    trail = await _call(app, "GET", f"{P}/workspaces/{ws}/audit-trail")
    assert trail.status_code == 200, trail.text
    entries = trail.json()["entries"]
    by_action: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_action.setdefault(e["action"], []).append(e)
    # (The split and merged chunks are gone, and their "create" entries with them.)
    assert {"split", "merge", "approve", "reject", "split_from", "rewrite", "note"} <= set(
        by_action
    )
    assert by_action["split"][0]["details"]["position"] == 11
    assert by_action["split"][0]["details"]["source_chunk_id"] == a
    assert sorted(by_action["merge"][0]["details"]["source_chunk_ids"]) == sorted([b, c])
    assert by_action["approve"][0]["details"] == {"reason": "reads well"}
    assert by_action["reject"][0]["details"] == {"reason": "too short"}
    assert by_action["note"][0]["details"] == "a plain string"
    # The AI chat's top-level extras are kept, as details.
    assert "source_chunk_id" in by_action["split_from"][0]["details"]
    assert by_action["split_from"][0]["timestamp"] is None
    assert by_action["rewrite"][0]["old_text"] == "before"
    assert trail.json()["total"] == len(entries)


async def test_workspace_list_carries_counts(db_engine, corpus_id) -> None:
    app = _app(db_engine)
    busy = await _workspace(app, corpus_id)
    empty = await _workspace(app, corpus_id)
    await _call(app, "POST", f"{P}/workspaces/{busy}/lock", json={"user": EDITOR})
    one = await _chunk(app, busy, "One.")
    await _chunk(app, busy, "Two.")
    await _chunk(app, busy, "Three.")
    ok = await _call(
        app, "POST", f"{P}/workspaces/{busy}/chunks/{one}/approve", json={"user": EDITOR}
    )
    assert ok.status_code == 200, ok.text
    # A staged content row, inserted directly (the upload route needs object storage).
    from callobuzz_rag.content_portal.models.staged_content import StagedContent

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            StagedContent(
                workspace_id=uuid.UUID(busy), source_type="text_dump", raw_storage_key="k"
            )
        )
        await session.commit()

    listed = await _call(app, "GET", f"{P}/workspaces?corpus_id={corpus_id}")
    assert listed.status_code == 200, listed.text
    items = {w["id"]: w for w in listed.json()["items"]}
    counts = ("content_count", "chunk_count", "approved_chunk_count")
    assert tuple(items[busy][k] for k in counts) == (1, 3, 1)
    assert tuple(items[empty][k] for k in counts) == (0, 0, 0)

    # The single workspace reads the same numbers.
    single = await _call(app, "GET", f"{P}/workspaces/{busy}")
    assert tuple(single.json()[k] for k in counts) == (1, 3, 1)


async def test_review_refusals_are_coded(db_engine, corpus_id) -> None:
    app = _app(db_engine)
    ws = await _workspace(app, corpus_id, workflow_mode="maker_checker", assigned_reviewer="rev")
    await _call(app, "POST", f"{P}/workspaces/{ws}/lock", json={"user": EDITOR})
    await _chunk(app, ws, "Content.")

    # A draft cannot be approved, rejected or ingested.
    for action, body in (
        ("approve", {"user": "rev"}),
        ("reject", {"user": "rev", "feedback": "no"}),
        ("ingest", {"user": "rev"}),
    ):
        res = await _call(app, "POST", f"{P}/workspaces/{ws}/{action}", json=body)
        assert (res.status_code, res.json().get("code")) == (409, "invalid_status_transition"), (
            action,
            res.text,
        )

    submitted = await _call(app, "POST", f"{P}/workspaces/{ws}/submit", json={"user": EDITOR})
    assert submitted.status_code == 200, submitted.text
    again = await _call(app, "POST", f"{P}/workspaces/{ws}/submit", json={"user": EDITOR})
    assert (again.status_code, again.json().get("code")) == (409, "invalid_status_transition")

    # Maker-checker: the creator cannot approve their own workspace. The code catalog has
    # no code for it, so it stays a plain 403 with a clear detail.
    own = await _call(app, "POST", f"{P}/workspaces/{ws}/approve", json={"user": EDITOR})
    assert own.status_code == 403, own.text
    assert "creator cannot approve" in own.json()["detail"]
    assert "code" not in own.json()
