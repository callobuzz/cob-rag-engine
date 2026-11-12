"""Dashboard sessions: create, rotate the refresh token, revoke (accounts A3).

A session is one refresh-token family. Every refresh replaces the token and keeps the
replaced one's hash as `previous_refresh_token_hash`; presenting that previous token
again means two parties hold the family (one of them stole it), so the whole session is
revoked. Any other mismatch is a plain 401 and revokes nothing, so nobody can end
someone's session by guessing its id. Rotation locks the row (`SELECT ... FOR UPDATE`),
which serializes concurrent refreshes of one session. Nothing here commits except the
reuse revocation, which must stick although the request fails.
"""

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from callobuzz_rag.accounts.tokens import new_refresh_token, parse_refresh_token, sha256_hex
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.user_session import UserSession

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.user import User


def request_ip(request: "Request") -> str:
    from callobuzz_rag.widget.security import request_client_ip

    return request_client_ip(request)[:64]


def _invalid() -> APIError:
    return APIError(401, "This sign-in is no longer valid. Sign in again.", "refresh_token_invalid")


def session_revoked() -> APIError:
    return APIError(401, "This session has ended. Sign in again.", "session_revoked")


async def create_session(
    session: "AsyncSession",
    user: "User",
    tenant_id: uuid.UUID | None,
    request: "Request",
) -> tuple[UserSession, str]:
    """A new session for `user` acting for `tenant_id`, and its raw refresh token."""
    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)
    session_id = uuid.uuid4()
    raw = new_refresh_token(session_id)
    absolute = now + timedelta(days=settings.session_max_days)
    row = UserSession(
        id=session_id,
        user_id=user.id,
        tenant_id=tenant_id,
        refresh_token_hash=sha256_hex(raw),
        previous_refresh_token_hash=None,
        created_at=now,
        last_used_at=None,
        expires_at=min(now + timedelta(days=settings.refresh_token_days), absolute),
        absolute_expires_at=absolute,
        user_agent=(request.headers.get("user-agent") or "")[:512] or None,
        ip=request_ip(request),
    )
    session.add(row)
    await session.flush()
    return row, raw


async def rotate(
    session: "AsyncSession", raw_token: str, request: "Request"
) -> tuple[UserSession, str]:
    """Replace the session's refresh token; returns (locked session row, new raw token).

    Raises:
        APIError 401 `refresh_token_invalid`: malformed, unknown, expired or mismatched.
        APIError 401 `refresh_token_reused`: the previous token came back; the session is
            revoked and that revocation committed (with its audit row).
        APIError 401 `session_revoked`: the session was already revoked.
    """
    parsed = parse_refresh_token(raw_token)
    if parsed is None:
        raise _invalid()
    session_id, _ = parsed
    row = (
        await session.execute(
            select(UserSession).where(UserSession.id == session_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise _invalid()

    presented = sha256_hex(raw_token)
    is_current = hmac.compare_digest(presented, row.refresh_token_hash)
    is_previous = row.previous_refresh_token_hash is not None and hmac.compare_digest(
        presented, row.previous_refresh_token_hash
    )
    now = datetime.now(UTC)
    if row.revoked_at is not None:
        raise session_revoked() if (is_current or is_previous) else _invalid()
    if is_previous:
        row.revoked_at = now
        row.revoked_reason = "refresh_reuse"
        from callobuzz_rag.accounts.audit import record_audit

        await record_audit(
            session,
            request,
            "session.refresh_reused",
            tenant_id=row.tenant_id,
            target_type="session",
            target_id=str(row.id),
            details={"user_id": str(row.user_id)},
            actor=("system", row.user_id, "refresh token reuse detection"),
        )
        await session.commit()
        raise APIError(
            401,
            "This sign-in was used from somewhere else and has been ended. Sign in again.",
            "refresh_token_reused",
        )
    if not is_current or row.expires_at <= now or row.absolute_expires_at <= now:
        raise _invalid()

    settings: Settings = request.app.state.settings
    raw = new_refresh_token(row.id)
    row.previous_refresh_token_hash = row.refresh_token_hash
    row.refresh_token_hash = sha256_hex(raw)
    row.last_used_at = now
    row.expires_at = min(now + timedelta(days=settings.refresh_token_days), row.absolute_expires_at)
    return row, raw


async def revoke(session: "AsyncSession", session_id: uuid.UUID, reason: str) -> bool:
    """Revoke one session if still open. True when it was."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    return bool(getattr(result, "rowcount", 0))


async def revoke_all(
    session: "AsyncSession",
    user_id: uuid.UUID,
    reason: str,
    except_id: uuid.UUID | None = None,
) -> int:
    """Revoke every open session of `user_id` (except `except_id`); returns how many."""
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    if except_id is not None:
        stmt = stmt.where(UserSession.id != except_id)
    result = await session.execute(stmt)
    return int(getattr(result, "rowcount", 0) or 0)


async def revoke_for_tenant(session: "AsyncSession", tenant_id: uuid.UUID, reason: str) -> int:
    """Revoke every open session currently acting for `tenant_id` (A4: suspension)."""
    result = await session.execute(
        update(UserSession)
        .where(UserSession.tenant_id == tenant_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason=reason)
    )
    return int(getattr(result, "rowcount", 0) or 0)
