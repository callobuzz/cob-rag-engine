"""Access tokens (JWT HS256) and refresh tokens for dashboard sessions (accounts A3).

Access token claims: `iss=cob-rag-engine`, `aud=cob-rag-dashboard`, `sub` (user id),
`sid` (session id), `tid` (tenant id or null), `iat`, `exp`, `typ=access`. No role or
flag is in the token: the middleware reads them from the database on every request, so a
change takes effect at once. The algorithm is pinned to HS256 on decode, so a `none` or
asymmetric-algorithm token can never verify.

`RAG_SESSION_SECRET` is a comma-separated list: the first secret signs, every one
verifies (rotation). Without it accounts are off (`AccountsNotConfiguredError`), except
with `RAG_DEBUG=true`, where a random per-process secret is used and a warning logged.

Refresh tokens are `rt_<session id hex>_<43 url-safe chars>` (256 random bits), stored as
sha256: with that much entropy a slow hash adds nothing, and the session id makes the
lookup a primary-key read.
"""

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

import jwt

from callobuzz_rag.core.exceptions import (
    AccountsNotConfiguredError,
    TokenExpiredError,
    TokenInvalidError,
)

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

ISSUER: Final = "cob-rag-engine"
AUDIENCE: Final = "cob-rag-dashboard"
ALGORITHM: Final = "HS256"
LEEWAY_S: Final = 30
MIN_SECRET_LENGTH: Final = 32
REFRESH_PREFIX: Final = "rt_"

_debug_secret: str | None = None


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    tenant_id: uuid.UUID | None
    issued_at: datetime
    expires_at: datetime


def configured_secrets(settings: "Settings") -> list[str]:
    """The secrets from `RAG_SESSION_SECRET` (possibly none). Raises ValueError for one
    shorter than 32 characters (Settings already refuses those at load)."""
    raw = settings.session_secret.get_secret_value()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if any(len(p) < MIN_SECRET_LENGTH for p in parts):
        raise ValueError(f"every RAG_SESSION_SECRET must be at least {MIN_SECRET_LENGTH} chars")
    return parts


def session_secrets(settings: "Settings") -> list[str]:
    """The signing secret first, then the others.

    Raises:
        AccountsNotConfiguredError: no secret and not debug.
    """
    parts = configured_secrets(settings)
    if parts:
        return parts
    if not settings.debug:
        raise AccountsNotConfiguredError(
            "Accounts are not configured on this engine (set RAG_SESSION_SECRET)"
        )
    global _debug_secret
    if _debug_secret is None:
        _debug_secret = secrets.token_hex(32)
        logger.warning(
            "RAG_SESSION_SECRET not set: using a random secret for this process only "
            "(RAG_DEBUG=true). Every sign-in ends when the process restarts."
        )
    return [_debug_secret]


def accounts_enabled(settings: "Settings") -> bool:
    try:
        session_secrets(settings)
    except AccountsNotConfiguredError:
        return False
    return True


def issue_access_token(
    settings: "Settings",
    *,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
) -> tuple[str, int]:
    """(token, lifetime in seconds)."""
    lifetime = settings.access_token_minutes * 60
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(user_id),
        "sid": str(session_id),
        "tid": str(tenant_id) if tenant_id is not None else None,
        "iat": now,
        "exp": now + timedelta(seconds=lifetime),
        "typ": "access",
    }
    token = jwt.encode(payload, session_secrets(settings)[0], algorithm=ALGORITHM)
    return token, lifetime


def decode_access_token(settings: "Settings", token: str) -> AccessClaims:
    """Verify `token` against every configured secret.

    Raises:
        AccountsNotConfiguredError: accounts are off.
        TokenExpiredError: a valid signature, past `exp` (+30 s leeway).
        TokenInvalidError: anything else wrong with it.
    """
    claims: dict[str, Any] | None = None
    for secret in session_secrets(settings):
        try:
            claims = jwt.decode(
                token,
                secret,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "sid", "typ"]},
                leeway=LEEWAY_S,
            )
            break
        except jwt.InvalidSignatureError:
            continue  # maybe signed by another secret in the list
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("access token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise TokenInvalidError("invalid access token") from exc
    if claims is None or claims.get("typ") != "access":
        raise TokenInvalidError("invalid access token")
    try:
        tid = claims.get("tid")
        return AccessClaims(
            user_id=uuid.UUID(str(claims["sub"])),
            session_id=uuid.UUID(str(claims["sid"])),
            tenant_id=uuid.UUID(str(tid)) if tid is not None else None,
            issued_at=datetime.fromtimestamp(int(claims["iat"]), UTC),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), UTC),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise TokenInvalidError("invalid access token") from exc


def new_refresh_token(session_id: uuid.UUID) -> str:
    return f"{REFRESH_PREFIX}{session_id.hex}_{secrets.token_urlsafe(32)}"


def parse_refresh_token(token: str) -> tuple[uuid.UUID, str] | None:
    """(session id, the token) for a well-formed refresh token, else None."""
    if not token.startswith(REFRESH_PREFIX):
        return None
    parts = token.split("_", 2)  # ["rt", "<hex>", "<random, may contain _>"]
    if len(parts) != 3 or len(parts[1]) != 32 or not parts[2]:
        return None
    try:
        return uuid.UUID(hex=parts[1]), token
    except ValueError:
        return None


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
