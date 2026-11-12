"""Every Content Portal route takes every key kind, scoped by the shared rule (G-13).

Workspaces and staged content used to accept only a corpus key, and rules had a
hand-written check that refused tenant keys. Now each route authorizes through
`authorized_corpus_ids`: a workspace in the path, `?corpus_id=` on create and list,
the corpus in the path for rules. The route list comes from the app itself, so a new
portal route without an entry here fails `test_every_portal_route_is_in_the_matrix`.
"""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from callobuzz_rag.content_portal.services.ai_chat_service import AIChatService
from tests.unit.scope_harness import MATRIX, NOW, World, call, check, expected, scoped_app

X = uuid.uuid4()  # an id that exists nowhere
P = "/api/v1/portal"
W = P + "/workspaces/{workspace_id}"
USER = {"user": "reviewer@example.com"}

# (method, route template) -> (query string, JSON body). `{workspace_id}` is the
# workspace of the corpus the matrix row names, `{corpus_id}` that corpus, and every
# other id is one that exists nowhere (the route's own 404 is fine: it got past scoping).
ROUTES: dict[tuple[str, str], tuple[str, dict[str, Any] | None]] = {
    # workspaces (7)
    ("POST", P + "/workspaces"): ("?corpus_id={corpus_id}", {"title": "Docs"}),
    ("GET", P + "/workspaces"): ("?corpus_id={corpus_id}", None),
    ("GET", W): ("", None),
    ("PATCH", W): ("", {"title": "Renamed"}),
    ("DELETE", W): ("", None),
    ("POST", W + "/lock"): ("", USER),
    ("POST", W + "/unlock"): ("", USER),
    # staging (4)
    ("POST", W + "/content"): ("", {"source_type": "text_dump", "content": "Some text"}),
    ("GET", W + "/content"): ("", None),
    ("GET", W + "/content/{content_id}"): ("", None),
    ("POST", W + "/content/{content_id}/reprocess"): ("", None),
    # rules (4)
    ("GET", P + "/corpus/{corpus_id}/rules"): ("", None),
    (
        "POST",
        P + "/corpus/{corpus_id}/rules",
    ): ("", {"rule_type": "always", "condition": {}, "action": "stage"}),
    ("PATCH", P + "/corpus/{corpus_id}/rules/{rule_id}"): ("", {"priority": 3}),
    ("DELETE", P + "/corpus/{corpus_id}/rules/{rule_id}"): ("", None),
    # chunks (9)
    ("GET", W + "/chunks"): ("", None),
    ("POST", W + "/chunks"): ("", {"text": "A chunk", **USER}),
    ("PATCH", W + "/chunks/{chunk_id}"): ("", {"text": "Edited", **USER}),
    ("POST", W + "/chunks/{chunk_id}/split"): ("", {"position": 2, **USER}),
    ("POST", W + "/chunks/merge"): ("", {"chunk_ids": [str(X), str(uuid.uuid4())], **USER}),
    ("POST", W + "/chunks/reorder"): ("", {"chunk_ids": [str(X)], **USER}),
    ("DELETE", W + "/chunks/{chunk_id}"): ("?user=reviewer", None),
    ("POST", W + "/chunks/{chunk_id}/approve"): ("", USER),
    ("POST", W + "/chunks/{chunk_id}/reject"): ("", USER),
    # review (5)
    ("POST", W + "/submit"): ("", USER),
    ("POST", W + "/approve"): ("", USER),
    ("POST", W + "/reject"): ("", {"feedback": "Needs work", **USER}),
    ("POST", W + "/ingest"): ("", USER),
    ("GET", W + "/audit-trail"): ("", None),
    # ai_chat (3)
    ("POST", W + "/chat"): ("", {"message": "Hello", **USER}),
    ("GET", W + "/chat/history"): ("", None),
    (
        "POST",
        W + "/chat/execute",
    ): ("", {"action": {"tool_name": "rewrite_chunk", "arguments": {}}, **USER}),
    # post_ingest (3)
    (
        "POST",
        P + "/workspaces/from-ingestion",
    ): ("", {"document_version_id": "{version_id}", **USER}),
    ("POST", W + "/finalize-review"): ("", USER),
    ("GET", W + "/review-status"): ("", None),
}


def _portal_routes() -> set[tuple[str, str]]:
    """(method, path) of every portal route the app serves, from its OpenAPI table.

    FastAPI 0.141 keeps included routers nested in `app.routes`; the schema is the
    flat list (every portal route is in it).
    """
    with scoped_app(portal=True) as (app, _):
        paths = app.openapi()["paths"]
    return {
        (method.upper(), path)
        for path, item in paths.items()
        if path.startswith(P + "/")
        for method in item
    }


def test_every_portal_route_is_in_the_matrix() -> None:
    routes = _portal_routes()
    assert len(routes) == 35
    assert routes == set(ROUTES), (
        f"missing from the matrix: {sorted(routes - set(ROUTES))}; "
        f"no longer served: {sorted(set(ROUTES) - routes)}"
    )


def test_portal_is_off_by_default() -> None:
    with scoped_app() as (app, _):
        assert not any(path.startswith(P + "/") for path in app.openapi()["paths"])


def _document_version(world: World, which: str) -> uuid.UUID:
    """A document version of the named corpus, for from-ingestion."""
    from callobuzz_rag.db.models.document import Document
    from callobuzz_rag.db.models.document_version import DocumentVersion

    doc = SimpleNamespace(id=uuid.uuid4(), corpus_id=world.corpus(which).id)
    version = SimpleNamespace(id=uuid.uuid4(), document_id=doc.id, status="active", created_at=NOW)
    world.add_row(Document, doc)
    world.add_row(DocumentVersion, version)
    return version.id


def _fill(value: Any, ids: dict[str, str]) -> Any:
    if isinstance(value, str):
        return value.format(**ids) if "{" in value else value
    if isinstance(value, dict):
        return {k: _fill(v, ids) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ids) for v in value]
    return value


class _Chat(AIChatService):
    """The AI chat service without its LLM turn: these tests are about who gets in."""

    def __init__(self) -> None:
        super().__init__(llm=AsyncMock())

    async def send_message(self, session: Any, workspace_id: Any, **kwargs: Any) -> Any:
        return {
            "message_id": uuid.uuid4(),
            "conversation_id": uuid.uuid4(),
            "role": "assistant",
            "content": "Hi",
            "created_at": NOW,
        }


@pytest.mark.parametrize("guard", ["router", "handler"])
@pytest.mark.parametrize(("method", "template"), sorted(ROUTES))
@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_scoping(method, template, key, which, outcome, guard, monkeypatch) -> None:
    """`guard="handler"` switches the router-level `authorize_workspace` off: every
    handler must still refuse on its own. A handler that relied on the router guard, or
    fell back to the key's own corpus, would let a foreign workspace through here."""
    from callobuzz_rag.content_portal.routes import ai_chat
    from callobuzz_rag.content_portal.services import staging_service

    monkeypatch.setattr(ai_chat, "_get_chat_service", lambda settings: _Chat())
    monkeypatch.setattr(staging_service, "object_storage", lambda settings: AsyncMock())
    query, body = ROUTES[(method, template)]
    with scoped_app(portal=True) as (app, world):
        if guard == "handler":
            from callobuzz_rag.content_portal.routes._access import authorize_workspace

            app.dependency_overrides[authorize_workspace] = lambda: None
        ids = {
            "workspace_id": str(world.workspace(which).id),
            "corpus_id": str(world.corpus(which).id),
            "version_id": str(_document_version(world, which)),
            "content_id": str(X),
            "chunk_id": str(X),
            "rule_id": str(X),
        }
        url = _fill(template + query, ids)
        res = await call(app, key, method, url, json=_fill(body, ids))
    check(expected(key, outcome, method, url), res.status_code, res.text)


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_admin_and_tenant_name_the_corpus_to_create(key) -> None:
    with scoped_app(portal=True) as (app, _):
        res = await call(app, key, "POST", P + "/workspaces", json={"title": "Docs"})
    assert res.status_code == 422
    assert "corpus_id" in res.json()["detail"]


@pytest.mark.parametrize("key", ["admin", "tk_a"])
async def test_unknown_corpus_is_404(key) -> None:
    with scoped_app(portal=True) as (app, _):
        create = await call(app, key, "POST", f"{P}/workspaces?corpus_id={X}", json={"title": "t"})
        rules = await call(app, key, "GET", f"{P}/corpus/{X}/rules")
    assert create.status_code == 404
    assert rules.status_code == 404


async def test_tenant_listing_an_unknown_corpus_is_404() -> None:
    with scoped_app(portal=True) as (app, _):
        res = await call(app, "tk_a", "GET", f"{P}/workspaces?corpus_id={X}")
    assert res.status_code == 404


@pytest.mark.parametrize("key", ["admin", "tk_a", "ck_a"])
async def test_unknown_workspace_is_404(key) -> None:
    with scoped_app(portal=True) as (app, _):
        res = await call(app, key, "GET", f"{P}/workspaces/{X}")
    assert res.status_code == 404


@pytest.mark.parametrize(
    ("key", "expected"),
    [("tk_a", "tenant"), ("ck_a", "own"), ("admin", "all")],
)
async def test_list_filters_by_scope(key, expected) -> None:
    from callobuzz_rag.content_portal.services import workspace_service

    seen: dict[str, Any] = {}

    async def fake_list(self, session, *, corpus_ids, status=None, offset=0, limit=20):
        seen["corpus_ids"] = corpus_ids
        return [], 0

    with scoped_app(portal=True) as (app, world):
        world.session.execute.side_effect = None
        world.session.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [world.corpus_a.id])
        )
        original = workspace_service.WorkspaceService.list_workspaces
        workspace_service.WorkspaceService.list_workspaces = fake_list  # type: ignore[method-assign]
        try:
            res = await call(app, key, "GET", P + "/workspaces")
        finally:
            workspace_service.WorkspaceService.list_workspaces = original  # type: ignore[method-assign]
    assert res.status_code == 200, res.text
    if expected == "all":
        assert seen["corpus_ids"] is None
    else:
        assert list(seen["corpus_ids"]) == [world.corpus_a.id]


USER_ROUTES = sorted(
    (method, template)
    for (method, template), (query, body) in ROUTES.items()
    if "user=" in query or (body is not None and "user" in body)
)


@pytest.mark.parametrize(("method", "template"), USER_ROUTES)
async def test_user_is_optional(method, template, monkeypatch) -> None:
    """`portal_actor` names who acted (a session's account, else the key's `user`,
    `X-User-Id` or "api_key_user"), so no route requires the caller to type a name."""
    from callobuzz_rag.content_portal.routes import ai_chat

    monkeypatch.setattr(ai_chat, "_get_chat_service", lambda settings: _Chat())
    query, body = ROUTES[(method, template)]
    query = "" if "user=" in query else query
    body = {k: v for k, v in (body or {}).items() if k != "user"} or None
    with scoped_app(portal=True) as (app, world):
        ids = {
            "workspace_id": str(world.workspace("a").id),
            "corpus_id": str(world.corpus("a").id),
            "version_id": str(_document_version(world, "a")),
            "chunk_id": str(X),
        }
        res = await call(app, "ck_a", method, _fill(template + query, ids), json=_fill(body, ids))
    # A request-validation 422 has a list `detail`; a handler's own 422 is a sentence.
    assert not (res.status_code == 422 and isinstance(res.json()["detail"], list)), res.text
    assert res.status_code < 500, res.text
