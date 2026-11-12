"""API Key authentication middleware.

Authenticates every non-public request via X-API-Key header. Resolves to
admin access (kernel-wide), a tenant (multi-corpus scope), or a specific
corpus (scoped access).

Flow:
1. Skip auth for PUBLIC_PATHS (health, docs, OpenAPI spec) and PUBLIC_PREFIXES
   (widget routes, which check their own pk_ key; /api/v1/public/, which needs none)
2. Extract X-API-Key header — 401 if missing
3. Check admin key via constant_time_compare — if match, set is_admin=True
4. Check tenant keys (tk_ prefix) — bcrypt verify, set tenant
5. Check corpus keys (ck_ prefix) — bcrypt verify, set corpus
6. If no match found — 401

A key is one row of `api_keys` (many named keys per knowledge base and per customer, each
with a permission). It is found by the id segment it carries (`ck_{id_short}_...`,
`tk_{id_short}_...`) and the first 8 characters of its random part (stored as `lookup`;
NULL for a key migrated from the old single-key column), then checked against its bcrypt
hash. bcrypt is deliberately slow (~150 ms), so:

- it runs in a worker thread, never on the event loop, which it used to block —
  every authenticated request serialized behind it, capping the API at ~5 req/s;
- a key that verified is remembered for `_CACHE_TTL_S`, keyed by its SHA-256 and
  bound to the stored hash, so rotating or deleting the key ends it immediately;
- there is no scan over every stored hash for a key that matches no prefix. That
  fallback let one request with a made-up key cost N bcrypt checks (N = corpora);
- a key's `last_used_at` is written at most once a minute per process (and only when it is
  older than a minute), best effort, never failing the request.

After a key authenticated, its permission is checked against the route (`api/permissions.py`):
a `read` key may only search, ask, list and get; `write` adds adding and changing content;
`full` is everything, including delete and key management. Refusal: 403
`key_permission_denied` naming the permission needed. The admin key and sessions are not keys
with a permission.

Bearer sessions (accounts A3):

Dashboard users send `Authorization: Bearer <access token>` instead of a key. That
branch is taken only when there is NO X-API-Key header (a key always wins, so a proxy
adding `Authorization: Basic` changes nothing for key clients) and the scheme is
`Bearer` (any case). It never touches `_VerifiedKeys` or bcrypt:

1. accounts off (no RAG_SESSION_SECRET) -> 503 accounts_not_configured
2. verify the JWT (HS256 pinned, aud/iss/typ checked) -> 401 token_expired / invalid_token
3. ONE query: session + user (+ membership in `tid` + that tenant)
4. no row, revoked, expired, or user inactive -> 401 session_revoked
5. super admin (flag AND RAG_SUPERADMIN_EMAIL) with no `tid` -> the admin principal; with
   a `tid` -> the tenant principal of the operator workspace (role = their membership),
   and 403 not_member for any other tenant or a lost membership
6. else no `tid` -> 401 invalid_token; no membership -> 403 not_member; tenant
   pending/suspended -> 403 workspace_not_active except on the status page's routes
   (`INACTIVE_TENANT_ALLOWED`, every /api/v1/auth/ route); otherwise the tenant principal

Tenant status (accounts A4): a `tk_` key or a `ck_` key whose tenant is `pending` or
`suspended` gets 403 `{"code": "workspace_not_active", "status": ...}` on every route,
read per request (never cached). Widget keys are checked in `_authorize_widget`. No
route adds a second status check.

So a super-admin session IS the admin scope (until it switches into the operator
workspace, when it IS that tenant's scope) and a workspace session IS its tenant's
scope: `resolve_scope` / `authorized_corpus_ids` scope data without knowing how the
caller signed in. Everything is re-read per request, so logout-everywhere, password
resets, removal and suspension take effect at once.

Sets on request.state (every branch sets every field; README principals table):
- is_admin: bool
- corpus: Corpus | None (None when admin or tenant, Corpus instance when corpus key)
- tenant: Tenant | None (None when admin or corpus key, Tenant instance when tenant
  key or workspace session)
- user: User | None (sessions only)
- role: "owner" | "member" | None (tk_ keys act as owner; sessions carry the membership)
- api_key: KeyInfo | None (the ck_/tk_ key's id, name and permission; None otherwise)
- session_id: UUID | None (sessions only)
- auth_kind: "api_key" | "session" | "none"
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from fastapi import Request, Response
from sqlalchemy import select
from starlette.types import ASGIApp, Receive, Scope, Send

from callobuzz_rag.api.errors import error_response
from callobuzz_rag.api.features import gate_session  # BIZ3 features
from callobuzz_rag.api.permissions import KeyInfo, check_key_permission
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.core.secrets import constant_time_compare, verify_api_key

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.user import User

logger = logging.getLogger(__name__)

PUBLIC_PATHS: set[str] = {
    "/api/v1/health",
    "/api/v1/health/ready",
    "/docs",
    "/openapi.json",
    "/redoc",
    "/widget.js",
    # Conversation C4: the page a lead withdrawal link opens (the token is in the fragment).
    "/privacy/withdraw",
    "/privacy/withdraw.js",
    # Accounts A3: what a signed-out dashboard calls.
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/api/v1/auth/reset",
    # Accounts A4: sign-up and codes.
    "/api/v1/auth/signup",
    "/api/v1/auth/verify",
    "/api/v1/auth/resend",
    "/api/v1/auth/forgot",
    # Accounts A5: the accept-invitation page (the token is in the POST body).
    "/api/v1/invitations/view",
    "/api/v1/invitations/accept",
}
# What a signed-in user of a pending or suspended workspace may still call (the status
# page): these, and every /api/v1/auth/ route. Keys of such a tenant get nothing.
INACTIVE_TENANT_ALLOWED: set[tuple[str, str]] = {
    ("GET", "/api/v1/whoami"),
    ("GET", "/api/v1/tenants/me"),
    # Joining another workspace from a pending/suspended one (accounts A5).
    ("POST", "/api/v1/invitations/accept"),
}
INACTIVE_TENANT_PREFIX = "/api/v1/auth/"
# Public (also in PUBLIC_PATHS), but a Bearer token, if sent, is resolved (and a bad one
# refused): accepting an invitation as the signed-in user (accounts A5).
OPTIONAL_AUTH_PATHS: set[str] = {"/api/v1/invitations/accept"}

# Authenticated by their own publishable widget key instead (api/routes/widget.py).
WIDGET_PREFIXES: tuple[str, ...] = ("/api/v1/widget/",)
# No credential at all: what a sign-up or login page needs before anyone signs in
# (api/routes/engine_settings.py, public branding).
OPEN_PREFIXES: tuple[str, ...] = ("/api/v1/public/",)
# Everything the middleware lets through without an X-API-Key.
# P3 legal: the deployer's published legal pages (HTML, outside /api, like /privacy/withdraw)
LEGAL_PREFIXES: tuple[str, ...] = ("/legal/",)
# /P3 legal
# LP1 landing: the public website (HTML outside /api). Its pages, in every language, and its
# generated files need no credential; the editor API stays under /api/v1/admin/landing.
LANDING_PREFIXES: tuple[str, ...] = ("/_landing/",)
_LANDING_LANG_PREFIXES: tuple[str, ...] = ("", "/hi", "/hi-latn")
_LANDING_PAGE_PATHS: tuple[str, ...] = ("/pricing", "/features", "/about", "/contact")
LANDING_PATHS: frozenset[str] = frozenset(
    {"/", "/robots.txt", "/sitemap.xml"}
    | {f"{p}{page}" for p in _LANDING_LANG_PREFIXES for page in _LANDING_PAGE_PATHS}
    | {p or "/" for p in _LANDING_LANG_PREFIXES}
    | {f"{p}/" for p in _LANDING_LANG_PREFIXES if p}
)
PUBLIC_PATHS.update(LANDING_PATHS)
# /LP1 landing
PUBLIC_PREFIXES: tuple[str, ...] = (
    WIDGET_PREFIXES + OPEN_PREFIXES + LEGAL_PREFIXES + LANDING_PREFIXES  # P3 legal, LP1 landing
)

_CACHE_TTL_S = 300.0
_CACHE_MAX = 10_000
# A key's last_used_at is rewritten at most this often (seconds), per process.
_TOUCH_EVERY_S = 60.0


class _VerifiedKeys:
    """sha256(key) -> (row id, the bcrypt hash it verified against, expiry).

    Never holds a key. A hit still loads the row and requires its hash to be the
    one that was verified, so a rotated, deleted or deactivated key stops working
    on its next request, not after the TTL.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[uuid.UUID, str, float]] = {}

    @staticmethod
    def digest(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    def get(self, digest: str) -> tuple[uuid.UUID, str] | None:
        entry = self._entries.get(digest)
        if entry is None:
            return None
        if entry[2] < time.monotonic():
            del self._entries[digest]
            return None
        return entry[0], entry[1]

    def put(self, digest: str, row_id: uuid.UUID, api_key_hash: str) -> None:
        if len(self._entries) >= _CACHE_MAX:
            self._entries.clear()  # crude, bounded, and a miss only costs one bcrypt
        self._entries[digest] = (row_id, api_key_hash, time.monotonic() + _CACHE_TTL_S)

    def drop(self, digest: str) -> None:
        self._entries.pop(digest, None)


CallNext = Callable[[Request], Awaitable[Response]]


def _bearer_token(header: str | None) -> str | None:
    """The token of an `Authorization: Bearer <token>` header (scheme in any case)."""
    if not header:
        return None
    scheme, _, token = header.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _set_state(
    request: Request,
    *,
    auth_kind: str,
    is_admin: bool = False,
    tenant: "Tenant | None" = None,
    corpus: "Corpus | None" = None,
    user: "User | None" = None,
    role: str | None = None,
    session_id: uuid.UUID | None = None,
    api_key: KeyInfo | None = None,
) -> None:
    """Set every principal field, so no branch leaves one over from anything else."""
    state = request.state
    state.api_key = api_key
    state.is_admin = is_admin
    state.tenant = tenant
    state.corpus = corpus
    state.user = user
    state.role = role
    state.session_id = session_id
    state.auth_kind = auth_kind
    # BIZ3 features: gated only for a business's session (`gate_session` sets it after this)
    state.hidden_gates = frozenset()
    state.dashboard_features = None
    # /BIZ3 features


def _session_revoked() -> Response:
    return error_response(401, "This session has ended. Sign in again.", "session_revoked")


def _not_active(status: str) -> Response:
    """403 for any credential of a pending or suspended tenant (accounts A4)."""
    return error_response(
        403, "This workspace is not active", "workspace_not_active", status=status
    )


class _WorkspaceNotActiveError(RAGKernelError):
    """A corpus key verified, but its tenant is not active."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _key_info(owner: object) -> KeyInfo | None:
    """The `KeyInfo` the finder attached to the owner it returned (None for anything else)."""
    info = getattr(owner, "_matched_key", None)
    return info if isinstance(info, KeyInfo) else None


def _permission_denied(info: KeyInfo | None, request: Request) -> Response | None:
    """403 `key_permission_denied` when the key's permission does not allow this route."""
    if info is None or info.permission == "full":
        return None
    needed = check_key_permission(info.permission, request.method, request.url.path)
    if needed is None:
        return None
    return error_response(
        403,
        f"This API key has {info.permission} permission; this needs {needed}",
        "key_permission_denied",
        permission=info.permission,
        required=needed,
    )


class _Passed(Response):
    """What `call_next` returns once the downstream app has sent its own response."""


class APIKeyAuthMiddleware:
    """Middleware that enforces X-API-Key authentication on all non-public routes.

    Pure ASGI, not Starlette's BaseHTTPMiddleware. That class runs the endpoint
    in a task and pipes its body through a memory stream; when a streamed
    response (the SSE routes) finished, its cancel scope cancelled the request
    task mid-cleanup, so the request's database session was torn down by a
    CancelledError and a dead connection went back to the pool — the NEXT
    request then failed with "connection is closed". Here the downstream app is
    called directly with the original receive/send, so nothing is cancelled.

    `dispatch` keeps its old shape (request, call_next) -> Response so tests can
    drive or replace it; `call_next` runs the app and returns a marker.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._verified = _VerifiedKeys()
        self._touched: dict[uuid.UUID, float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)

        async def call_next(_: Request) -> Response:
            await self.app(scope, receive, send)
            return _Passed()

        response = await self.dispatch(request, call_next)
        if not isinstance(response, _Passed):
            await response(scope, receive, send)

    async def dispatch(
        self,
        request: Request,
        call_next: CallNext,
    ) -> Response:
        """Authenticate the request by X-API-Key, or by a Bearer session token."""
        # Skip auth for CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        api_key = request.headers.get("X-API-Key")
        bearer = None if api_key else _bearer_token(request.headers.get("authorization"))

        # Skip auth for public paths
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES):
            if path in OPTIONAL_AUTH_PATHS and bearer is not None:
                return await self._session(request, bearer, call_next)
            _set_state(request, auth_kind="none")
            return await call_next(request)

        if not api_key:
            if bearer is not None:
                return await self._session(request, bearer, call_next)
            return error_response(401, "API key required", "auth_required")

        # Check admin key first (constant-time comparison)
        settings = request.app.state.settings
        admin_key = settings.admin_api_key.get_secret_value()
        if admin_key and constant_time_compare(api_key, admin_key):
            _set_state(request, auth_kind="api_key", is_admin=True)
            return await call_next(request)

        # Check tenant keys (tk_ prefix)
        if api_key.startswith("tk_"):
            tenant = await self._find_tenant_by_key(request, api_key)
            if tenant is not None:
                if tenant.status != "active":
                    return _not_active(tenant.status)
                info = _key_info(tenant)
                _set_state(request, auth_kind="api_key", tenant=tenant, role="owner", api_key=info)
                denied = _permission_denied(info, request)
                if denied is not None:
                    return denied
                return await call_next(request)

        # Check corpus keys (bcrypt verify)
        try:
            corpus = await self._find_corpus_by_key(request, api_key)
        except _WorkspaceNotActiveError as exc:
            return _not_active(exc.status)
        if corpus is not None:
            info = _key_info(corpus)
            _set_state(request, auth_kind="api_key", corpus=corpus, api_key=info)
            denied = _permission_denied(info, request)
            if denied is not None:
                return denied
            return await call_next(request)

        # No match
        return error_response(401, "Invalid API key", "invalid_api_key")

    async def _session(self, request: Request, token: str, call_next: CallNext) -> Response:
        """Resolve a dashboard access token to the admin or a tenant principal."""
        from callobuzz_rag.accounts.passwords import normalize_email
        from callobuzz_rag.accounts.tokens import decode_access_token
        from callobuzz_rag.core.exceptions import (
            AccountsNotConfiguredError,
            TokenExpiredError,
            TokenInvalidError,
        )
        from callobuzz_rag.db.models.plan import Plan  # BIZ3 features
        from callobuzz_rag.db.models.tenant import Tenant
        from callobuzz_rag.db.models.tenant_member import TenantMember
        from callobuzz_rag.db.models.user import User
        from callobuzz_rag.db.models.user_session import UserSession

        settings = request.app.state.settings
        try:
            claims = decode_access_token(settings, token)
        except AccountsNotConfiguredError:
            return error_response(
                503,
                "Accounts are not configured on this engine (RAG_SESSION_SECRET)",
                "accounts_not_configured",
            )
        except TokenExpiredError:
            return error_response(401, "Access token expired", "token_expired")
        except TokenInvalidError:
            return error_response(401, "Invalid access token", "invalid_token")

        tid = claims.tenant_id
        stmt = (
            # BIZ3 features: the plan's dashboard flags ride on this one statement
            select(UserSession, User, TenantMember.role, Tenant, Plan.dashboard_features)
            .join(User, User.id == UserSession.user_id)
            .outerjoin(
                TenantMember,
                sa.and_(TenantMember.user_id == User.id, TenantMember.tenant_id == tid),
            )
            .outerjoin(Tenant, Tenant.id == tid)
            .outerjoin(Plan, Plan.id == Tenant.plan_id)  # BIZ3 features
            .where(UserSession.id == claims.session_id, UserSession.user_id == claims.user_id)
        )
        now = datetime.now(UTC)
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            found = (await session.execute(stmt)).first()
            if found is None:
                return _session_revoked()
            user_session, user, role, tenant, plan_features = found  # BIZ3 features
            if (
                user_session.revoked_at is not None
                or user_session.expires_at <= now
                or user_session.absolute_expires_at <= now
                or not user.is_active
            ):
                return _session_revoked()
            session.expunge(user)
            if tenant is not None:
                session.expunge(tenant)

        superadmin = settings.superadmin_email
        if user.is_superadmin and superadmin and user.email == normalize_email(superadmin):
            if tid is None:
                _set_state(
                    request,
                    auth_kind="session",
                    is_admin=True,
                    user=user,
                    session_id=user_session.id,
                )
                return await call_next(request)
            # The super admin working inside the operator's own workspace: a plain
            # tenant principal (never admin), only for that one workspace and only
            # while they are a member of it. Anything else fails closed.
            if role is None or tenant is None or not tenant.is_operator:
                return error_response(403, "You are not a member of that workspace", "not_member")
        if tid is None:
            return error_response(401, "Invalid access token", "invalid_token")
        if role is None or tenant is None:
            return error_response(403, "You are not a member of that workspace", "not_member")
        if tenant.status != "active":
            path = request.url.path
            allowed = (request.method, path) in INACTIVE_TENANT_ALLOWED or path.startswith(
                INACTIVE_TENANT_PREFIX
            )
            if not allowed:
                return _not_active(tenant.status)
        _set_state(
            request,
            auth_kind="session",
            tenant=tenant,
            role=role,
            user=user,
            session_id=user_session.id,
        )
        # BIZ3 features: a business's plan can hide configuration routes from its people
        refused = await gate_session(request, tenant, plan_features)
        if refused is not None:
            return refused
        # /BIZ3 features
        return await call_next(request)

    async def _find_tenant_by_key(
        self,
        request: Request,
        api_key: str,
    ) -> "Tenant | None":
        """Resolve a `tk_{id_short}_{random}` key to its tenant, or None.

        Whatever the tenant's status: `dispatch` answers a non-active tenant's key with
        403 `workspace_not_active` (the key holder already has the key, so confirming it
        is valid reveals nothing). The returned tenant carries the key's `KeyInfo`.
        """
        owner = await self._find_owner(request, api_key, "tk")
        return owner  # type: ignore[no-any-return]

    async def _find_corpus_by_key(
        self,
        request: Request,
        api_key: str,
    ) -> "Corpus | None":
        """Resolve a `ck_{id_short}_{random}` key to its corpus, or None.

        Raises `_WorkspaceNotActiveError` when the key is valid but its tenant is not active.
        The returned corpus carries the key's `KeyInfo`.
        """
        owner = await self._find_owner(request, api_key, "ck")
        return owner  # type: ignore[no-any-return]

    async def _find_owner(self, request: Request, api_key: str, kind: str) -> Any:
        """The Corpus (`ck`) or Tenant (`tk`) whose `api_keys` row `api_key` matches.

        One query for the owner's candidate rows (matching `id_short` and `lookup`, or a
        migrated row with no `lookup`), bcrypt only on those, in a worker thread. A key that
        verified is remembered by its SHA-256 with the row id and hash it verified against; a
        hit still loads the row and needs that hash, so a rotated or revoked key ends at once.
        """
        from callobuzz_rag.db.models.api_key import ApiKey
        from callobuzz_rag.db.models.corpus import Corpus
        from callobuzz_rag.db.models.tenant import Tenant

        parts = api_key.split("_", 2)  # [kind, "{id_short}", "{random}"]
        if len(parts) != 3 or parts[0] != kind or not parts[1]:
            return None
        owner_model: Any = Corpus if kind == "ck" else Tenant
        owner_column = ApiKey.corpus_id if kind == "ck" else ApiKey.tenant_id
        digest = self._verified.digest(api_key)
        session_factory = request.app.state.session_factory
        async with session_factory() as session:
            cached = self._verified.get(digest)
            if cached is not None:
                found = (
                    await session.execute(
                        select(ApiKey, owner_model)
                        .join(owner_model, owner_model.id == owner_column)
                        .where(ApiKey.id == cached[0])
                    )
                ).first()
                if found is not None and found[0].key_hash == cached[1]:
                    return await self._accept(session, kind, found[0], found[1])
                self._verified.drop(digest)

            candidates = (
                await session.execute(
                    select(ApiKey, owner_model)
                    .join(owner_model, owner_model.id == owner_column)
                    .where(
                        ApiKey.id_short == parts[1],
                        sa.or_(ApiKey.lookup == parts[2][:8], ApiKey.lookup.is_(None)),
                    )
                    # exact-lookup rows first: a migrated (NULL lookup) row costs a bcrypt
                    # only when nothing else matched
                    .order_by(ApiKey.lookup.is_(None))
                )
            ).all()
            for row, owner in candidates:
                if row.key_hash and await asyncio.to_thread(verify_api_key, api_key, row.key_hash):
                    self._verified.put(digest, row.id, row.key_hash)
                    return await self._accept(session, kind, row, owner)
        return None

    async def _accept(self, session: "AsyncSession", kind: str, row: Any, owner: Any) -> Any:
        """A verified key's owner, with the key attached. A knowledge-base key of a pending or
        suspended tenant raises `_WorkspaceNotActiveError` (read per request, never cached)."""
        if kind == "ck":
            await self._check_tenant_status(session, owner)
        info = KeyInfo(id=row.id, name=row.name, permission=row.permission, kind=kind)  # type: ignore[arg-type]
        await self._touch(session, row.id)
        session.expunge(owner)
        owner._matched_key = info
        return owner

    async def _touch(self, session: "AsyncSession", key_id: uuid.UUID) -> None:
        """Stamp `last_used_at`, at most once a minute per process. Best effort: a failure here
        is logged and never fails the request."""
        from callobuzz_rag.db.models.api_key import ApiKey

        now = time.monotonic()
        last = self._touched.get(key_id)
        if last is not None and now - last < _TOUCH_EVERY_S:
            return
        if len(self._touched) >= _CACHE_MAX:
            self._touched.clear()
        self._touched[key_id] = now
        try:
            await session.execute(
                sa.update(ApiKey).where(ApiKey.id == key_id).values(last_used_at=datetime.now(UTC))
            )
            await session.commit()
        except Exception:
            logger.warning("Could not record the use of API key %s", key_id, exc_info=True)
            try:
                await session.rollback()
            except Exception:
                logger.debug("Rollback after a failed last_used_at update failed", exc_info=True)

    @staticmethod
    async def _tenant_status(session: "AsyncSession", corpus: "Corpus") -> str:
        """The status of the corpus's tenant (every corpus has one, accounts A1).

        Read on every request (not cached), so suspending a tenant takes effect at
        once for all its corpora.
        """
        from callobuzz_rag.db.models.tenant import Tenant

        tenant = await session.get(Tenant, corpus.tenant_id)
        return "active" if tenant is None else str(tenant.status)

    async def _check_tenant_status(self, session: "AsyncSession", corpus: "Corpus") -> None:
        """Raises `_WorkspaceNotActiveError` when the corpus's tenant is pending or suspended."""
        status = await self._tenant_status(session, corpus)
        if status != "active":
            raise _WorkspaceNotActiveError(status)
