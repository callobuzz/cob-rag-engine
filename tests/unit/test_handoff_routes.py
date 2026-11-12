"""Handoffs API (C5): scoped list, detail with transcript and lead, status updates."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.handoff import Handoff
from callobuzz_rag.db.models.lead import Lead
from tests.unit.scope_harness import MATRIX, call, check, scoped_app

X = uuid.uuid4()
WHEN = datetime(2026, 10, 5, 6, tzinfo=UTC)
KEY = Fernet.generate_key().decode()


def _row(corpus_id: uuid.UUID, **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "corpus_id": corpus_id,
        "tenant_id": uuid.uuid4(),
        "conversation_id": None,
        "lead_id": None,
        "channel": "whatsapp",
        "reason": "keyword",
        "language": "hi-Latn",
        "page_url": None,
        "office_open": True,
        "status": "new",
        "handled_at": None,
        "notified_at": None,
        "summary_encrypted": None,
        "created_at": WHEN,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _with_key(app: Any) -> None:
    app.state.settings = app.state.settings.model_copy(update={"encryption_key": KEY})


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_list_scoping(key: str | None, which: str, outcome: str) -> None:
    with scoped_app() as (app, world):
        res = await call(app, key, "GET", f"/api/v1/handoffs?corpus_id={world.corpus(which).id}")
    check(outcome, res.status_code, res.text)


def _ids_for(world: Any) -> Any:
    """What `authorized_corpus_ids` resolves to for each principal, so the id routes can be
    tried against a fake session that cannot list a tenant's knowledge bases (the real rules
    are exercised by the list test above and `test_scope_corpus_routes`)."""
    mine = {
        "admin": None,
        "sa": None,
        "tk_a": [world.corpus_a.id],
        "ck_a": [world.corpus_a.id],
        "owner_a": [world.corpus_a.id],
        "member_a": [world.corpus_a.id],
    }

    def patch_for(key: str) -> Any:
        return patch(
            "callobuzz_rag.api.routes.handoffs.authorized_corpus_ids",
            AsyncMock(return_value=mine[key]),
        )

    return patch_for


@pytest.mark.parametrize("key", ["admin", "sa", "tk_a", "ck_a", "owner_a", "member_a"])
@pytest.mark.parametrize("which", ["a", "b"])
async def test_patch_and_detail_scoping(key: str, which: str) -> None:
    """A handoff of another knowledge base looks like a missing one (404), never a 403."""
    with scoped_app() as (app, world):
        row = _row(world.corpus(which).id)
        world.add_row(Handoff, row)
        with _ids_for(world)(key):
            patched = await call(
                app, key, "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "handled"}
            )
            detail = await call(app, key, "GET", f"/api/v1/handoffs/{row.id}")
    if key in ("admin", "sa") or which == "a":
        assert patched.status_code == 200 and detail.status_code == 200, patched.text
        assert row.status == "handled" and row.handled_at is not None
    else:
        assert patched.status_code == detail.status_code == 404
        assert patched.json()["code"] == "handoff_not_found"
        assert row.status == "new"


async def test_nobody_without_a_key_reads_or_updates() -> None:
    with scoped_app() as (app, world):
        row = _row(world.corpus_a.id)
        world.add_row(Handoff, row)
        patched = await call(
            app, None, "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "new"}
        )
        detail = await call(app, None, "GET", f"/api/v1/handoffs/{row.id}")
    assert patched.status_code == detail.status_code == 401


async def test_members_can_update_a_handoff() -> None:
    with scoped_app() as (app, world):
        row = _row(world.corpus_a.id)
        world.add_row(Handoff, row)
        with _ids_for(world)("member_a"):
            res = await call(
                app, "member_a", "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "handled"}
            )
    assert res.status_code == 200


async def test_unknown_handoff_is_404_and_a_bad_status_is_422() -> None:
    with scoped_app() as (app, world):
        res = await call(app, "tk_a", "GET", f"/api/v1/handoffs/{X}")
        assert res.status_code == 404 and res.json()["code"] == "handoff_not_found"
        row = _row(world.corpus_a.id)
        world.add_row(Handoff, row)
        res = await call(
            app, "tk_a", "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "done"}
        )
        assert res.status_code == 422
        res = await call(
            app,
            "tk_a",
            "PATCH",
            f"/api/v1/handoffs/{row.id}",
            json={"status": "new", "extra": 1},
        )
        assert res.status_code == 422  # strict body


async def test_status_sets_and_clears_handled_at() -> None:
    with scoped_app() as (app, world):
        row = _row(world.corpus_a.id)
        world.add_row(Handoff, row)
        res = await call(
            app, "ck_a", "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "handled"}
        )
        assert res.json()["status"] == "handled" and row.handled_at is not None
        res = await call(app, "ck_a", "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "new"})
    assert res.json()["handled_at"] is None and row.handled_at is None


async def test_list_shape_and_filters() -> None:
    with scoped_app() as (app, world):
        rows = [
            _row(world.corpus_a.id),
            _row(world.corpus_a.id, channel="callback", reason="abuse"),
        ]
        page, total = MagicMock(), MagicMock()
        page.scalars.return_value.all.return_value = rows
        total.scalar.return_value = 2
        world.session.execute = AsyncMock(side_effect=[page, total])
        res = await call(
            app,
            "ck_a",
            "GET",
            "/api/v1/handoffs?status=new&reason=keyword&channel=whatsapp&from=2026-10-01"
            "&to=2026-10-05&limit=10&offset=5",
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert (body["total"], body["limit"], body["offset"]) == (2, 10, 5)
        assert body["items"][0]["reason_label"].startswith("Mentioned a keyword")
        assert body["items"][1]["reason_label"] == "Abusive messages after a warning"
        statement = str(world.session.execute.await_args_list[0].args[0])
        assert "handoffs.status" in statement and "handoffs.reason" in statement
        assert "ORDER BY handoffs.created_at DESC, handoffs.id" in statement
        bad = await call(app, "ck_a", "GET", "/api/v1/handoffs?status=done")
        assert bad.status_code == 422
        assert (await call(app, "ck_a", "GET", "/api/v1/handoffs?limit=500")).status_code == 422


async def test_detail_has_summary_transcript_and_lead_and_is_not_cached() -> None:
    crypto = LeadCrypto(FernetEncryptor(KEY))
    lead = SimpleNamespace(
        id=uuid.uuid4(),
        fields_encrypted=crypto.encrypt_json(
            {"name": "Priya", "phone": "+919876543210", "email": None}
        ),
    )
    with scoped_app() as (app, world):
        _with_key(app)
        row = _row(
            world.corpus_a.id,
            conversation_id=uuid.uuid4(),
            lead_id=lead.id,
            channel="callback",
            summary_encrypted=FernetEncryptor(KEY).encrypt("Wants a call back."),
        )
        world.add_row(Handoff, row)
        world.add_row(Lead, lead)
        msgs = MagicMock()
        msgs.scalars.return_value.all.return_value = [
            SimpleNamespace(role="user", content="call me", created_at=WHEN),
            SimpleNamespace(role="assistant", content="Sure.", created_at=WHEN),
        ]
        world.session.execute = AsyncMock(return_value=msgs)
        res = await call(app, "admin", "GET", f"/api/v1/handoffs/{row.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["summary"] == "Wants a call back."
    assert [m["role"] for m in body["transcript"]] == ["user", "assistant"]
    assert body["lead"] == {
        "id": str(lead.id),
        "name": "Priya",
        "phone": "+919876543210",
        "email": None,
    }
    assert res.headers["cache-control"] == "no-store"


async def test_detail_survives_a_bad_token_a_missing_key_and_an_erased_lead() -> None:
    other = FernetEncryptor(Fernet.generate_key().decode())
    with scoped_app() as (app, world):
        row = _row(
            world.corpus_a.id,
            lead_id=uuid.uuid4(),
            summary_encrypted=other.encrypt("sealed under another key"),
        )
        world.add_row(Handoff, row)
        # no key configured at all
        res = await call(app, "admin", "GET", f"/api/v1/handoffs/{row.id}")
        assert res.status_code == 200 and res.json()["summary"] is None
        assert res.json()["lead"] is None
        # a key that cannot read the token
        _with_key(app)
        res = await call(app, "admin", "GET", f"/api/v1/handoffs/{row.id}")
        assert res.status_code == 200 and res.json()["summary"] is None  # lead row erased: None
        # a lead whose token the key cannot read
        broken = SimpleNamespace(id=row.lead_id, fields_encrypted=other.encrypt("{}"))
        world.add_row(Lead, broken)
        res = await call(app, "admin", "GET", f"/api/v1/handoffs/{row.id}")
    assert res.status_code == 200 and res.json()["lead"] is None


async def test_the_list_never_carries_contact_data_or_the_summary() -> None:
    with scoped_app() as (app, world):
        page, total = MagicMock(), MagicMock()
        page.scalars.return_value.all.return_value = [
            _row(world.corpus_a.id, lead_id=uuid.uuid4(), summary_encrypted="x")
        ]
        total.scalar.return_value = 1
        world.session.execute = AsyncMock(side_effect=[page, total])
        res = await call(app, "admin", "GET", "/api/v1/handoffs")
    item = res.json()["items"][0]
    assert not {"summary", "lead", "transcript", "phone", "email"} & set(item)


def test_detail_is_classified_as_a_write_for_keys() -> None:
    from callobuzz_rag.api.permissions import ROUTE_OPS

    assert ROUTE_OPS[("GET", "/api/v1/handoffs")] == "read"
    assert ROUTE_OPS[("GET", "/api/v1/handoffs/{handoff_id}")] == "write"
    assert ROUTE_OPS[("PATCH", "/api/v1/handoffs/{handoff_id}")] == "write"


def test_patch_is_not_owner_only() -> None:
    from tests.unit.scope_harness import is_owner_only

    assert not is_owner_only("PATCH", f"/api/v1/handoffs/{X}", {"status": "handled"})


# TV2 reference
async def test_the_reference_is_on_the_list_the_detail_and_the_update() -> None:
    with scoped_app() as (app, world):
        conv = uuid.uuid4()
        row = _row(world.corpus_a.id, conversation_id=conv)
        bare = _row(world.corpus_a.id)
        world.add_row(Handoff, row)
        world.add_row(Handoff, bare)
        refs = AsyncMock(return_value={conv: "SD-4F7K"})
        with (
            _ids_for(world)("owner_a"),
            patch("callobuzz_rag.api.routes.handoffs.refs_for", refs),
        ):
            detail = await call(app, "owner_a", "GET", f"/api/v1/handoffs/{row.id}")
            patched = await call(
                app, "owner_a", "PATCH", f"/api/v1/handoffs/{row.id}", json={"status": "handled"}
            )
            no_conv = await call(app, "owner_a", "GET", f"/api/v1/handoffs/{bare.id}")
    assert detail.json()["ref_code"] == "SD-4F7K"
    assert patched.json()["ref_code"] == "SD-4F7K"
    assert no_conv.json()["ref_code"] is None


# /TV2 reference
