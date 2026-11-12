"""/api/v1/auth: dashboard sign-in, sessions and passwords (accounts A3).

People sign in here; machines keep using API keys. `login`, `refresh`, `logout`,
`reset` and (accounts A4) `signup`, `verify`, `resend`, `forgot` are public
(`PUBLIC_PATHS` in api/auth.py); every other route needs a session
(`Authorization: Bearer <access token>`) and answers an API key with 403
`session_required`. Every route answers 503 `accounts_not_configured` while
`RAG_SESSION_SECRET` is unset. Rate limits fail closed (503 when Redis is down).

Nothing here logs a password, code or token.
"""

import hmac
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import service
from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.codes import consume_code
from callobuzz_rag.accounts.passwords import (
    hash_password,
    normalize_email,
    validate_password,
    verify_password,
)
from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
from callobuzz_rag.accounts.sessions import request_ip, revoke, revoke_all
from callobuzz_rag.accounts.tokens import (
    issue_access_token,
    parse_refresh_token,
    session_secrets,
    sha256_hex,
)
from callobuzz_rag.api.deps import get_session, require_session_user
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.auth import (
    ChangePasswordRequest,
    ForgotRequest,
    LoginRequest,
    MeResponse,
    ProfilePatch,
    RefreshRequest,
    ResendRequest,
    ResetRequest,
    ResetResponse,
    RevokedCount,
    SentResponse,
    SessionItem,
    SessionList,
    SignupRequest,
    SignupResponse,
    SwitchAdminResponse,
    SwitchTenantRequest,
    SwitchTenantResponse,
    TokenResponse,
    VerifyRequest,
)
from callobuzz_rag.core.exceptions import AccountsNotConfiguredError
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession

logger = logging.getLogger(__name__)


def require_accounts(request: Request) -> None:
    """503 `accounts_not_configured` while the session secret is missing."""
    try:
        session_secrets(request.app.state.settings)
    except AccountsNotConfiguredError as exc:
        raise APIError(
            503,
            "Accounts are not configured on this engine (RAG_SESSION_SECRET)",
            "accounts_not_configured",
        ) from exc


router = APIRouter(prefix="/api/v1/auth", tags=["auth"], dependencies=[Depends(require_accounts)])


def account_limiter(request: Request) -> AccountRateLimiter:
    """One limiter per app, created on first use (like the widget's)."""
    limiter: AccountRateLimiter | None = getattr(request.app.state, "account_rate_limiter", None)
    if limiter is None:
        limiter = AccountRateLimiter(request.app.state.settings.redis_url)
        request.app.state.account_rate_limiter = limiter
    return limiter


def _weak(reasons: list[str]) -> APIError:
    return APIError(422, "That password is too weak", "weak_password", reasons=reasons)


def _session_id(request: Request) -> uuid.UUID:
    sid: uuid.UUID = request.state.session_id
    return sid


async def _managed_user(session: AsyncSession, request: Request) -> User:
    """The signed-in user, loaded in this request's session (the middleware's is detached)."""
    detached = require_session_user(request)
    user = await session.get(User, detached.id)
    if user is None:
        raise APIError(401, "This session has ended. Sign in again.", "session_revoked")
    return user


def _password_changed_mail(user: User) -> None:
    """Queue the "your password was changed" notice; best-effort, never fails the request."""
    try:
        from callobuzz_rag.accounts.email.dispatch import NOTICE_EXPIRES_S, enqueue_email

        enqueue_email(
            "password_changed",
            user.email,
            {
                "name": user.name,
                "when": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            },
            expires_s=NOTICE_EXPIRES_S,
        )
    except Exception as exc:
        logger.warning("Could not queue the password-changed e-mail: %s", type(exc).__name__)


# ------------------------------------------------------------------ public


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Sign in with e-mail and password. One message for every wrong e-mail or password."""
    return await service.login(
        session,
        request,
        account_limiter(request),
        email=body.email,
        password=body.password,
        tenant_id=body.tenant_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Exchange the refresh token for a new one and a new access token.

    Each refresh token works once. Presenting the one just replaced ends the session.
    """
    limit = service.REFRESH_IP_LIMIT
    await account_limiter(request).hit(limit[0], request_ip(request), limit[1], limit[2])
    return await service.refresh(session, request, body.refresh_token)


@router.post("/logout", status_code=204)
async def logout(
    body: RefreshRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """End the session this refresh token belongs to. 204 whatever the token."""
    limit = service.LOGOUT_IP_LIMIT
    await account_limiter(request).hit(limit[0], request_ip(request), limit[1], limit[2])
    parsed = parse_refresh_token(body.refresh_token)
    if parsed is not None:
        row = await session.get(UserSession, parsed[0])
        if row is not None and hmac.compare_digest(
            row.refresh_token_hash, sha256_hex(body.refresh_token)
        ):
            await revoke(session, row.id, "logout")
            await session.commit()
    return Response(status_code=204)


@router.post("/signup", response_model=SignupResponse, status_code=202)
async def signup(
    body: SignupRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Sign up a new workspace: e-mails a 6-digit code to verify the address.

    The same 202 for every address (an existing account gets an e-mail saying so
    instead). 403 `signup_closed` in closed mode, with the operator's contact text.
    """
    from callobuzz_rag.accounts import signup as signup_service

    return await signup_service.signup(
        session,
        request,
        account_limiter(request),
        business_name=body.business_name,
        name=body.name,
        email=body.email,
        password=body.password,
        accept_legal=body.accept_legal.model_dump(exclude_none=True)  # P3 legal
        if body.accept_legal is not None
        else None,
        plan=body.plan,  # P6 plan choice
    )


@router.post("/verify", response_model=TokenResponse)
async def verify(
    body: VerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Verify the address with the e-mailed code: creates the workspace and signs in.

    `tenant.status` is `active` (open sign-up) or `pending` (waiting for approval).
    """
    from callobuzz_rag.accounts import signup as signup_service

    return await signup_service.verify(
        session, request, account_limiter(request), email=body.email, code=body.code
    )


@router.post("/resend", response_model=SentResponse, status_code=202)
async def resend(
    body: ResendRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Send a new verification or reset code, if one applies. Always the same 202."""
    from callobuzz_rag.accounts import signup as signup_service

    return await signup_service.resend(
        session, request, account_limiter(request), email=body.email, purpose=body.purpose
    )


@router.post("/forgot", response_model=SentResponse, status_code=202)
async def forgot(
    body: ForgotRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """E-mail a password-reset code (use it at /auth/reset). Always the same 202."""
    from callobuzz_rag.accounts import signup as signup_service

    return await signup_service.forgot(session, request, account_limiter(request), email=body.email)


@router.post("/reset", response_model=ResetResponse)
async def reset_password(
    body: ResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ResetResponse:
    """Set a new password with an e-mailed code (reset, or the super admin's first one).

    Ends every session of the account.
    """
    limiter = account_limiter(request)
    email = normalize_email(body.email)
    ip_limit, email_limit = service.RESET_IP_LIMIT, service.RESET_EMAIL_LIMIT
    await limiter.hit(ip_limit[0], request_ip(request), ip_limit[1], ip_limit[2])
    await limiter.hit(email_limit[0], email, email_limit[1], email_limit[2])

    reasons = validate_password(body.new_password, email=email)
    if reasons:
        raise _weak(reasons)  # before the code is consumed: a retry can reuse it

    settings = request.app.state.settings
    await consume_code(
        session,
        settings=settings,
        email=email,
        purposes=("reset", "superadmin_setup"),
        code=body.code,
    )
    user = await service.user_by_email(session, email)
    if user is None:
        await session.commit()  # the code is spent either way
        raise APIError(
            400, "This code has expired or was already used. Ask for a new one.", "code_expired"
        )
    now = datetime.now(UTC)
    user.password_hash = await hash_password(body.new_password)
    user.password_changed_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now
    await revoke_all(session, user.id, "password_reset")
    await record_audit(
        session,
        request,
        "user.password_reset",
        target_type="user",
        target_id=str(user.id),
        actor=("user", user.id, user.email),
    )
    await session.commit()
    try:
        await limiter.clear_failures(email)
    except APIError:
        logger.warning("Could not clear the sign-in lockout after a password reset")
    _password_changed_mail(user)
    return ResetResponse(reset=True)


# ----------------------------------------------------------------- session


@router.get("/me", response_model=MeResponse)
async def me(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The signed-in user, the current workspace and every workspace they belong to."""
    user = require_session_user(request)
    tenant = getattr(request.state, "tenant", None)
    payload = await service.principal_payload(
        session, user, tenant.id if tenant is not None else None, request.app.state.settings
    )
    return {**payload, "session_id": _session_id(request)}


@router.patch("/me", response_model=MeResponse)
async def patch_me(
    body: ProfilePatch,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Change your display name."""
    user = await _managed_user(session, request)
    user.name = body.name.strip() or user.name
    await session.commit()
    tenant = getattr(request.state, "tenant", None)
    payload = await service.principal_payload(
        session, user, tenant.id if tenant is not None else None, request.app.state.settings
    )
    return {**payload, "session_id": _session_id(request)}


@router.post("/change-password", status_code=204)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Change your password. Every other session ends; this one stays."""
    user = await _managed_user(session, request)
    limit = service.CHANGE_PASSWORD_USER_LIMIT
    await account_limiter(request).hit(limit[0], str(user.id), limit[1], limit[2])
    ok, _ = await verify_password(body.current_password, user.password_hash)
    if not ok:
        raise service.invalid_credentials()
    reasons = validate_password(body.new_password, email=user.email)
    if reasons:
        raise _weak(reasons)
    user.password_hash = await hash_password(body.new_password)
    user.password_changed_at = datetime.now(UTC)
    await revoke_all(session, user.id, "password_changed", except_id=_session_id(request))
    await record_audit(
        session, request, "user.password_changed", target_type="user", target_id=str(user.id)
    )
    await session.commit()
    _password_changed_mail(user)
    return Response(status_code=204)


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> SessionList:
    """Your active sessions, newest first."""
    user = require_session_user(request)
    current = _session_id(request)
    now = datetime.now(UTC)
    rows = await session.execute(
        select(UserSession)
        .where(
            UserSession.user_id == user.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
        .order_by(UserSession.created_at.desc())
    )
    return SessionList(
        items=[
            SessionItem(
                id=row.id,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                user_agent=row.user_agent,
                ip=row.ip,
                current=row.id == current,
            )
            for row in rows.scalars().all()
        ]
    )


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """End one of your sessions (your current one = sign out)."""
    user = require_session_user(request)
    row = await session.get(UserSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    reason = "logout" if session_id == _session_id(request) else "revoked"
    await revoke(session, session_id, reason)
    await session.commit()
    return Response(status_code=204)


@router.post("/sessions/revoke-others", response_model=RevokedCount)
async def revoke_others(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> RevokedCount:
    """End every session except this one."""
    user = require_session_user(request)
    count = await revoke_all(session, user.id, "revoke_others", except_id=_session_id(request))
    await session.commit()
    return RevokedCount(revoked=count)


@router.post("/switch-tenant", response_model=SwitchTenantResponse)
async def switch_tenant(
    body: SwitchTenantRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Act for another workspace you belong to. Returns a new access token for it.

    The super admin may enter exactly one workspace this way: the operator's own
    (`is_operator`, where they are the owner). The session then acts as that workspace's
    owner and nothing more: it is a plain workspace scope and cannot reach admin routes.
    `POST /auth/switch-admin` goes back; any other tenant is 403 `not_member`.
    """
    user = require_session_user(request)
    settings = request.app.state.settings
    if service.is_superadmin(user, settings):
        match = await service.operator_membership(session, user.id, body.tenant_id)
    else:
        match = next(
            (
                (m, t)
                for m, t in await service.memberships(session, user.id)
                if t.id == body.tenant_id
            ),
            None,
        )
    if match is None:
        raise service.not_member()
    row = await session.get(UserSession, _session_id(request))
    if row is None or row.revoked_at is not None:
        raise APIError(401, "This session has ended. Sign in again.", "session_revoked")
    row.tenant_id = body.tenant_id
    await session.commit()
    access, expires_in = issue_access_token(
        settings, user_id=user.id, session_id=row.id, tenant_id=body.tenant_id
    )
    member, tenant = match
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": expires_in,
        "tenant": service.tenant_info(member, tenant),
    }


@router.post("/switch-admin", response_model=SwitchAdminResponse)
async def switch_admin(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Super admin only: leave the operator workspace and return to the admin (operator)
    view. Returns a new access token with no workspace (`tenant: null`). Harmless when
    already in the admin view; 403 `not_superadmin` for anyone else."""
    user = require_session_user(request)
    settings = request.app.state.settings
    if not service.is_superadmin(user, settings):
        raise APIError(403, "Only the super admin has an operator view", "not_superadmin")
    row = await session.get(UserSession, _session_id(request))
    if row is None or row.revoked_at is not None:
        raise APIError(401, "This session has ended. Sign in again.", "session_revoked")
    row.tenant_id = None
    await session.commit()
    access, expires_in = issue_access_token(
        settings, user_id=user.id, session_id=row.id, tenant_id=None
    )
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": expires_in,
        "tenant": None,
    }
