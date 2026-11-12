"""Every route is classified, and the classification is enforced in the auth layer.

`ROUTE_OPS` (api/permissions.py) is the one table. The completeness test walks the app's
OpenAPI paths (FastAPI keeps included routers nested, so the schema is the flat list), with
the Content Portal mounted, and fails on a route that is in no row and on a row for a route
that no longer exists. The enforcement tests send real requests through the real middleware.
"""

import os
from unittest.mock import patch

import pytest

from callobuzz_rag.api.permissions import (
    PUBLIC_ROUTES,
    ROUTE_OPS,
    check_key_permission,
    needed_permission,
    permission_allows,
    required_operation,
)
from callobuzz_rag.core.secrets import hash_api_key
from callobuzz_rag.db.models.api_key import ApiKey
from callobuzz_rag.db.models.corpus import Corpus
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

METHODS = {"get", "post", "put", "patch", "delete"}


def _served_routes() -> set[tuple[str, str]]:
    from callobuzz_rag.api.app import create_app

    with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "true"}):
        app = create_app()
    return {
        (method.upper(), path)
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method in METHODS
    }


def test_every_authenticated_route_is_classified() -> None:
    served = _served_routes() - PUBLIC_ROUTES
    missing = sorted(served - set(ROUTE_OPS))
    stale = sorted(set(ROUTE_OPS) - served)
    assert not missing, f"routes with no row in ROUTE_OPS (api/permissions.py): {missing}"
    assert not stale, f"rows in ROUTE_OPS for routes that are gone: {stale}"


def test_public_routes_are_not_classified() -> None:
    assert not (PUBLIC_ROUTES & set(ROUTE_OPS))


def test_the_public_list_matches_what_the_middleware_lets_through() -> None:
    from callobuzz_rag.api.auth import OPEN_PREFIXES, PUBLIC_PATHS, WIDGET_PREFIXES

    for _method, path in PUBLIC_ROUTES:
        assert path in PUBLIC_PATHS or path.startswith(OPEN_PREFIXES + WIDGET_PREFIXES), path


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/query"),
        ("POST", "/api/v1/query/stream"),
        ("POST", "/api/v1/query/retrieve"),
        ("GET", "/api/v1/documents"),
        ("GET", "/api/v1/corpora/{corpus_id}"),
        ("POST", "/api/v1/corpora/{corpus_id}/conversations/{conversation_id}/messages"),
        ("POST", "/api/v1/corpora/{corpus_id}/memories/search"),
        ("GET", "/api/v1/corpora/{corpus_id}/delete-preview"),
        ("GET", "/api/v1/documents/{document_id}/delete-preview"),
    ],
)
def test_asking_listing_and_previewing_are_read(method: str, path: str) -> None:
    assert ROUTE_OPS[(method, path)] == "read"


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    [
        ("POST", "/api/v1/ingest", "write"),
        ("POST", "/api/v1/ingest/records", "write"),
        ("PUT", "/api/v1/corpora/{corpus_id}", "write"),
        ("PATCH", "/api/v1/config", "write"),
        ("POST", "/api/v1/corpora", "write"),
        ("POST", "/api/v1/corpora/{corpus_id}/assistant/rules/improve", "write"),
        ("DELETE", "/api/v1/corpora/{corpus_id}", "delete"),
        ("DELETE", "/api/v1/documents/{document_id}", "delete"),
        ("POST", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}/rotate", "delete"),
        ("POST", "/api/v1/corpora/{corpus_id}/rotate-key", "manage"),
        ("POST", "/api/v1/tenants/me/rotate-key", "manage"),
        ("GET", "/api/v1/corpora/{corpus_id}/keys", "manage"),
        ("DELETE", "/api/v1/tenants/me/keys/{key_id}", "manage"),
        ("POST", "/api/v1/tenants/me/members", "manage"),
        ("DELETE", "/api/v1/admin/tenants/{tenant_id}", "manage"),
    ],
)
def test_changes_deletes_and_key_management(method: str, path: str, operation: str) -> None:
    assert ROUTE_OPS[(method, path)] == operation


def test_the_permission_ladder() -> None:
    allowed = {
        ("read", "read"): True,
        ("read", "write"): False,
        ("read", "delete"): False,
        ("read", "manage"): False,
        ("write", "read"): True,
        ("write", "write"): True,
        ("write", "delete"): False,
        ("write", "manage"): False,
        ("full", "read"): True,
        ("full", "write"): True,
        ("full", "delete"): True,
        ("full", "manage"): True,
    }
    for (permission, operation), expected in allowed.items():
        assert permission_allows(permission, operation) is expected, (permission, operation)
    assert not permission_allows("admin", "read")  # an unknown permission allows nothing
    assert [needed_permission(o) for o in ("read", "write", "delete", "manage")] == [
        "read",
        "write",
        "full",
        "full",
    ]


def test_matching_by_concrete_path() -> None:
    assert required_operation("POST", "/api/v1/query") == "read"
    assert required_operation("post", "/api/v1/query/") == "read"
    assert required_operation("HEAD", "/api/v1/documents") == "read"
    assert required_operation("DELETE", "/api/v1/documents/3f1c") == "delete"
    assert required_operation("POST", "/api/v1/corpora/9/widget-keys/2/rotate") == "delete"
    # `/query/{query_id}` is a GET; the literal `/query/stream` POST is not mistaken for it
    assert required_operation("GET", "/api/v1/query/abc") == "read"
    assert required_operation("POST", "/api/v1/query/stream") == "read"


def test_an_unknown_route_needs_full() -> None:
    assert required_operation("GET", "/api/v1/not-a-route") == "manage"
    assert required_operation("TRACE", "/api/v1/query") == "manage"
    assert check_key_permission("write", "GET", "/api/v1/not-a-route") == "full"
    assert check_key_permission("full", "GET", "/api/v1/not-a-route") is None


def test_check_key_permission_names_what_is_needed() -> None:
    assert check_key_permission("read", "POST", "/api/v1/ingest") == "write"
    assert check_key_permission("read", "DELETE", "/api/v1/documents/1") == "full"
    assert check_key_permission("write", "DELETE", "/api/v1/documents/1") == "full"
    assert check_key_permission("write", "POST", "/api/v1/ingest") is None
    assert check_key_permission("read", "GET", "/api/v1/documents") is None


# ------------------------------------------------------------------ through the real app


class Keyed:
    """A customer with a knowledge base and one key of each permission on each."""

    def __init__(self) -> None:
        self.world = World()
        self.tenant = self.world.shree
        self.corpus = self.world.db.add(
            Corpus(
                name="Support FAQ",
                tenant_id=self.tenant.id,
                qdrant_collection="corpus_abcd1234",
                api_key_hash="",
            )
        )
        self.keys: dict[str, str] = {}
        for permission in ("read", "write", "full"):
            ck = f"ck_abcd1234_{permission}key"
            tk = f"tk_{str(self.tenant.id)[:8]}_{permission}key"
            for owner, plaintext in ((self.corpus, ck), (self.tenant, tk)):
                self.world.db.add(
                    ApiKey(
                        corpus_id=owner.id if plaintext.startswith("ck") else None,
                        tenant_id=owner.id if plaintext.startswith("tk") else None,
                        id_short=plaintext.split("_")[1],
                        lookup=None,
                        name=f"{permission} key",
                        key_prefix="x...",
                        key_hash=hash_api_key(plaintext),
                        permission=permission,
                        is_default=False,
                    )
                )
                self.keys[f"{plaintext[:2]}_{permission}"] = plaintext


def _denied(res: object, permission: str, required: str) -> None:
    assert res.status_code == 403, res.text  # type: ignore[attr-defined]
    assert res.json() == {  # type: ignore[attr-defined]
        "detail": f"This API key has {permission} permission; this needs {required}",
        "code": "key_permission_denied",
        "permission": permission,
        "required": required,
    }


@pytest.mark.parametrize("kind", ["ck", "tk"])
async def test_a_read_key_reads_and_is_refused_everything_else(kind: str) -> None:
    k = Keyed()
    key = k.keys[f"{kind}_read"]
    cid = k.corpus.id
    with auth_app(k.world) as app:
        whoami = await req(app, "GET", "/api/v1/whoami", key=key)
        ingest = await req(app, "POST", "/api/v1/ingest", key=key)
        replace = await req(app, "PUT", f"/api/v1/corpora/{cid}", key=key, json={})
        delete = await req(app, "DELETE", f"/api/v1/documents/{cid}", key=key)
        rotate = await req(app, "POST", f"/api/v1/corpora/{cid}/rotate-key", key=key)
        keys = await req(app, "GET", f"/api/v1/corpora/{cid}/keys", key=key)
    assert whoami.status_code == 200
    _denied(ingest, "read", "write")
    _denied(replace, "read", "write")
    _denied(delete, "read", "full")
    _denied(rotate, "read", "full")
    _denied(keys, "read", "full")


async def test_a_refused_request_does_nothing() -> None:
    """Refused in the middleware: the route never ran, so no row was written."""
    k = Keyed()
    before = len(k.world.db.all(ApiKey))
    with auth_app(k.world) as app:
        res = await req(
            app,
            "POST",
            f"/api/v1/corpora/{k.corpus.id}/keys",
            key=k.keys["ck_read"],
            json={"name": "Escalate", "permission": "full"},
        )
    _denied(res, "read", "full")
    assert len(k.world.db.all(ApiKey)) == before
    assert k.world.audits("api_key.created") == []


async def test_a_write_key_can_change_content_settings_but_not_delete_or_manage() -> None:
    k = Keyed()
    key = k.keys["tk_write"]
    cid = k.corpus.id
    with auth_app(k.world) as app:
        refused_delete = await req(app, "DELETE", f"/api/v1/corpora/{cid}", key=key)
        refused_keys = await req(
            app, "POST", "/api/v1/tenants/me/keys", key=key, json={"name": "x"}
        )
        refused_members = await req(app, "GET", "/api/v1/tenants/me/members", key=key)
        passed = await req(app, "POST", "/api/v1/ingest", key=key)
    _denied(refused_delete, "write", "full")
    _denied(refused_keys, "write", "full")
    _denied(refused_members, "write", "full")
    # past the permission check, the route's own answer (no file sent: 422, not 403)
    assert passed.status_code == 422, passed.text


async def test_a_full_key_is_not_refused_by_permission() -> None:
    k = Keyed()
    key = k.keys["tk_full"]
    with auth_app(k.world) as app:
        keys = await req(app, "GET", "/api/v1/tenants/me/keys", key=key)
        ingest = await req(app, "POST", "/api/v1/ingest", key=key)
    assert keys.status_code == 200
    assert ingest.status_code != 403


async def test_the_admin_key_and_sessions_are_not_keys_with_a_permission() -> None:
    k = Keyed()
    asha = k.world.user("asha@shree.example")
    k.world.member(asha, k.tenant, "owner")
    from tests.unit.test_auth_routes import login

    with auth_app(k.world) as app:
        token = (await login(app, asha.email)).json()["access_token"]
        admin = await req(app, "GET", f"/api/v1/admin/tenants/{k.tenant.id}/keys", key=ADMIN)
        session = await req(app, "GET", "/api/v1/tenants/me/keys", token=token)
        ingest = await req(app, "POST", "/api/v1/ingest", token=token)
    assert admin.status_code == 200 and session.status_code == 200
    assert ingest.status_code != 403  # 422: no file, but the permission layer is not involved


async def test_query_routes_are_open_to_a_read_key() -> None:
    k = Keyed()
    with auth_app(k.world) as app:
        results = [
            await req(app, "POST", path, key=k.keys["ck_read"], json={})
            for path in ("/api/v1/query", "/api/v1/query/stream", "/api/v1/query/retrieve")
        ]
    # an empty body is the route's own 422; a 403 would be the permission layer refusing
    assert [r.status_code for r in results] == [422, 422, 422]
