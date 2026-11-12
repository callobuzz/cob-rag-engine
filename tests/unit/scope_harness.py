"""A real app with two tenants' worth of keys, for scoping tests.

Keys: `admin`, `tk_a`/`tk_b` (tenant A / B), `ck_a`/`ck_b` (corpus A, owned by
tenant A / corpus B, owned by tenant B), and three dashboard sessions (accounts A3):
`sa` (the super admin, the admin principal), `owner_a` (an owner of tenant A) and
`member_a` (a member of tenant A, refused every `OWNER_ONLY` route; accounts A5).
Authentication is injected per key the
way APIKeyAuthMiddleware sets `request.state`; a request with no key goes through
the real middleware (its 401 needs no database). The database is a stand-in
that knows the two tenants and two corpora and answers every query with nothing,
so a route that gets past scoping answers 200 (or its own 404/422), and one that
does not answers 401/403.

`scoped_app(portal=True)` also mounts the Content Portal (off by default, and the
tests never read an env file), and the world then holds one workspace per corpus.
"""

import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.core.config import Settings

ADMIN_KEY = "admin-key-for-scope-tests"
KEYS = ("admin", "tk_a", "tk_b", "ck_a", "ck_b", "sa", "owner_a", "member_a")
NOW = datetime(2026, 9, 30, tzinfo=UTC)


def _tenant(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        description=None,
        status="active",
        is_active=True,  # the model's read-only property, for code reading it
        is_operator=False,
        activated_at=NOW,
        activation_note=None,
        created_by_user_id=None,
        max_corpora=10,
        plan_id=uuid.uuid4(),
        api_key_hash="hash",
        created_at=NOW,
        updated_at=NOW,
    )


def _corpus(name: str, tenant: SimpleNamespace) -> SimpleNamespace:
    cid = uuid.uuid4()
    return SimpleNamespace(
        id=cid,
        name=name,
        description=None,
        tenant_id=tenant.id,
        qdrant_collection=f"corpus_{str(cid)[:8]}",
        llm_config=None,
        prompt_config=None,
        retrieval_config=None,
        embedding_config=None,
        applied_embedding=None,
        api_key_hash="hash",
        created_at=NOW,
    )


def _user(email: str, *, superadmin: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        name=email.split("@")[0],
        is_superadmin=superadmin,
        is_active=True,
        password_hash="$argon2id$stub",
        email_verified_at=NOW,
        created_at=NOW,
    )


def _workspace(corpus: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        title=f"{corpus.name} workspace",
        kind="standard",
        reviewed_version_id=None,
        finalized_version_id=None,
        status="draft",
        workflow_mode="single_reviewer",
        created_by="someone",
        assigned_reviewer=None,
        locked_by=None,
        locked_at=None,
        review_feedback=None,
        created_at=NOW,
        updated_at=NOW,
    )


class _ZeroRow:
    """A one-row aggregate over nothing: every column is 0."""

    def __getattr__(self, name: str) -> int:
        return 0


class _Savepoint:
    """The async context manager a real session.begin_nested() returns."""

    async def __aenter__(self) -> "_Savepoint":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Empty:
    """A query result with no rows, whatever shape the route asks for."""

    def scalars(self) -> "_Empty":
        return self

    def all(self) -> list[Any]:
        return []

    def first(self) -> None:
        return None

    def scalar(self) -> int:
        return 0

    def scalar_one(self) -> int:
        return 0

    def scalar_one_or_none(self) -> None:
        return None

    def one_or_none(self) -> None:
        return None

    def one(self) -> Any:
        return _ZeroRow()

    def mappings(self) -> "_Empty":
        return self

    @property
    def rowcount(self) -> int:
        return 0


class World:
    """Two tenants, a corpus each, and the fake session that knows them."""

    def __init__(self) -> None:
        self.tenant_a = _tenant("Tenant A")
        self.tenant_b = _tenant("Tenant B")
        self.corpus_a = _corpus("Corpus A", self.tenant_a)
        self.corpus_b = _corpus("Corpus B", self.tenant_b)
        self.rows: dict[uuid.UUID, Any] = {
            o.id: o for o in (self.tenant_a, self.tenant_b, self.corpus_a, self.corpus_b)
        }
        self.session = AsyncMock()
        self.session.get = AsyncMock(side_effect=self._get)
        self.session.execute = AsyncMock(side_effect=lambda *a, **k: _Empty())
        self.session.add = MagicMock()
        self.session.delete = AsyncMock()
        self.session.begin_nested = MagicMock(side_effect=lambda: _Savepoint())
        self.session.refresh = AsyncMock(side_effect=self._refresh)
        self.others: dict[tuple[Any, Any], Any] = {}
        self.workspace_a = _workspace(self.corpus_a)
        self.workspace_b = _workspace(self.corpus_b)
        self.superadmin = _user("ops@operator.example", superadmin=True)
        self.owner_a = _user("owner@tenant-a.example")
        self.member_a = _user("member@tenant-a.example")
        self.session_ids = {"sa": uuid.uuid4(), "owner_a": uuid.uuid4(), "member_a": uuid.uuid4()}
        # BIZ3 features: the plan flags of each workspace, and the operator's own workspace
        self.plan_features: dict[uuid.UUID, dict[str, Any]] = {}
        self.operator = _tenant("Operator")
        self.operator.is_operator = True
        # /BIZ3 features

    def add_portal_rows(self) -> None:
        """The two workspaces, as the portal's routes load them."""
        from callobuzz_rag.content_portal.models.workspace import Workspace

        self.add_row(Workspace, self.workspace_a)
        self.add_row(Workspace, self.workspace_b)

    async def _get(self, model: Any, key: Any, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(key, uuid.UUID):  # P2 invoices: the engine settings row has the key 1
            try:
                key = uuid.UUID(str(key))
            except ValueError:
                return self.extra_get(model, key)
        row = self.rows.get(key)
        if row is None:
            return self.extra_get(model, key)
        from callobuzz_rag.db.models.corpus import Corpus
        from callobuzz_rag.db.models.tenant import Tenant

        wanted = {Corpus: "qdrant_collection", Tenant: "max_corpora"}.get(model)
        if wanted is None:
            return self.extra_get(model, key)
        return row if hasattr(row, wanted) else None

    async def _refresh(self, obj: Any, *args: Any, **kwargs: Any) -> None:
        """What the database would have filled in on a row a route just created."""
        for name, value in (("id", uuid.uuid4()), ("created_at", NOW), ("updated_at", NOW)):
            if hasattr(obj, name) and getattr(obj, name) is None:
                setattr(obj, name, value)

    def extra_get(self, model: Any, key: Any) -> Any:
        """Rows of other models a test registers with `add_row`."""
        return self.others.get((model, key))

    def add_row(self, model: Any, row: Any) -> None:
        self.others[(model, row.id)] = row

    def corpus(self, which: str) -> SimpleNamespace:
        return self.corpus_a if which == "a" else self.corpus_b

    def workspace(self, which: str) -> SimpleNamespace:
        return self.workspace_a if which == "a" else self.workspace_b

    def state_for(self, key: str) -> dict[str, Any] | None:
        """Every principal field, as APIKeyAuthMiddleware sets it (README principals)."""
        key_state = {"user": None, "session_id": None, "auth_kind": "api_key"}
        return {
            "admin": {
                "is_admin": True,
                "tenant": None,
                "corpus": None,
                "role": None,
                **key_state,
            },
            "tk_a": {
                "is_admin": False,
                "tenant": self.tenant_a,
                "corpus": None,
                "role": "owner",
                **key_state,
            },
            "tk_b": {
                "is_admin": False,
                "tenant": self.tenant_b,
                "corpus": None,
                "role": "owner",
                **key_state,
            },
            "ck_a": {
                "is_admin": False,
                "tenant": None,
                "corpus": self.corpus_a,
                "role": None,
                **key_state,
            },
            "ck_b": {
                "is_admin": False,
                "tenant": None,
                "corpus": self.corpus_b,
                "role": None,
                **key_state,
            },
            "sa": {
                "is_admin": True,
                "tenant": None,
                "corpus": None,
                "role": None,
                "user": self.superadmin,
                "session_id": self.session_ids["sa"],
                "auth_kind": "session",
            },
            "owner_a": {
                "is_admin": False,
                "tenant": self.tenant_a,
                "corpus": None,
                "role": "owner",
                "user": self.owner_a,
                "session_id": self.session_ids["owner_a"],
                "auth_kind": "session",
            },
            "member_a": {
                "is_admin": False,
                "tenant": self.tenant_a,
                "corpus": None,
                "role": "member",
                "user": self.member_a,
                "session_id": self.session_ids["member_a"],
                "auth_kind": "session",
            },
            # BIZ3 features: the deployer's own workspace (never gated); not in KEYS
            "operator_owner": {
                "is_admin": False,
                "tenant": self.operator,
                "corpus": None,
                "role": "owner",
                "user": self.superadmin,
                "session_id": uuid.uuid4(),
                "auth_kind": "session",
            },
            # /BIZ3 features
        }.get(key)


@contextmanager
def scoped_app(world: World | None = None, portal: bool = False) -> Iterator[tuple[Any, World]]:
    """The real app, with the world's keys and database. Restores the middleware.

    `portal=True` mounts the Content Portal routes (`RAG_ENABLE_CONTENT_PORTAL`)
    and registers the world's workspaces.
    """
    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.api.auth import APIKeyAuthMiddleware
    from callobuzz_rag.api.deps import get_session

    world = world or World()
    flag = {"RAG_ENABLE_CONTENT_PORTAL": "true" if portal else "false"}
    with patch.dict(os.environ, flag):
        app = create_app()
        settings = Settings(_env_file=None, admin_api_key=ADMIN_KEY, llm_api_key="k")
    if portal:
        world.add_portal_rows()
    app.state.settings = settings
    app.state.session_factory = MagicMock()
    app.state.vector_store = AsyncMock()
    app.state.embedder = AsyncMock() if portal else None
    app.state.reranker = None

    original = APIKeyAuthMiddleware.dispatch

    async def dispatch(self: Any, request: Any, call_next: Any) -> Any:
        state = world.state_for(request.headers.get("X-API-Key", ""))
        if state is None:
            return await original(self, request, call_next)
        for name, value in state.items():
            setattr(request.state, name, value)
        # BIZ3 features: the same gate the real middleware applies to a business's session
        request.state.hidden_gates = frozenset()
        if state["auth_kind"] == "session" and state["tenant"] is not None:
            from callobuzz_rag.api.features import gate_session

            tenant = state["tenant"]
            refused = await gate_session(request, tenant, world.plan_features.get(tenant.id))
            if refused is not None:
                return refused
        # /BIZ3 features
        return await call_next(request)

    async def session_override() -> Any:
        yield world.session

    APIKeyAuthMiddleware.dispatch = dispatch  # type: ignore[method-assign]
    app.dependency_overrides[get_session] = session_override
    try:
        yield app, world
    finally:
        APIKeyAuthMiddleware.dispatch = original  # type: ignore[method-assign]


async def call(app: Any, key: str | None, method: str, path: str, **kwargs: Any) -> Any:
    headers = {"X-API-Key": ADMIN_KEY if key == "admin" else key} if key else {}
    headers.update(kwargs.pop("headers", None) or {})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.request(method, path, headers=headers, **kwargs)


# (key, which corpus the request names) -> expected outcome
MATRIX: list[tuple[str | None, str, str]] = [
    ("admin", "b", "allowed"),
    ("tk_a", "a", "allowed"),
    ("tk_a", "b", "forbidden"),
    ("ck_a", "a", "allowed"),
    ("ck_a", "b", "forbidden"),
    ("owner_a", "a", "allowed"),
    ("owner_a", "b", "forbidden"),
    ("member_a", "a", "allowed"),
    ("member_a", "b", "forbidden"),
    ("sa", "b", "allowed"),
    (None, "a", "unauthenticated"),
]

# Every owner-only route (accounts A5; the table in docs/internal/.../A5-teams.md):
# (method, path template, JSON body, needs the portal mounted). `{corpus_id}`,
# `{tenant_id}` and `{workspace_id}` are the named tenant's; `{x}` / `{y}` exist nowhere.
# A new destructive or key-managing route belongs here and behind `require_owner()`.
OWNER_ONLY: list[tuple[str, str, dict[str, Any] | None, bool]] = [
    ("POST", "/api/v1/corpora", {"name": "New KB"}, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}", None, False),
    ("POST", "/api/v1/corpora/{corpus_id}/rotate-key", None, False),
    ("PUT", "/api/v1/corpora/{corpus_id}", {"llm_config": {"model": "gpt-4o-mini"}}, False),
    ("PUT", "/api/v1/corpora/{corpus_id}", {"embedding_config": {"model": "m"}}, False),
    ("PUT", "/api/v1/corpora/{corpus_id}", {"tenant_id": "{tenant_id}"}, False),
    ("DELETE", "/api/v1/documents/{x}?corpus_id={corpus_id}", None, False),
    ("POST", "/api/v1/documents/{x}/replace/{y}?corpus_id={corpus_id}", None, False),
    # accounts A8: retiring the active version and deleting golden pairs, like a delete
    ("POST", "/api/v1/documents/{x}/versions/{y}/restore?corpus_id={corpus_id}", None, False),
    ("DELETE", "/api/v1/golden/{x}", None, False),
    (
        "POST",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        {"name": "Site", "allowed_origins": ["https://a.example"]},
        False,
    ),
    ("PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{x}", {"name": "Renamed"}, False),
    ("POST", "/api/v1/corpora/{corpus_id}/widget-keys/{x}/rotate", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/widget-keys/{x}", None, False),
    ("POST", "/api/v1/tenants/me/rotate-key", None, False),
    # TV4 value: the owner's own numbers
    (
        "PUT",
        "/api/v1/value/settings",
        {
            "lead_value_minor": 1,
            "handoff_value_minor": 1,
            "staff_hour_minor": 1,
            "minutes_per_answer": 3,
        },
        False,
    ),
    # /TV4 value
    # TV3 spend cap: the owner's own spending limit
    ("PUT", "/api/v1/billing/spend-cap", {"cap_minor": 1}, False),
    # /TV3 spend cap
    # conversation layer C2: publishing the assistant (members may edit drafts)
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/publish", {"base_revision": 0}, False),
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/versions/{x}/restore", None, False),
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/enabled", {"enabled": False}, False),
    # conversation layer C8: applying a guided setup publishes the draft
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/setup/{x}/apply", {"base_revision": 1}, False),
    # conversation layer C4: knowledge-base webhooks receive every lead's contact data
    (
        "POST",
        "/api/v1/corpora/{corpus_id}/webhooks",
        {"url": "https://a.example/h", "events": ["lead.created"], "secret": "s" * 20},
        False,
    ),
    ("PATCH", "/api/v1/corpora/{corpus_id}/webhooks/{x}", {"enabled": False}, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/webhooks/{x}", None, False),
    # conversation layer C4: erasing a lead and exporting contact data
    ("DELETE", "/api/v1/leads/{x}", None, False),
    ("GET", "/api/v1/leads/export.csv?corpus_id={corpus_id}", None, False),
    # conversation layer C7: deleting a test set and the judge key
    ("DELETE", "/api/v1/corpora/{corpus_id}/golden-sets/{x}", None, False),
    ("PUT", "/api/v1/corpora/{corpus_id}/eval/settings", {"judge_model": "judge-x"}, False),
    # named API keys (a key's own permission is `full` only; see api/permissions.py)
    ("GET", "/api/v1/corpora/{corpus_id}/keys", None, False),
    ("POST", "/api/v1/corpora/{corpus_id}/keys", {"name": "Agent", "permission": "read"}, False),
    ("PATCH", "/api/v1/corpora/{corpus_id}/keys/{x}", {"name": "Renamed"}, False),
    ("POST", "/api/v1/corpora/{corpus_id}/keys/{x}/rotate", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/keys/{x}", None, False),
    ("GET", "/api/v1/tenants/me/keys", None, False),
    ("POST", "/api/v1/tenants/me/keys", {"name": "Agent", "permission": "read"}, False),
    ("PATCH", "/api/v1/tenants/me/keys/{x}", {"name": "Renamed"}, False),
    ("POST", "/api/v1/tenants/me/keys/{x}/rotate", None, False),
    ("DELETE", "/api/v1/tenants/me/keys/{x}", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories?user_id=u", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/memories/purge", None, False),
    ("DELETE", "/api/v1/portal/workspaces/{workspace_id}", None, True),
    # team writes
    ("POST", "/api/v1/tenants/me/members", {"email": "new@a.example", "role": "member"}, False),
    ("DELETE", "/api/v1/tenants/me/invitations/{x}", None, False),
    ("POST", "/api/v1/tenants/me/invitations/{x}/resend", None, False),
    ("PATCH", "/api/v1/tenants/me/members/{x}", {"role": "owner"}, False),
    ("DELETE", "/api/v1/tenants/me/members/{x}", None, False),  # someone else: owners only
    # BIL3: the workspace's transactions (money is shown to owners only)
    ("GET", "/api/v1/billing/ledger", None, False),
    # BIL6: the workspace's statements (money is shown to owners only)
    ("GET", "/api/v1/billing/statements", None, False),
    ("GET", "/api/v1/billing/statements/2026-10", None, False),
    ("POST", "/api/v1/billing/top-up-requests", {}, False),
    # /BIL6
    # P1 payments: who the tax invoices are made out to (owners only)
    ("GET", "/api/v1/billing/tax-details", None, False),
    (
        "PUT",
        "/api/v1/billing/tax-details",
        {
            "legal_name": "Sharma Furniture",
            "address_line1": "Paud Road",
            "city": "Pune",
            "state_code": "27",
        },
        False,
    ),
    (
        "POST",
        "/api/v1/billing/payments/orders/{x}/verify",
        {"provider_payment_id": "pay_test0001", "signature": "0" * 64},
        False,
    ),
    ("GET", "/api/v1/billing/payments/config", None, False),
    ("GET", "/api/v1/billing/payments", None, False),
    ("POST", "/api/v1/billing/payments/quote", {"purpose": "top_up", "amount_minor": 50000}, False),
    (
        "POST",
        "/api/v1/billing/payments/orders",
        {"purpose": "top_up", "amount_minor": 50000, "idempotency_key": "scope-test-0001"},
        False,
    ),
    # /P1 payments
    # P5 plans: buying from the balance and stopping a monthly add-on (money: owners only)
    ("POST", "/api/v1/billing/add-ons/{x}/buy", {"idempotency_key": "scope-test-0001"}, False),
    ("POST", "/api/v1/billing/add-ons/grants/{x}/cancel", None, False),
    # /P5 plans
    # P6 plan choice: the plan catalogue with what a move costs, a move and calling one off
    ("GET", "/api/v1/billing/plans", None, False),
    ("POST", "/api/v1/billing/plan", {"plan_id": "00000000-0000-0000-0000-000000000001"}, False),
    ("DELETE", "/api/v1/billing/plan/pending", None, False),
    # /P6 plan choice
    # P2 invoices: tax invoices (owners only)
    ("GET", "/api/v1/billing/invoices", None, False),
    ("GET", "/api/v1/billing/invoices/{x}/pdf", None, False),
    # /P2 invoices
    # BIZ1 website: what the assistant learns from the site, and which pages stay out
    ("PATCH", "/api/v1/corpora/{corpus_id}/website", {"enabled": False}, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/website/sources/{x}", None, False),
    ("PATCH", "/api/v1/corpora/{corpus_id}/website/pages/{x}", {"excluded": True}, False),
    # /BIZ1 website
    # BIZ2 website: the push link
    ("POST", "/api/v1/corpora/{corpus_id}/website/learn-key", None, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/website/learn-key", None, False),
    # /BIZ2 website
    # BIZ4: the assistant's picture (a multipart route; the harness sends none, which is enough)
    ("POST", "/api/v1/corpora/{corpus_id}/assistant/avatar", None, False),
    # the first-run steps are a workspace's own routes; the body is refused before any step runs
    ("POST", "/api/v1/onboarding/website", {"website": "localhost"}, False),
    ("PATCH", "/api/v1/onboarding", {"status": "dismissed"}, False),
    # /BIZ4
    # TV6 whatsapp: connecting, configuring and disconnecting the business's number
    (
        "POST",
        "/api/v1/corpora/{corpus_id}/whatsapp/embedded-signup",
        {"code": "c", "phone_number_id": "1234567890", "waba_id": "5550001"},
        False,
    ),
    ("PATCH", "/api/v1/corpora/{corpus_id}/whatsapp", {"human_pause_hours": 6}, False),
    ("PUT", "/api/v1/corpora/{corpus_id}/whatsapp/documents", {"document_ids": []}, False),
    ("DELETE", "/api/v1/corpora/{corpus_id}/whatsapp", None, False),
    # /TV6 whatsapp
]


def _owner_only_pattern(template: str) -> "re.Pattern[str]":
    path = template.split("?")[0]
    return re.compile("^" + re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(path)) + "$")


def is_owner_only(method: str, path: str, body: dict[str, Any] | None = None) -> bool:
    """Whether (method, concrete path, body) is one of `OWNER_ONLY`. For `PUT /corpora/{id}`
    it depends on the body (members may change prompts and retrieval)."""
    path = path.split("?")[0]
    for m, template, owner_body, _ in OWNER_ONLY:
        if m != method or not _owner_only_pattern(template).match(path):
            continue
        if m == "PUT":
            return bool(body) and bool(set(body) & set(owner_body or {}))
        return True
    return False


def expected(key: str | None, outcome: str, method: str, path: str, body: Any = None) -> str:
    """The matrix outcome, except that a member is refused an owner-only route."""
    if key == "member_a" and outcome == "allowed" and is_owner_only(method, path, body):
        return "owner_only"
    return outcome


def check(outcome: str, status: int, body: str = "") -> None:
    if outcome == "owner_only":
        assert status == 403, f"expected 403 owner_required, got {status}: {body[:300]}"
        assert '"owner_required"' in body, body[:300]
    elif outcome == "allowed":
        assert status not in (401, 403), f"refused ({status}): {body[:300]}"
        assert status < 500, f"server error ({status}): {body[:300]}"
    elif outcome == "forbidden":
        assert status == 403, f"expected 403, got {status}: {body[:300]}"
    else:
        assert status == 401, f"expected 401, got {status}: {body[:300]}"
