"""Deleting with an API key needs the item's exact name; a preview says what would go.

A knowledge base, a document and a customer are the deletes an agent or script cannot undo.
With an API key (the admin key, `tk_`, `ck_`; `auth_kind == "api_key"`) the DELETE needs
`?confirm_name=<exact name>`: 422 `confirmation_required` without it, 422 `confirm_mismatch`
with another name, nothing deleted either way. A dashboard session keeps its own confirm
dialog and is not asked. `GET .../delete-preview` changes nothing.
"""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests.unit.scope_harness import ADMIN_KEY, World, call, scoped_app

NAME = "Corpus A"


class _Found:
    """A query result holding exactly one row."""

    def __init__(self, row: Any) -> None:
        self._row = row

    def scalar_one_or_none(self) -> Any:
        return self._row

    def scalars(self) -> "_Found":
        return self

    def all(self) -> list[Any]:
        return [self._row]

    def first(self) -> Any:
        return self._row


# ------------------------------------------------------------------ knowledge base


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_a_key_must_confirm_the_name(key: str) -> None:
    with scoped_app() as (app, world):
        path = f"/api/v1/corpora/{world.corpus_a.id}"
        none = await call(app, key, "DELETE", path)
        blank = await call(app, key, "DELETE", path, params={"confirm_name": "  "})
        wrong = await call(app, key, "DELETE", path, params={"confirm_name": "corpus a"})
        other = await call(app, key, "DELETE", path, params={"confirm_name": "Corpus B"})
    for res in (none, blank):
        assert res.status_code == 422, res.text
        body = res.json()
        assert body["code"] == "confirmation_required"
        assert body["confirm_name"] == NAME  # what to send, so a script can follow the message
        assert "knowledge base" in body["detail"] and "delete-preview" in body["detail"]
    for res in (wrong, other):
        assert res.status_code == 422 and res.json()["code"] == "confirm_mismatch", res.text
    world.session.delete.assert_not_called()
    world.session.commit.assert_not_called()


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_the_exact_name_deletes(key: str) -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            key,
            "DELETE",
            f"/api/v1/corpora/{world.corpus_a.id}",
            params={"confirm_name": f"  {NAME} "},  # outer spaces are forgiven
        )
    assert res.status_code == 204, res.text
    world.session.commit.assert_awaited()


async def test_a_dashboard_session_is_not_asked_again() -> None:
    """The dashboard has its own typed-name dialog; its session needs no confirm_name."""
    with scoped_app() as (app, world):
        res = await call(app, "owner_a", "DELETE", f"/api/v1/corpora/{world.corpus_a.id}")
    assert res.status_code == 204, res.text


async def test_a_members_session_is_still_refused_first() -> None:
    with scoped_app() as (app, world):
        res = await call(app, "member_a", "DELETE", f"/api/v1/corpora/{world.corpus_a.id}")
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_another_tenants_knowledge_base_is_refused_before_the_name() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app,
            "tk_a",
            "DELETE",
            f"/api/v1/corpora/{world.corpus_b.id}",
            params={"confirm_name": "Corpus B"},
        )
    assert res.status_code == 403, res.text


async def test_knowledge_base_preview_counts_and_changes_nothing() -> None:
    with scoped_app() as (app, world):
        res = await call(app, "tk_a", "GET", f"/api/v1/corpora/{world.corpus_a.id}/delete-preview")
        foreign = await call(
            app, "tk_a", "GET", f"/api/v1/corpora/{world.corpus_b.id}/delete-preview"
        )
        member = await call(
            app, "member_a", "GET", f"/api/v1/corpora/{world.corpus_a.id}/delete-preview"
        )
    assert res.status_code == 200, res.text
    assert res.json() == {
        "kind": "knowledge_base",
        "id": str(world.corpus_a.id),
        "name": NAME,
        "confirm_name": NAME,
        "counts": {
            "documents": 0,
            "chunks": 0,
            "widget_keys": 0,
            "conversations": 0,
            "memories": 0,
        },
        "irreversible": True,
    }
    assert foreign.status_code == 403
    assert member.status_code == 200  # a preview deletes nothing, so members may look
    world.session.delete.assert_not_called()
    world.session.commit.assert_not_called()


async def test_preview_counts_what_the_database_holds() -> None:
    """The counts are the database's: one number per kind of thing."""
    with scoped_app() as (app, world):
        answers = iter([7, 120, 2, 5, 3])  # documents, chunks, widget keys, conversations, memories

        class _Count:
            def __init__(self, n: int) -> None:
                self.n = n

            def scalar_one(self) -> int:
                return self.n

        async def execute(stmt: Any, *a: Any, **k: Any) -> Any:
            return _Count(next(answers))

        world.session.execute = execute
        res = await call(app, "tk_a", "GET", f"/api/v1/corpora/{world.corpus_a.id}/delete-preview")
    assert res.json()["counts"] == {
        "documents": 7,
        "chunks": 120,
        "widget_keys": 2,
        "conversations": 5,
        "memories": 3,
    }


# ------------------------------------------------------------------ customer


async def test_deleting_a_customer_needs_its_name_with_a_code() -> None:
    with scoped_app() as (app, world):
        path = f"/api/v1/admin/tenants/{world.tenant_a.id}"
        none = await call(app, "admin", "DELETE", path)
        wrong = await call(app, "admin", "DELETE", path, params={"confirm_name": "Tenant B"})
    assert none.status_code == 422 and none.json()["code"] == "confirmation_required"
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    world.session.commit.assert_not_called()


async def test_customer_preview_lists_what_goes_with_it() -> None:
    with scoped_app() as (app, world):
        res = await call(
            app, "admin", "GET", f"/api/v1/admin/tenants/{world.tenant_a.id}/delete-preview"
        )
        tenant_key = await call(
            app, "tk_a", "GET", f"/api/v1/admin/tenants/{world.tenant_a.id}/delete-preview"
        )
        unknown = await call(
            app, "admin", "GET", f"/api/v1/admin/tenants/{uuid.uuid4()}/delete-preview"
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "customer" and body["name"] == "Tenant A"
    assert body["confirm_name"] == "Tenant A"
    assert set(body["counts"]) == {
        "knowledge_bases",
        "documents",
        "chunks",
        "widget_keys",
        "conversations",
        "memories",
        "members",
        "api_keys",
    }
    assert tenant_key.status_code == 403 and unknown.status_code == 404


# ------------------------------------------------------------------ document


def _document(world: World) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=world.corpus_a.id,
        file_name="refund-policy.pdf",
        external_id=None,
        versions=[],
    )


def _serve(world: World, doc: SimpleNamespace) -> None:
    from tests.unit.scope_harness import _Empty

    async def execute(stmt: Any, *a: Any, **k: Any) -> Any:
        return _Found(doc) if "FROM documents" in str(stmt) else _Empty()

    world.session.execute = execute


@pytest.mark.parametrize("key", ["admin", "tk_a", "ck_a"])
async def test_a_document_delete_needs_its_file_name(key: str) -> None:
    with scoped_app() as (app, world):
        doc = _document(world)
        _serve(world, doc)
        path = f"/api/v1/documents/{doc.id}"
        scope = {"corpus_id": str(world.corpus_a.id)}
        none = await call(app, key, "DELETE", path, params=scope)
        wrong = await call(
            app, key, "DELETE", path, params={**scope, "confirm_name": "refund-policy"}
        )
        right = await call(
            app, key, "DELETE", path, params={**scope, "confirm_name": "refund-policy.pdf"}
        )
    assert none.status_code == 422 and none.json()["code"] == "confirmation_required"
    assert none.json()["confirm_name"] == "refund-policy.pdf"
    assert wrong.status_code == 422 and wrong.json()["code"] == "confirm_mismatch"
    assert right.status_code == 204, right.text
    world.session.delete.assert_awaited_once_with(doc)


async def test_a_records_document_is_named_by_its_record_id() -> None:
    with scoped_app() as (app, world):
        doc = _document(world)
        doc.file_name = None
        doc.external_id = "faq-12"
        _serve(world, doc)
        path = f"/api/v1/documents/{doc.id}?corpus_id={world.corpus_a.id}"
        none = await call(app, "tk_a", "DELETE", path)
    assert none.json()["confirm_name"] == "faq-12"


async def test_a_session_deletes_a_document_without_the_name() -> None:
    with scoped_app() as (app, world):
        doc = _document(world)
        _serve(world, doc)
        res = await call(
            app, "owner_a", "DELETE", f"/api/v1/documents/{doc.id}?corpus_id={world.corpus_a.id}"
        )
    assert res.status_code == 204, res.text


async def test_document_preview() -> None:
    with scoped_app() as (app, world):
        doc = _document(world)
        _serve(world, doc)
        res = await call(
            app,
            "ck_a",
            "GET",
            f"/api/v1/documents/{doc.id}/delete-preview?corpus_id={world.corpus_a.id}",
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["kind"] == "document" and body["name"] == "refund-policy.pdf"
    assert body["confirm_name"] == "refund-policy.pdf"
    assert set(body["counts"]) == {"versions", "chunks"}
    world.session.delete.assert_not_called()


async def test_a_missing_document_is_404_not_a_name_hint() -> None:
    """Nothing is revealed about a document the caller cannot see."""
    with scoped_app() as (app, world):
        res = await call(
            app, "tk_a", "DELETE", f"/api/v1/documents/{uuid.uuid4()}?corpus_id={world.corpus_a.id}"
        )
    assert res.status_code == 404


# ------------------------------------------------------------------ the guard itself


def _request(auth_kind: Any) -> MagicMock:
    request = MagicMock()
    request.state = SimpleNamespace(auth_kind=auth_kind)
    return request


def test_only_api_key_callers_are_guarded() -> None:
    from callobuzz_rag.api.errors import APIError
    from callobuzz_rag.api.services.delete_guard import require_confirm_name

    require_confirm_name(_request("session"), None, "X", "document")  # no error
    require_confirm_name(_request("none"), None, "X", "document")
    require_confirm_name(_request("api_key"), "X", "X", "document")
    with pytest.raises(APIError) as caught:
        require_confirm_name(_request("api_key"), None, "X", "document")
    assert caught.value.status_code == 422 and caught.value.code == "confirmation_required"
    with pytest.raises(APIError) as caught:
        require_confirm_name(_request("api_key"), "Y", "X", "document")
    assert caught.value.code == "confirm_mismatch"


def test_the_admin_key_constant_is_what_the_harness_uses() -> None:
    assert ADMIN_KEY  # the harness maps the "admin" key name to it
