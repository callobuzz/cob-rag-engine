"""FastAPI dependency injection providers."""

import logging
import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.core.config import Settings

if TYPE_CHECKING:
    from callobuzz_rag.db.models.corpus import Corpus
    from callobuzz_rag.db.models.tenant import Tenant
    from callobuzz_rag.db.models.user import User

logger = logging.getLogger(__name__)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Get a database session from the app state."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    """Get application settings from app state."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_webhook_dispatcher(request: Request) -> Any | None:
    """Get webhook dispatcher from app state (may be None)."""
    return getattr(request.app.state, "webhook_dispatcher", None)


def get_authenticated_corpus(request: Request) -> "Corpus":
    """Get the authenticated corpus from request state.

    Returns the Corpus object set by APIKeyAuthMiddleware. Raises 403 if
    the request was authenticated with an admin key (admin must use
    corpus-specific endpoints with a corpus key, not the admin key).

    IMPORTANT — the returned instance is DETACHED. The middleware loads it in its own
    session and expunges it, so it is safe to read but writing to it persists nothing
    and any `session.refresh()` on it raises `InvalidRequestError: ... is not
    persistent within this Session`. To modify the corpus, re-load it first:
    `managed = await session.get(Corpus, corpus.id)`. That was BUG-7 — every
    `PATCH /api/v1/config` returned 500 for exactly this reason.

    Raises:
        HTTPException(401): If no authentication state is set.
        HTTPException(403): If authenticated as admin (no corpus context).
    """
    is_admin = getattr(request.state, "is_admin", None)
    corpus: Corpus | None = getattr(request.state, "corpus", None)

    if is_admin is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    if is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin key cannot access corpus-scoped endpoints. Use a corpus API key.",
        )

    if corpus is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    return corpus


def require_admin(request: Request) -> None:
    """Verify the request was authenticated with the admin API key.

    Use as a dependency on admin-only endpoints:
        @router.post("/admin/action")
        async def do_thing(admin: None = Depends(require_admin)):
            ...

    Raises:
        HTTPException(401): If no authentication state is set.
        HTTPException(403): If not authenticated as admin.
    """
    is_admin = getattr(request.state, "is_admin", None)

    if is_admin is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
        )

    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Admin access required",
        )


def get_authenticated_tenant(request: Request) -> "Tenant":
    """The tenant a tenant key (tk_) authenticated as. Tenant keys only.

    The instance is the middleware's DETACHED copy: re-load it before writing.

    Raises:
        HTTPException(401): If no authentication state is set.
        HTTPException(403): For an admin or corpus key.
    """
    tenant: Tenant | None = getattr(request.state, "tenant", None)
    if tenant is not None:
        return tenant
    if getattr(request.state, "is_admin", None) is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    raise HTTPException(
        status_code=403,
        detail=(
            "This route is for a tenant (a tk_ key or a workspace sign-in); "
            "an admin uses /api/v1/admin/tenants"
        ),
    )


def current_user(request: Request) -> "User | None":
    """The signed-in user of a dashboard session, or None for an API key or no auth.

    The instance is the middleware's DETACHED copy: re-load it before writing.
    """
    return getattr(request.state, "user", None)


def require_session_user(request: Request) -> "User":
    """The signed-in user; for routes that only make sense for a person (accounts A3).

    Raises:
        APIError(401, auth_required): no credential at all.
        APIError(403, session_required): authenticated with an API key.
    """
    from callobuzz_rag.api.errors import APIError

    user = current_user(request)
    if user is not None:
        return user
    if getattr(request.state, "is_admin", None) is None:
        raise APIError(401, "API key required", "auth_required")
    raise APIError(403, "This route is for a signed-in user, not an API key", "session_required")


def require_owner(request: Request) -> None:
    """Refuse a workspace **member**'s sign-in on an owner-only route (accounts A5).

    Only a session whose membership role is `member` is refused: the admin key, a `tk_`
    key (it acts as an owner), a `ck_` key (it keeps exactly what it could do before),
    the super admin and owners pass. Scoping is still `resolve_scope`'s job. The list of
    owner-only routes is in the A5 spec and in `OWNER_ONLY` (tests/unit/scope_harness.py);
    a new destructive or key-managing route belongs on both.

    Raises:
        APIError(403, owner_required): a member's session.
    """
    _refuse_member(request, "Only a workspace owner can do this")


def require_owner_for(request: Request, what: str) -> None:
    """`require_owner` for one part of a request, e.g. a field of a body a member may
    otherwise send (`PUT /corpora/{id}` with `llm_config`)."""
    _refuse_member(request, f"Only a workspace owner can change {what}")


def _refuse_member(request: Request, detail: str) -> None:
    from callobuzz_rag.api.errors import APIError

    if getattr(request.state, "role", None) == "member":
        raise APIError(403, detail, "owner_required")


def require_admin_or_tenant(request: Request) -> None:
    """Verify the request is authenticated as either admin or tenant.

    Use on endpoints that both admin and tenant can access (like corpus CRUD).

    Raises:
        HTTPException(401): If no authentication state is set.
        HTTPException(403): If not authenticated as admin or tenant.
    """
    is_admin = getattr(request.state, "is_admin", None)
    tenant = getattr(request.state, "tenant", None)

    if is_admin is None and tenant is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not is_admin and tenant is None:
        raise HTTPException(
            status_code=403,
            detail="Admin or tenant access required",
        )


@dataclass(frozen=True)
class Scope:
    """Who the caller is, in terms of what corpora they may touch.

    `require_admin_or_tenant` is a pure gate — it returns None and carries no
    identity, which is why every route using it has to hand-roll its own
    filtering. This carries the identity instead, so the filtering can be done
    once (see `authorized_corpus_ids`).

    - admin key  -> is_admin=True, everything else None: unrestricted
    - tenant key -> tenant_id set: any corpus that tenant owns
    - corpus key -> corpus_id set: exactly that corpus. tenant_id is the owning
      tenant (always set since accounts A1).
    """

    is_admin: bool
    tenant_id: uuid.UUID | None = None
    corpus_id: uuid.UUID | None = None


def resolve_scope(request: Request) -> Scope:
    """Resolve the caller's scope from whichever key type authenticated them.

    Accepts admin, tenant AND corpus keys, so a route using this works for every
    caller without needing to know which credential it will be handed. Dashboard
    sessions (accounts A3) resolve to the same scopes: the middleware sets a
    super-admin session as `is_admin` and a workspace session as its `tenant`, so
    nothing here branches on how the caller authenticated.

    Raises:
        HTTPException(401): If no authentication state is set.
    """
    is_admin = getattr(request.state, "is_admin", None)
    tenant = getattr(request.state, "tenant", None)
    corpus = getattr(request.state, "corpus", None)

    if is_admin:
        return Scope(is_admin=True)

    if tenant is not None:
        return Scope(is_admin=False, tenant_id=tenant.id)

    if corpus is not None:
        # APIKeyAuthMiddleware sets tenant=None for corpus keys, so the owning
        # tenant has to be read off the corpus itself.
        return Scope(
            is_admin=False,
            tenant_id=getattr(corpus, "tenant_id", None),
            corpus_id=corpus.id,
        )

    raise HTTPException(status_code=401, detail="Not authenticated")


async def authorized_corpus_ids(
    scope: Scope,
    requested_corpus_id: uuid.UUID | None,
    session: AsyncSession,
) -> Sequence[uuid.UUID] | None:
    """Corpus ids this caller may read, or None meaning "no restriction".

    This is the single place the scoping rules live. Returning None only ever
    happens for admin — a tenant that owns nothing gets an empty sequence, which
    filters everything out rather than falling through to unrestricted.

    Raises:
        HTTPException(403): If the caller asked for a corpus outside their scope.
        HTTPException(404): If the requested corpus does not exist.
    """
    from callobuzz_rag.db.models.corpus import Corpus

    if scope.is_admin:
        return None if requested_corpus_id is None else [requested_corpus_id]

    # Corpus key: the key IS the scope. Asking for anything else is a 403, and
    # asking for nothing still resolves to that one corpus.
    if scope.corpus_id is not None:
        if requested_corpus_id is not None and requested_corpus_id != scope.corpus_id:
            raise HTTPException(
                status_code=403,
                detail="This API key is scoped to a different corpus.",
            )
        return [scope.corpus_id]

    # Tenant key: verify ownership of what was asked for, or fall back to
    # everything this tenant owns.
    if requested_corpus_id is not None:
        corpus = await session.get(Corpus, requested_corpus_id)
        if corpus is None:
            raise HTTPException(status_code=404, detail="Corpus not found")
        if corpus.tenant_id != scope.tenant_id:
            raise HTTPException(
                status_code=403,
                detail="Corpus does not belong to this tenant.",
            )
        return [requested_corpus_id]

    result = await session.execute(select(Corpus.id).where(Corpus.tenant_id == scope.tenant_id))
    return list(result.scalars().all())


async def _load_scoped_corpus(
    request: Request, corpus_id: uuid.UUID, session: AsyncSession
) -> "Corpus":
    """The corpus `corpus_id` names, if the caller's scope reaches it.

    403 for a tenant asking about another tenant's corpus or a corpus key asking
    about another corpus; 404 for a corpus that does not exist.
    """
    from callobuzz_rag.db.models.corpus import Corpus

    scope = resolve_scope(request)
    await authorized_corpus_ids(scope, corpus_id, session)
    if scope.corpus_id is not None:
        # The key's own corpus, already loaded by the middleware (detached).
        own: Corpus = request.state.corpus
        _protect_system_corpus(request, own)
        return own
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    _protect_system_corpus(request, corpus)
    return corpus


def _protect_system_corpus(request: Request, corpus: "Corpus") -> None:
    """COB's built-in knowledge base can be read, never changed (409
    `system_resource_protected`). One choke point for every route that names a knowledge
    base through `path_corpus` / `scoped_corpus`; a POST that only searches (`/query`) is a
    read in ROUTE_OPS and passes."""
    from callobuzz_rag.api.permissions import required_operation
    from callobuzz_rag.cob.platform import PLATFORM_DOC_ROUTES, is_super_admin_session
    from callobuzz_rag.cob.system import is_system_corpus, refuse_system_corpus

    if not is_system_corpus(corpus) or request.method in ("GET", "HEAD", "OPTIONS"):
        return
    route = request.scope.get("route")
    template = getattr(route, "path", None) or request.url.path
    if (request.method, template) in PLATFORM_DOC_ROUTES and is_super_admin_session(request):
        # COB9 platform docs (start)
        return  # a super admin deletes platform docs (the route refuses built-in ones)
        # COB9 platform docs (end)
    if required_operation(request.method, template) != "read":
        refuse_system_corpus(corpus)


async def scoped_corpus(
    request: Request,
    corpus_id: uuid.UUID | None = Query(
        None,
        description=(
            "The corpus to act on. Required with an admin or tenant key; optional with a "
            "corpus key (which may only name its own corpus)."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> "Corpus":
    """The corpus a single-corpus route acts on, for any key kind.

    - corpus key: its own corpus; naming another is 403.
    - tenant key: `corpus_id` required; another tenant's corpus is 403.
    - admin key: `corpus_id` required; any corpus.

    With a corpus key the returned instance is the middleware's DETACHED copy
    (see `get_authenticated_corpus`); re-load it before writing to it.
    """
    scope = resolve_scope(request)
    if corpus_id is None:
        if scope.corpus_id is not None:
            own: Corpus = request.state.corpus
            return own
        raise HTTPException(
            status_code=422,
            detail="corpus_id is required with an admin or tenant API key",
        )
    return await _load_scoped_corpus(request, corpus_id, session)


async def path_corpus(
    request: Request,
    corpus_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> "Corpus":
    """`scoped_corpus` for routes that carry the corpus id in the path."""
    return await _load_scoped_corpus(request, corpus_id, session)
