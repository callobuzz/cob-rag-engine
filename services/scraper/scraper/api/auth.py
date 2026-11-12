"""Caller authentication against the RAG engine.

The scraper has no keys of its own. Every JSON API request carries an engine
credential: a key (admin, tenant ``tk_`` or corpus ``ck_``) in ``X-API-Key``, or a
dashboard sign-in's access token in ``Authorization: Bearer``. ``X-API-Key`` wins
when both are sent, exactly like the engine. The engine's ``GET /api/v1/whoami``,
asked with the same header, says which kind of caller it is and what it reaches.

Accepted identities are cached in memory, keyed by the SHA-256 of the credential,
for ``auth_cache_seconds`` (a Bearer token for at most 30 s, so a revoked session
stops quickly). The raw credential is never written to disk or logged: it lives
only on the ``Caller`` for the duration of a request (and, for a review-mode-off
job, which only a key can start, in the background task that delivers its pages).

Engine errors that carry a ``code`` (``token_expired``, ``session_revoked``,
``workspace_not_active``, ...) are passed through as ``{"detail", "code", ...}``
so the dashboard's refresh and sign-out logic works against the scraper too.
"""

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from scraper.storage.models import ScrapeJob

logger = logging.getLogger(__name__)

CallerKind = Literal["admin", "tenant", "corpus"]
CredentialKind = Literal["api_key", "bearer"]
_ENGINE_TIMEOUT = httpx.Timeout(10)
# A signed-out or revoked session must stop working quickly; access tokens live 15 min.
BEARER_CACHE_MAX_SECONDS = 30


@dataclass(frozen=True)
class Credential:
    """The engine credential a request carried, re-used for every call to the engine."""

    kind: CredentialKind
    value: str = field(repr=False)

    def headers(self) -> dict[str, str]:
        """The one header that presents this credential to the engine."""
        if self.kind == "bearer":
            return {"Authorization": f"Bearer {self.value}"}
        return {"X-API-Key": self.value}


class EngineError(HTTPException):
    """An engine error passed through with its machine-readable ``code`` and extras."""

    def __init__(
        self, status_code: int, detail: str, code: str | None, extra: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code
        self.extra = extra or {}

    def body(self) -> dict[str, Any]:
        body: dict[str, Any] = {**self.extra, "detail": self.detail}
        if self.code:
            body["code"] = self.code
        return body


async def engine_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render an ``EngineError`` as ``{"detail", "code", ...extra}``."""
    if not isinstance(exc, EngineError):  # registered for EngineError only
        raise exc
    return JSONResponse(status_code=exc.status_code, content=exc.body())


def _engine_error_parts(response: httpx.Response) -> tuple[str | None, str | None, dict[str, Any]]:
    """(detail, code, extras) of an engine error body; Nones when it has none."""
    try:
        body = response.json()
    except ValueError:
        return None, None, {}
    if not isinstance(body, dict):
        return None, None, {}
    detail = body.get("detail")
    code = body.get("code")
    extra = {
        k: v
        for k, v in body.items()
        if k not in ("detail", "code") and isinstance(v, str | int | float | bool | None)
    }
    return (
        detail if isinstance(detail, str) else None,
        code if isinstance(code, str) and code else None,
        extra,
    )


def pass_through(response: httpx.Response, fallback_detail: str) -> EngineError:
    """The scraper's answer to an engine 401/403: same status, same code and extras."""
    detail, code, extra = _engine_error_parts(response)
    return EngineError(response.status_code, detail or fallback_detail, code, extra)


@dataclass(frozen=True)
class Identity:
    """What the engine said about a credential. Holds no secret, so it can be cached."""

    kind: CallerKind
    tenant_id: str | None = None
    corpus_id: str | None = None


@dataclass(frozen=True)
class Caller:
    """The authenticated caller of one request."""

    credential: Credential
    kind: CallerKind
    tenant_id: str | None = None
    corpus_id: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def is_session(self) -> bool:
        """Signed in to the dashboard (Bearer), rather than holding a key."""
        return self.credential.kind == "bearer"

    def can_see(self, job: ScrapeJob) -> bool:
        """Admin sees every job; a tenant its own; a corpus key its corpus's jobs.

        Jobs created before ownership existed have no target corpus and no owner,
        so only an admin can see them.
        """
        if self.kind == "admin":
            return True
        if self.kind == "tenant":
            return self.tenant_id is not None and job.owner_tenant_id == self.tenant_id
        return bool(job.target_corpus_id) and job.target_corpus_id == self.corpus_id


class IdentityCache:
    """In-memory, TTL-bounded map of sha256(credential) -> Identity.

    Keys are cached for ``ttl_seconds``; Bearer tokens for at most
    ``BEARER_CACHE_MAX_SECONDS``.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._entries: dict[str, tuple[float, Identity]] = {}

    def ttl_for(self, credential: Credential) -> int:
        if credential.kind == "bearer":
            return min(self.ttl, BEARER_CACHE_MAX_SECONDS)
        return self.ttl

    @staticmethod
    def _digest(credential: Credential) -> str:
        raw = f"{credential.kind}:{credential.value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, credential: Credential) -> Identity | None:
        digest = self._digest(credential)
        entry = self._entries.get(digest)
        if entry is None:
            return None
        expires_at, identity = entry
        if expires_at <= time.monotonic():
            self._entries.pop(digest, None)
            return None
        return identity

    def put(self, credential: Credential, identity: Identity) -> None:
        ttl = self.ttl_for(credential)
        if ttl <= 0:
            return
        now = time.monotonic()
        # Drop expired entries so the map cannot grow without bound.
        for digest in [d for d, (exp, _) in self._entries.items() if exp <= now]:
            del self._entries[digest]
        self._entries[self._digest(credential)] = (now + ttl, identity)


def _rag_url(request: Request) -> str:
    return str(request.app.state.settings.rag_url).rstrip("/")


def credential_from_headers(x_api_key: str | None, authorization: str | None) -> Credential | None:
    """``X-API-Key`` first (like the engine), else ``Authorization: Bearer`` (any case)."""
    if x_api_key:
        return Credential("api_key", x_api_key)
    if authorization:
        scheme, _, token = authorization.strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token:
            return Credential("bearer", token)
    return None


async def authenticate(request: Request, credential: Credential | None) -> Caller:
    """Resolve an engine credential to a Caller, or raise 401 / 403 / 503."""
    if credential is None or not credential.value:
        raise HTTPException(status_code=401, detail="Credential required")

    cache: IdentityCache = request.app.state.identity_cache
    identity = cache.get(credential)
    if identity is None:
        identity = await _fetch_identity(_rag_url(request), credential)
        cache.put(credential, identity)
    return Caller(
        credential=credential,
        kind=identity.kind,
        tenant_id=identity.tenant_id,
        corpus_id=identity.corpus_id,
    )


async def _fetch_identity(rag_url: str, credential: Credential) -> Identity:
    try:
        async with httpx.AsyncClient(timeout=_ENGINE_TIMEOUT) as client:
            response = await client.get(f"{rag_url}/api/v1/whoami", headers=credential.headers())
    except httpx.HTTPError as exc:
        logger.warning("RAG engine unreachable for credential check: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="RAG engine unreachable") from exc

    invalid = "Invalid API key" if credential.kind == "api_key" else "Invalid sign-in"
    if response.status_code == 401:
        raise pass_through(response, invalid)
    if response.status_code == 403:
        # workspace_not_active, not_member: the dashboard acts on these codes.
        _, code, _ = _engine_error_parts(response)
        if code:
            raise pass_through(response, "Forbidden")
        raise HTTPException(status_code=401, detail=invalid)
    if response.status_code == 503:
        _, code, _ = _engine_error_parts(response)
        if code:  # accounts_not_configured: sign-in is off on the engine
            raise pass_through(response, "RAG engine unavailable")
    if response.status_code != 200:
        logger.warning("RAG engine whoami returned HTTP %s", response.status_code)
        raise HTTPException(status_code=503, detail="RAG engine unavailable")

    try:
        body = response.json()
        kind = body["kind"]
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="RAG engine gave no identity") from exc
    if kind not in ("admin", "tenant", "corpus"):
        raise HTTPException(status_code=503, detail="RAG engine gave no identity")
    tenant_id = body.get("tenant_id")
    corpus_id = body.get("corpus_id")
    if (kind == "tenant" and not tenant_id) or (kind == "corpus" and not corpus_id):
        raise HTTPException(status_code=503, detail="RAG engine gave an incomplete identity")
    return Identity(
        kind=kind,
        tenant_id=str(tenant_id) if tenant_id else None,
        corpus_id=str(corpus_id) if corpus_id else None,
    )


async def get_caller(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> Caller:
    """FastAPI dependency: the authenticated caller of a JSON API request."""
    return await authenticate(request, credential_from_headers(x_api_key, authorization))


CallerDep = Annotated[Caller, Depends(get_caller)]


async def check_corpus_access(request: Request, caller: Caller, corpus_id: str) -> None:
    """Ask the engine whether this caller's credential may use a corpus.

    ``GET /api/v1/corpora/{id}`` answers 200 for an admin and for a tenant (key or
    sign-in) that owns the corpus, 403 for a tenant that does not, and 404 when
    the corpus does not exist.
    """
    try:
        async with httpx.AsyncClient(timeout=_ENGINE_TIMEOUT) as client:
            response = await client.get(
                f"{_rag_url(request)}/api/v1/corpora/{corpus_id}",
                headers=caller.credential.headers(),
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="RAG engine unreachable") from exc

    if response.status_code == 200:
        return
    if response.status_code == 401:
        # e.g. token_expired: the identity was cached but the token has since expired.
        raise pass_through(response, "Invalid API key")
    if response.status_code == 403:
        _, code, _ = _engine_error_parts(response)
        if code in ("workspace_not_active", "not_member"):
            raise pass_through(response, "Forbidden")
        raise HTTPException(status_code=403, detail="This key cannot use that corpus")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Corpus not found")
    raise HTTPException(status_code=503, detail="RAG engine unavailable")
