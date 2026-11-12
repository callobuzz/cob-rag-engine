"""Sign-in, refresh and the account payloads the auth routes return (accounts A3).

The super admin's session is the admin scope (no tenant) unless they chose to work inside
the operator's own workspace (accounts operator workspace): a session whose `tenant_id` is
that workspace, which the middleware turns into a plain tenant principal (role from the
membership, never admin). `POST /auth/switch-tenant` enters it and `POST /auth/switch-admin`
leaves it; no other tenant is ever reachable that way.

Login order matters for what a failure reveals: rate limits and the lockout first, then
the password (verified against a dummy hash when there is no account or no password, so
the time taken is the same), and only after a correct password the distinct refusals
(`email_not_verified`, `no_workspace`, `not_member`). Every earlier failure is the same
401 `invalid_credentials`.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from callobuzz_rag.accounts.passwords import normalize_email, verify_password
from callobuzz_rag.accounts.ratelimit import LOCK_AFTER, AccountRateLimiter
from callobuzz_rag.accounts.sessions import create_session, request_ip, rotate, session_revoked
from callobuzz_rag.accounts.tokens import issue_access_token
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.models.user_session import UserSession

logger = logging.getLogger(__name__)

# (bucket, limit, window seconds)
LOGIN_IP_LIMIT = ("login_ip", 30, 300)
LOGIN_EMAIL_LIMIT = ("login_email", 10, 900)
REFRESH_IP_LIMIT = ("refresh_ip", 120, 60)
LOGOUT_IP_LIMIT = ("logout_ip", 60, 60)
RESET_IP_LIMIT = ("reset_ip", 20, 600)
RESET_EMAIL_LIMIT = ("reset_email", 10, 600)
CHANGE_PASSWORD_USER_LIMIT = ("change_password", 5, 900)


def invalid_credentials() -> APIError:
    return APIError(401, "Wrong e-mail or password", "invalid_credentials")


def not_member() -> APIError:
    return APIError(403, "You are not a member of that workspace", "not_member")


def is_superadmin(user: "User", settings: "Settings") -> bool:
    """The super admin needs both the flag and `RAG_SUPERADMIN_EMAIL`: changing the
    variable demotes the old account on its next request."""
    configured = normalize_email(settings.superadmin_email) if settings.superadmin_email else ""
    return bool(user.is_superadmin) and bool(configured) and user.email == configured


def tenant_status(tenant: Tenant) -> str:
    """`pending` / `active` / `suspended` (the `tenants.status` column, accounts A4)."""
    return str(tenant.status)


async def memberships(
    session: "AsyncSession", user_id: uuid.UUID
) -> list[tuple[TenantMember, Tenant]]:
    """The user's memberships with their tenants, earliest first."""
    rows = await session.execute(
        select(TenantMember, Tenant)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.user_id == user_id)
        .order_by(TenantMember.created_at, TenantMember.tenant_id)
    )
    return [(m, t) for m, t in rows.all()]


def tenant_info(member: TenantMember, tenant: Tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "role": member.role,
        "status": tenant_status(tenant),
        "is_operator": bool(tenant.is_operator),
    }


async def operator_membership(
    session: "AsyncSession", user_id: uuid.UUID, tenant_id: uuid.UUID
) -> tuple[TenantMember, Tenant] | None:
    """The user's membership of `tenant_id` if that tenant is the operator workspace."""
    return next(
        (
            (m, t)
            for m, t in await memberships(session, user_id)
            if t.id == tenant_id and t.is_operator
        ),
        None,
    )


def user_item(user: "User", settings: "Settings") -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_superadmin": is_superadmin(user, settings),
    }


async def principal_payload(
    session: "AsyncSession",
    user: "User",
    current_tenant_id: uuid.UUID | None,
    settings: "Settings",
) -> dict[str, Any]:
    """`user`, `tenant` (the current one, or None) and `tenants` for a response."""
    items = await memberships(session, user.id)
    current = next(
        (tenant_info(m, t) for m, t in items if t.id == current_tenant_id),
        None,
    )
    return {
        "user": user_item(user, settings),
        "tenant": current,
        "tenants": [tenant_info(m, t) for m, t in items],
    }


async def user_by_email(session: "AsyncSession", email: str) -> "User | None":
    result = await session.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


def token_response(
    settings: "Settings",
    row: "UserSession",
    raw_refresh: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    access, expires_in = issue_access_token(
        settings, user_id=row.user_id, session_id=row.id, tenant_id=row.tenant_id
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": expires_in,
        "refresh_token": raw_refresh,
        "refresh_expires_at": row.expires_at,
        **payload,
    }


def _is_shared_demo_login(settings: "Settings", email: str) -> bool:  # L5a demo
    """True for the two public demo sign-ins on a demo server (never on a normal install)."""
    if not settings.demo_mode:
        return False
    shared = {settings.superadmin_email, settings.demo_owner_email}
    return email in {normalize_email(a) for a in shared if a and a.strip()}


async def login(
    session: "AsyncSession",
    request: "Request",
    limiter: AccountRateLimiter,
    *,
    email: str,
    password: str,
    tenant_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Verify the password and open a session. Returns the `TokenResponse` body."""
    settings: Settings = request.app.state.settings
    email = normalize_email(email)
    ip = request_ip(request)
    await limiter.hit(LOGIN_IP_LIMIT[0], ip, LOGIN_IP_LIMIT[1], LOGIN_IP_LIMIT[2])
    # L5a demo: the demo sign-ins are shared and public, so a per-address limit or lockout would
    # only let anyone lock the demo out. The per-IP limit above stays.
    shared = _is_shared_demo_login(settings, email)
    if not shared:
        await limiter.hit(LOGIN_EMAIL_LIMIT[0], email, LOGIN_EMAIL_LIMIT[1], LOGIN_EMAIL_LIMIT[2])
        await limiter.check_lock(email)
    # /L5a demo

    user = await user_by_email(session, email)
    stored = user.password_hash if user is not None and user.is_active else None
    ok, new_hash = await verify_password(password, stored)
    if not ok or user is None:
        failures = 0 if shared else await limiter.record_failure(email)  # L5a demo
        if failures == LOCK_AFTER:
            from callobuzz_rag.accounts.audit import record_audit

            await record_audit(
                session,
                request,
                "user.login_locked",
                target_type="user",
                target_id=str(user.id) if user is not None else None,
                details={"failures": failures},
            )
            await session.commit()
        raise invalid_credentials()

    await limiter.clear_failures(email)
    if new_hash is not None:
        user.password_hash = new_hash

    chosen: uuid.UUID | None = None
    if is_superadmin(user, settings):
        if tenant_id is not None:  # only their own workspace; the default is the admin view
            if await operator_membership(session, user.id, tenant_id) is None:
                raise not_member()
            chosen = tenant_id
    else:
        if user.email_verified_at is None:
            raise APIError(
                403, "Verify your e-mail address before signing in", "email_not_verified"
            )
        items = await memberships(session, user.id)
        if not items:
            raise APIError(403, "Your account does not belong to any workspace", "no_workspace")
        if tenant_id is not None:
            if all(t.id != tenant_id for _, t in items):
                raise not_member()
            chosen = tenant_id
        else:
            chosen = items[0][1].id

    user.last_login_at = datetime.now(UTC)
    row, raw = await create_session(session, user, chosen, request)
    payload = await principal_payload(session, user, chosen, settings)
    body = token_response(settings, row, raw, payload)
    await session.commit()
    return body


async def refresh(session: "AsyncSession", request: "Request", raw_token: str) -> dict[str, Any]:
    """Rotate the refresh token and issue a new access token.

    If the session's workspace membership is gone but others remain, the session moves to
    the earliest remaining one. A deactivated user, or a non-super-admin with no
    membership left, gets 401 `session_revoked`.
    """
    settings: Settings = request.app.state.settings
    row, raw = await rotate(session, raw_token, request)
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        await session.rollback()
        raise session_revoked()
    if is_superadmin(user, settings):
        if row.tenant_id is not None and (
            await operator_membership(session, user.id, row.tenant_id) is None
        ):
            row.tenant_id = None  # left (or lost) the operator workspace: back to admin
    else:
        items = await memberships(session, user.id)
        if not items:
            await session.rollback()
            raise session_revoked()
        if all(t.id != row.tenant_id for _, t in items):
            row.tenant_id = items[0][1].id
    payload = await principal_payload(session, user, row.tenant_id, settings)
    body = token_response(settings, row, raw, payload)
    await session.commit()
    return body
