"""6-digit codes e-mailed to verify an address or (re)set a password (accounts A3).

A code is `secrets.randbelow(10**6)`, zero-padded. Only
`HMAC-SHA256(primary session secret, f"{purpose}:{email}:{code}")` is stored: a million
codes hashed without a key are reversible from a database dump, and the key is not in
the database. Verification tries every secret in `RAG_SESSION_SECRET`, so a rotation does
not void codes already sent. A code allows 5 attempts; issuing a new one for the same
(email, purpose) marks the older open ones used. Lifetimes: 10 minutes for `verify` and
`reset`, 24 hours for `superadmin_setup`.
"""

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select, update

from callobuzz_rag.accounts.passwords import normalize_email
from callobuzz_rag.accounts.tokens import session_secrets
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.email_code import EmailCode

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

PURPOSES: Final = frozenset({"verify", "reset", "superadmin_setup"})
TTL_S: Final[dict[str, int]] = {"verify": 600, "reset": 600, "superadmin_setup": 24 * 3600}
MAX_ATTEMPTS: Final = 5


def code_hmac(secret: str, *, purpose: str, email: str, code: str) -> str:
    message = f"{purpose}:{email}:{code}".encode()
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _expired() -> APIError:
    return APIError(
        400, "This code has expired or was already used. Ask for a new one.", "code_expired"
    )


async def issue_code(
    session: "AsyncSession",
    *,
    settings: "Settings",
    email: str,
    purpose: str,
    user_id: uuid.UUID | None,
    payload: dict[str, Any] | None = None,
) -> str:
    """A new code for (email, purpose); older open ones stop working. Flushed, not
    committed: the caller commits it with whatever else the request changed."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown code purpose {purpose!r}")
    email = normalize_email(email)
    now = datetime.now(UTC)
    await session.execute(
        update(EmailCode)
        .where(
            EmailCode.email == email,
            EmailCode.purpose == purpose,
            EmailCode.used_at.is_(None),
        )
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(10**6):06d}"
    session.add(
        EmailCode(
            email=email,
            purpose=purpose,
            user_id=user_id,
            code_hash=code_hmac(
                session_secrets(settings)[0], purpose=purpose, email=email, code=code
            ),
            payload=payload or {},
            attempts=0,
            expires_at=now + timedelta(seconds=TTL_S[purpose]),
        )
    )
    await session.flush()
    return code


async def consume_code(
    session: "AsyncSession",
    *,
    settings: "Settings",
    email: str,
    purposes: tuple[str, ...],
    code: str,
) -> EmailCode:
    """The newest open code for `email` among `purposes`, marked used, if `code` matches.

    Not committed on success (the caller commits with its change). A wrong code's
    attempt count IS committed before raising, so failing requests cannot reset it.

    Raises:
        APIError 400 `invalid_code`: wrong code, attempts remain.
        APIError 400 `code_expired`: no usable code (expired, used, superseded, attempts
            exhausted, or none).
    """
    email = normalize_email(email)
    now = datetime.now(UTC)
    row = (
        await session.execute(
            select(EmailCode)
            .where(
                EmailCode.email == email,
                EmailCode.purpose.in_(purposes),
                EmailCode.used_at.is_(None),
            )
            .order_by(EmailCode.created_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.expires_at <= now or row.attempts >= MAX_ATTEMPTS:
        raise _expired()

    candidate = code.strip()
    matched = False
    for secret in session_secrets(settings):
        expected = code_hmac(secret, purpose=row.purpose, email=email, code=candidate)
        if hmac.compare_digest(expected, row.code_hash):
            matched = True
    if matched:
        row.used_at = now
        return row

    row.attempts += 1
    exhausted = row.attempts >= MAX_ATTEMPTS
    if exhausted:
        row.used_at = now
    await session.commit()
    if exhausted:
        raise _expired()
    raise APIError(400, "That code is not right. Check the e-mail and try again.", "invalid_code")
