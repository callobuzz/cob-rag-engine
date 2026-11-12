"""The leads inbox routes with real encryption and a stand-in database (conversation C4)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from cryptography.fernet import Fernet

from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.lead import Lead
from tests.unit.scope_harness import call, scoped_app

KEY = Fernet.generate_key().decode()
CRYPTO = LeadCrypto(FernetEncryptor(KEY))
NOW = datetime(2026, 10, 14, tzinfo=UTC)
HOOK = "callobuzz_rag.api.routes.leads."


def _lead(corpus_id: uuid.UUID, tenant_id: uuid.UUID, **kw: Any) -> Lead:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "corpus_id": corpus_id,
        "tenant_id": tenant_id,
        "conversation_id": None,
        "channel": "widget",
        "status": "new",
        "trigger": "buying_intent",
        "tags": [],
        "language": "en",
        "fields_encrypted": CRYPTO.encrypt_json(
            {"name": "Priya", "phone": "+919876543210", "email": None, "custom": {}}
        ),
        "sources_consulted": [],
        "retention_until": NOW + timedelta(days=365),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(kw)
    return Lead(**values)


def _may_see(*corpus_ids: uuid.UUID) -> Any:
    """The scoping answer for a caller who may see exactly these knowledge bases (the stand-in
    database cannot answer the tenant's corpus query)."""
    return patch(HOOK + "authorized_corpus_ids", AsyncMock(return_value=list(corpus_ids)))


def _configure(app: Any, **update: Any) -> None:
    app.state.settings = app.state.settings.model_copy(update={"encryption_key": KEY, **update})


async def test_list_decrypts_contacts_and_is_never_cached() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(corpus.id, corpus.tenant_id)
        with (
            patch(HOOK + "list_leads", AsyncMock(return_value=([lead], 1))),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={corpus.id: "Patient help"})),
        ):
            res = await call(app, "tk_a", "GET", "/api/v1/leads")
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["contact"]["phone"] == "+919876543210" and item["knowledge_base"] == "Patient help"
    assert res.headers["cache-control"] == "no-store"
    assert res.json()["total"] == 1


async def test_list_filters_are_passed_through() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        listing = AsyncMock(return_value=([], 0))
        with patch(HOOK + "list_leads", listing):
            res = await call(
                app,
                "tk_a",
                "GET",
                f"/api/v1/leads?corpus_id={corpus.id}&status=won&q=9876543210&limit=5&offset=10"
                "&date_from=2026-10-01&page_url=lipid",
            )
    assert res.status_code == 200
    kwargs = listing.await_args.kwargs
    filters = kwargs["filters"]
    assert (filters.status, filters.q, filters.page_url) == ("won", "9876543210", "lipid")
    assert filters.date_from.isoformat() == "2026-10-01"
    assert (kwargs["limit"], kwargs["offset"]) == (5, 10) and kwargs["corpus_ids"] == [corpus.id]


async def test_bad_filters_are_422() -> None:
    with scoped_app() as (app, _):
        _configure(app)
        for query in ("status=archived", "limit=0", "limit=101", "date_from=yesterday"):
            res = await call(app, "tk_a", "GET", f"/api/v1/leads?{query}")
            assert res.status_code == 422, query


async def test_detail_reads_notes_summary_and_never_caches() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(
            corpus.id,
            corpus.tenant_id,
            notes_encrypted=CRYPTO.encrypt_text("call after 6"),
            summary_encrypted=CRYPTO.encrypt_text("Wants a home visit."),
        )
        with (
            _may_see(corpus.id),
            patch.object(world.session, "get", AsyncMock(return_value=lead)),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={corpus.id: "Patient help"})),
        ):
            res = await call(app, "tk_a", "GET", f"/api/v1/leads/{lead.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["notes"] == "call after 6" and body["summary"] == "Wants a home visit."
    assert body["contact"]["name"] == "Priya" and res.headers["cache-control"] == "no-store"


async def test_patch_updates_through_the_service() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(corpus.id, corpus.tenant_id)
        with (
            _may_see(corpus.id),
            patch.object(world.session, "get", AsyncMock(return_value=lead)),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={})),
        ):
            res = await call(
                app,
                "tk_a",
                "PATCH",
                f"/api/v1/leads/{lead.id}",
                json={"status": "contacted", "notes": "rang twice"},
            )
            bad = await call(
                app, "tk_a", "PATCH", f"/api/v1/leads/{lead.id}", json={"status": "archived"}
            )
            extra = await call(
                app, "tk_a", "PATCH", f"/api/v1/leads/{lead.id}", json={"phone": "9"}
            )
    assert res.status_code == 200, res.text
    assert lead.status == "contacted" and lead.contacted_at is not None
    assert CRYPTO.decrypt_text(lead.notes_encrypted) == "rang twice"
    assert bad.status_code == 422 and extra.status_code == 422


async def test_assignee_who_is_not_a_member_is_refused() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(corpus.id, corpus.tenant_id)
        with (
            _may_see(corpus.id),
            patch.object(world.session, "get", AsyncMock(return_value=lead)),
        ):
            res = await call(
                app,
                "tk_a",
                "PATCH",
                f"/api/v1/leads/{lead.id}",
                json={"assignee_user_id": str(uuid.uuid4())},
            )
    assert res.status_code == 422 and res.json()["code"] == "lead_field_invalid"
    assert res.json()["field"] == "assignee_user_id" and res.json()["reason"] == "not_member"


async def test_delete_audits_and_erases() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(corpus.id, corpus.tenant_id)
        with (
            _may_see(corpus.id),
            patch.object(world.session, "get", AsyncMock(return_value=lead)),
            patch(HOOK + "erase_lead", AsyncMock()) as erase,
        ):
            res = await call(app, "owner_a", "DELETE", f"/api/v1/leads/{lead.id}")
        added = [c.args[0] for c in world.session.add.call_args_list]
    assert res.status_code == 204
    erase.assert_awaited_once()
    assert erase.await_args.kwargs["withdrawn"] is False
    audit = next(o for o in added if isinstance(o, AuditLog) and o.action == "lead.deleted")
    assert audit.details == {"corpus_id": str(corpus.id), "lead_id": str(lead.id)}


async def test_export_is_a_csv_audited_without_the_search_text() -> None:
    with scoped_app() as (app, world):
        _configure(app)
        corpus = world.corpus("a")
        lead = _lead(corpus.id, corpus.tenant_id)
        with (
            patch(HOOK + "_count_for_export", AsyncMock(return_value=1)),
            patch(HOOK + "list_leads", AsyncMock(return_value=([lead], 1))),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={corpus.id: "Patient help"})),
        ):
            res = await call(
                app,
                "owner_a",
                "GET",
                f"/api/v1/leads/export.csv?corpus_id={corpus.id}&q=9876543210&status=new",
            )
        added = [c.args[0] for c in world.session.add.call_args_list]
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"].startswith('attachment; filename="leads-')
    assert res.headers["cache-control"] == "no-store"
    assert res.text.startswith("﻿created_at,") and "+919876543210" in res.text
    audit = next(o for o in added if isinstance(o, AuditLog) and o.action == "lead.exported")
    assert audit.details["count"] == 1
    assert audit.details["filters"]["q"] == "set" and "9876543210" not in str(audit.details)


async def test_export_too_large_and_owner_only() -> None:
    with scoped_app() as (app, world):
        _configure(app, lead_export_max_rows=2)
        url = f"/api/v1/leads/export.csv?corpus_id={world.corpus('a').id}"
        with patch(HOOK + "_count_for_export", AsyncMock(return_value=3)):
            res = await call(app, "owner_a", "GET", url)
        assert res.status_code == 413 and res.json()["code"] == "export_too_large"
        assert res.json()["limit"] == 2
        res = await call(app, "member_a", "GET", url)
        assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_no_key_is_503_on_every_read() -> None:
    with scoped_app() as (app, world):
        c = world.corpus("a").id
        for path in (
            "/api/v1/leads",
            f"/api/v1/leads/{uuid.uuid4()}",
            f"/api/v1/leads/export.csv?corpus_id={c}",
        ):
            res = await call(app, "owner_a", "GET", path)
            assert res.status_code == 503, path
            assert res.json()["code"] == "encryption_not_configured"


async def test_a_key_that_cannot_write_cannot_read_leads() -> None:
    """Contact data needs a `write` key; the read-only agent default is refused."""
    from callobuzz_rag.api.permissions import ROUTE_OPS
    from callobuzz_rag.api.permissions import permission_allows as allows

    for route in (
        ("GET", "/api/v1/leads"),
        ("GET", "/api/v1/leads/export.csv"),
        ("GET", "/api/v1/leads/{lead_id}"),
        ("PATCH", "/api/v1/leads/{lead_id}"),
    ):
        assert ROUTE_OPS[route] == "write"
        assert not allows("read", ROUTE_OPS[route]) and allows("write", ROUTE_OPS[route])
    assert ROUTE_OPS[("DELETE", "/api/v1/leads/{lead_id}")] == "delete"


async def _export(key: str, query: str, leads_of: Any) -> tuple[AuditLog, Any]:
    """Run an export as `key`; `leads_of(world)` gives the leads the stand-in returns."""
    with scoped_app() as (app, world):
        _configure(app)
        leads = leads_of(world)
        with (
            patch(HOOK + "_count_for_export", AsyncMock(return_value=len(leads))),
            patch(HOOK + "list_leads", AsyncMock(return_value=(leads, len(leads)))),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={})),
        ):
            res = await call(app, key, "GET", f"/api/v1/leads/export.csv{query(world)}")
        assert res.status_code == 200, res.text
        added = [c.args[0] for c in world.session.add.call_args_list]
        audit = next(o for o in added if isinstance(o, AuditLog) and o.action == "lead.exported")
        return audit, world


async def test_an_empty_export_is_filed_under_the_callers_tenant() -> None:
    audit, world = await _export("owner_a", lambda w: "", lambda w: [])
    assert audit.tenant_id == world.tenant_a.id and audit.details["count"] == 0


async def test_an_admin_export_of_one_knowledge_base_is_filed_under_its_tenant() -> None:
    # the only lead returned belongs to the other tenant: the filing must not follow it
    audit, world = await _export(
        "admin",
        lambda w: f"?corpus_id={w.corpus('b').id}",
        lambda w: [_lead(w.corpus("a").id, w.tenant_a.id)],
    )
    assert audit.tenant_id == world.tenant_b.id


async def test_an_admin_export_across_tenants_names_them_in_the_details() -> None:
    audit, world = await _export(
        "admin",
        lambda w: "",
        lambda w: [
            _lead(w.corpus("b").id, w.tenant_b.id),
            _lead(w.corpus("a").id, w.tenant_a.id),
        ],
    )
    assert audit.tenant_id is None
    assert audit.details["tenant_ids"] == sorted(
        str(t.id) for t in (world.tenant_a, world.tenant_b)
    )
