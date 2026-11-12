"""COB's built-in workspace and knowledge base: created once, protected, never listed."""

import re
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.permissions import ROUTE_OPS
from callobuzz_rag.cob.system import (
    SYSTEM_COLLECTION,
    ensure_cob_system,
    get_system_corpus,
    get_system_tenant,
    is_system_corpus,
    refuse_system_corpus,
    refuse_system_tenant,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan import Plan
from callobuzz_rag.db.models.tenant import Tenant
from tests.unit.accounts_fakes import FakeDB
from tests.unit.scope_harness import World, call, scoped_app

PROTECTED = "system_resource_protected"


# --------------------------------------------------------------- creation


async def test_ensure_is_idempotent() -> None:
    db = FakeDB()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    session = db.session()
    first = await ensure_cob_system(session, settings=settings, lock=False)  # type: ignore[arg-type]
    second = await ensure_cob_system(session, settings=settings, lock=False)  # type: ignore[arg-type]
    assert first.created and not second.created
    assert first.corpus_id == second.corpus_id and first.tenant_id == second.tenant_id
    assert len([t for t in db.all(Tenant) if t.is_system]) == 1
    assert len([c for c in db.all(Corpus) if c.is_system]) == 1


async def test_system_rows_have_the_documented_shape() -> None:
    db = FakeDB()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    await ensure_cob_system(db.session(), settings=settings, lock=False)  # type: ignore[arg-type]
    tenant = await get_system_tenant(db.session())  # type: ignore[arg-type]
    corpus = await get_system_corpus(db.session())  # type: ignore[arg-type]
    assert tenant is not None and corpus is not None
    plan = next(p for p in db.all(Plan) if p.id == tenant.plan_id)
    assert plan.is_internal  # unlimited and locked
    assert tenant.status == "active" and not tenant.is_operator
    assert corpus.tenant_id == tenant.id and corpus.qdrant_collection == SYSTEM_COLLECTION
    assert corpus.llm_config is None
    assert corpus.applied_embedding == {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model,
        "dimensions": settings.embedding_dimensions,
    }


async def test_ensure_creates_the_collection_once() -> None:
    db = FakeDB()
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    calls: list[tuple[str, int]] = []

    class Store:
        async def create_collection(self, name: str, dims: int) -> None:
            calls.append((name, dims))

    for _ in range(2):
        await ensure_cob_system(  # type: ignore[arg-type]
            db.session(),
            settings=settings,
            vector_store=Store(),
            lock=False,  # type: ignore[arg-type]
        )
    assert calls == [(SYSTEM_COLLECTION, settings.embedding_dimensions)]


async def test_a_failing_collection_create_does_not_stop_start_up() -> None:
    db = FakeDB()

    class Broken:
        async def create_collection(self, name: str, dims: int) -> None:
            raise ConnectionError("qdrant is down")

    out = await ensure_cob_system(  # type: ignore[arg-type]
        db.session(),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        vector_store=Broken(),  # type: ignore[arg-type]
        lock=False,
    )
    assert out.created


def test_refusals() -> None:
    refuse_system_corpus(SimpleNamespace(is_system=False))
    refuse_system_corpus(SimpleNamespace())  # a row without the flag is a normal one
    refuse_system_tenant(SimpleNamespace(is_system=False))
    for fn in (refuse_system_corpus, refuse_system_tenant):
        row = SimpleNamespace(is_system=True)
        with pytest.raises(APIError) as caught:
            fn(row)
        assert caught.value.status_code == 409 and caught.value.code == PROTECTED
        assert "built-in COB workspace" in caught.value.detail
    assert is_system_corpus(SimpleNamespace(is_system=True))


# ------------------------------------------------------------------ routes


def with_system(world: World) -> SimpleNamespace:
    """Add COB's workspace and knowledge base to the harness world."""
    tenant = SimpleNamespace(**{**vars(world.tenant_a), "id": uuid.uuid4(), "name": "COB"})
    tenant.is_system = True
    corpus = SimpleNamespace(**{**vars(world.corpus_a), "id": uuid.uuid4(), "name": "COB docs"})
    corpus.tenant_id = tenant.id
    corpus.is_system = True
    corpus.qdrant_collection = SYSTEM_COLLECTION
    world.rows[tenant.id] = tenant
    world.rows[corpus.id] = corpus
    return SimpleNamespace(tenant=tenant.id, corpus=corpus.id)


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/api/v1/corpora/{sys}", {"name": "x"}),
        ("DELETE", "/api/v1/corpora/{sys}", None),
        ("POST", "/api/v1/corpora/{sys}/rotate-key", None),
        ("POST", "/api/v1/corpora/{sys}/keys", {"name": "k", "permission": "read"}),
        (
            "POST",
            "/api/v1/corpora/{sys}/widget-keys",
            {"name": "Site", "allowed_origins": ["https://a.example"]},
        ),
        ("PATCH", "/api/v1/corpora/{sys}/assistant/draft", {}),
        ("POST", "/api/v1/corpora/{sys}/assistant/publish", {"base_revision": 0}),
    ],
)
async def test_admin_cannot_write_the_system_kb(method: str, path: str, body: Any) -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        for key in ("admin", "sa"):
            r = await call(app, key, method, path.format(sys=ids.corpus), json=body)
            assert r.status_code == 409 and r.json()["code"] == PROTECTED, (key, r.text)


async def test_admin_can_read_the_system_kb() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        r = await call(app, "admin", "GET", f"/api/v1/corpora/{ids.corpus}")
        assert r.status_code == 200
        r = await call(app, "admin", "GET", f"/api/v1/corpora/{ids.corpus}/keys")
        assert r.status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/api/v1/admin/tenants/{sys}", {"name": "Renamed"}),
        ("DELETE", "/api/v1/admin/tenants/{sys}?confirm_name=COB", None),
        ("POST", "/api/v1/admin/tenants/{sys}/suspend", {"confirm_name": "COB"}),
        ("POST", "/api/v1/admin/tenants/{sys}/reactivate", None),
        ("POST", "/api/v1/admin/tenants/{sys}/activate", {}),
        ("POST", "/api/v1/admin/tenants/{sys}/decline", {}),
        ("POST", "/api/v1/admin/tenants/{sys}/rotate-key", None),
        ("POST", "/api/v1/admin/tenants/{sys}/plan", {"plan_id": str(uuid.uuid4())}),
        ("POST", "/api/v1/admin/tenants/{sys}/keys", {"name": "k", "permission": "read"}),
        ("POST", "/api/v1/admin/tenants/{sys}/invitations", {"email": "a@b.in", "role": "owner"}),
        ("PATCH", "/api/v1/admin/tenants/{sys}/members/{x}", {"role": "member"}),
        ("DELETE", "/api/v1/admin/tenants/{sys}/members/{x}", None),
    ],
)
async def test_system_tenant_is_protected(method: str, path: str, body: Any) -> None:
    with scoped_app() as (app, world):
        app.state.settings.session_secret = SecretStr("s" * 40)  # accounts are on
        ids = with_system(world)
        url = path.format(sys=ids.tenant, x=uuid.uuid4())
        r = await call(app, "admin", method, url, json=body)
        assert r.status_code == 409 and r.json()["code"] == PROTECTED, r.text


async def test_creating_a_knowledge_base_for_the_system_workspace_is_refused() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        r = await call(
            app,
            "admin",
            "POST",
            "/api/v1/corpora",
            json={"name": "x", "tenant_id": str(ids.tenant)},
        )
        assert r.status_code == 409 and r.json()["code"] == PROTECTED, r.text


async def test_lists_and_counts_skip_the_system_rows() -> None:
    """Every list or count the dashboard shows filters `is_system` in SQL."""
    with scoped_app() as (app, world):
        with_system(world)
        seen: list[str] = []
        original = world.session.execute.side_effect

        async def record(stmt: Any, *args: Any, **kwargs: Any) -> Any:
            seen.append(str(stmt.compile(compile_kwargs={"literal_binds": False})))
            return original(stmt, *args, **kwargs)

        world.session.execute.side_effect = record
        for path in ("/api/v1/corpora", "/api/v1/admin/tenants", "/api/v1/ingest/jobs"):
            seen.clear()
            r = await call(app, "admin", "GET", path)
            assert r.status_code == 200, (path, r.text)
            sql = " ".join(seen)
            assert "is_system" in sql, f"{path} does not exclude the system rows: {sql[:300]}"


# The sweep: every route that is not a plain read and names a knowledge base in its path.
SAMPLE_BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys"): {
        "name": "Site",
        "allowed_origins": ["https://a.example"],
    },
    ("PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}"): {"name": "Renamed"},
}


def corpus_write_routes() -> list[tuple[str, str]]:
    return sorted(
        (m, p)
        for (m, p), op in ROUTE_OPS.items()
        if op != "read" and "{corpus_id}" in p and not p.startswith("/api/v1/portal")
    )


@pytest.mark.parametrize(("method", "template"), corpus_write_routes())
async def test_no_write_route_reaches_the_system_kb(method: str, template: str) -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        path = re.sub(r"\{(?!corpus_id)\w+\}", lambda _m: str(uuid.uuid4()), template)
        path = path.replace("{corpus_id}", str(ids.corpus))
        body = SAMPLE_BODIES.get((method, template), {})
        r = await call(app, "admin", method, path, json=body)
        if method == "GET":  # a read by method: never refused as protected (a list answers 200,
            # a detail route such as a WhatsApp chat answers 404 for the id made up here)
            assert r.status_code in (200, 404), r.text
            assert r.json().get("code") != PROTECTED, r.text
            return
        assert r.status_code == 409 and r.json().get("code") == PROTECTED, (
            f"{method} {template} -> {r.status_code} {r.text[:200]}"
        )


async def test_customers_per_plan_leave_the_system_workspace_out() -> None:
    from callobuzz_rag.accounts.plans import tenant_counts

    seen: list[str] = []

    class Session:
        async def execute(self, stmt: Any) -> Any:
            seen.append(str(stmt.compile()))
            return SimpleNamespace(all=list)

    await tenant_counts(Session(), [uuid.uuid4()])  # type: ignore[arg-type]
    assert "is_system" in seen[0]


class _SessionCtx:
    """`app.state.session_factory()` for routes that open their own session."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.mark.parametrize(
    ("method", "path", "body", "query"),
    [
        ("POST", "/api/v1/ingest/records", {"records": [{"id": "r1", "text": "t"}]}, "corpus"),
        ("POST", "/api/v1/golden", {"query": "q", "expected_answer": "a"}, "body"),
        ("POST", "/api/v1/admin/eval/trigger", {}, "body"),
        ("POST", "/api/v1/admin/optimize/trigger", {}, "body"),
        ("POST", "/api/v1/admin/gaps/trigger", {}, "corpus"),
        ("POST", "/api/v1/analytics/gaps/run", {}, "corpus"),
        ("PATCH", "/api/v1/config", {"retrieval_config": {"top_k_final": 5}}, "corpus"),
        ("POST", "/api/v1/portal/workspaces", {"title": "T"}, "corpus"),
    ],
)
async def test_routes_that_name_the_kb_outside_the_path_are_protected(
    method: str, path: str, body: dict[str, Any], query: str
) -> None:
    with scoped_app(portal=True) as (app, world):
        ids = with_system(world)
        app.state.session_factory = lambda: _SessionCtx(world.session)
        url = path
        payload = dict(body)
        if query == "corpus":
            url = f"{path}?corpus_id={ids.corpus}" if "ingest" not in path else path
            if "ingest" in path:
                payload["corpus_id"] = str(ids.corpus)
            if path.endswith(("/trigger", "/run")):
                payload = {}
        else:
            payload["corpus_id"] = str(ids.corpus)
        r = await call(app, "admin", method, url, json=payload)
        assert r.status_code == 409 and r.json().get("code") == PROTECTED, (
            f"{method} {path} -> {r.status_code} {r.text[:200]}"
        )


async def test_uploading_to_the_system_kb_is_refused() -> None:
    with scoped_app() as (app, world):
        ids = with_system(world)
        r = await call(
            app,
            "admin",
            "POST",
            "/api/v1/ingest",
            files={"file": ("a.txt", b"hello there", "text/plain")},
            data={"corpus_id": str(ids.corpus)},
        )
        assert r.status_code == 409 and r.json().get("code") == PROTECTED, r.text
