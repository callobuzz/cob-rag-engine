"""Teams: invitations, roles and removal (accounts A5).

An owner (a workspace owner's sign-in or the tenant's `tk_` key) or the operator invites
an address as `owner` or `member`. The e-mailed link is `{RAG_DASHBOARD_URL}/invite#token=
inv_...`: the token lives in the fragment, and the dashboard sends it in a POST body, so it
never reaches an access log. Only its sha256 is stored. One open invitation per address:
inviting again revokes the old link. A link works once and for 7 days.

Accepting:
- with a sign-in whose e-mail is the invitation's: joins (an existing membership keeps its
  role; the call is idempotent);
- without one: creates the account, already verified (holding the e-mailed link proves the
  address), joins and signs in. An address that already has an account must sign in first
  (409 `account_exists`).

The last owner can neither leave, be removed nor be demoted: the tenant's owner rows are
locked (`SELECT ... FOR UPDATE`) before the check, so two owners demoting each other at
once cannot both succeed. Removal deletes the membership and ends the sessions acting for
that tenant; the account stays (it may belong to other workspaces).

Nothing here commits; the routes commit, then queue the e-mail. Nothing logs a token.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from fastapi import HTTPException
from sqlalchemy import delete, select, update

from callobuzz_rag.accounts import service
from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.accounts.email.factory import email_configured
from callobuzz_rag.accounts.lifecycle import PendingEmails
from callobuzz_rag.accounts.passwords import hash_password, normalize_email, validate_password
from callobuzz_rag.accounts.sessions import create_session
from callobuzz_rag.accounts.tokens import sha256_hex
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.exceptions import EmailError
from callobuzz_rag.db.models.invitation import Invitation
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.tenant_member import TenantMember
from callobuzz_rag.db.models.user import User
from callobuzz_rag.db.models.user_session import UserSession

if TYPE_CHECKING:
    from fastapi import Request
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

INVITATION_DAYS: Final = 7
TOKEN_PREFIX: Final = "inv_"
# (bucket, limit, window seconds). Invites and re-sends by a tenant share one bucket.
INVITE_TENANT_LIMIT: Final = ("invite", 20, 3600)
VIEW_IP_LIMIT: Final = ("invitation_view_ip", 20, 600)
ACCEPT_IP_LIMIT: Final = ("invitation_accept_ip", 20, 600)


# ------------------------------------------------------------------ errors


def invitation_invalid() -> APIError:
    """Unknown, expired, revoked or used: one answer for all four."""
    return APIError(
        404, "This invitation is not valid any more. Ask for a new one.", "invitation_invalid"
    )


def _superadmin_email() -> APIError:
    return APIError(409, "This address cannot be invited to a workspace", "superadmin_email")


def _member_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Member not found")


def _invitation_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Invitation not found")


def email_not_configured() -> APIError:
    return APIError(503, "E-mail is not configured on this engine", "email_not_configured")


def email_unavailable() -> APIError:
    return APIError(
        503, "We could not send the e-mail right now. Try again shortly.", "email_unavailable"
    )


# ------------------------------------------------------------------ helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _settings(request: "Request") -> "Settings":
    settings: Settings = request.app.state.settings
    return settings


def _is_superadmin_address(email: str, settings: "Settings") -> bool:
    return bool(settings.superadmin_email) and email == normalize_email(settings.superadmin_email)


def new_token() -> str:
    """`inv_` + 256 random bits."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def accept_url(settings: "Settings", token: str) -> str:
    """The dashboard's accept page, the token in the fragment."""
    from callobuzz_rag.accounts.email.render import dashboard_link

    return dashboard_link(settings.dashboard_url, "/invite", {"token": token})


def is_open(inv: Invitation, now: datetime | None = None) -> bool:
    """Neither accepted nor revoked, and not expired."""
    now = now or _now()
    return inv.accepted_at is None and inv.revoked_at is None and inv.expires_at > now


def member_item(member: TenantMember, user: User) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": member.role,
        "joined_at": member.created_at,
        "last_login_at": user.last_login_at,
    }


def invitation_item(inv: Invitation) -> dict[str, Any]:
    return {
        "id": inv.id,
        "email": inv.email,
        "role": inv.role,
        "invited_by": inv.invited_by_label,
        "created_at": inv.created_at,
        "expires_at": inv.expires_at,
    }


def _inviter(request: "Request") -> tuple[uuid.UUID | None, str]:
    """(user id, label) of whoever is inviting: a person's name, "admin" or "tenant key"."""
    state = request.state
    user = getattr(state, "user", None)
    if user is not None:
        return user.id, str(user.name or user.email)[:320]
    if getattr(state, "is_admin", False):
        return None, "admin"
    return None, "tenant key"


async def _member_rows(
    session: "AsyncSession", tenant_id: uuid.UUID
) -> list[tuple[TenantMember, User]]:
    rows = await session.execute(
        select(TenantMember, User)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == tenant_id)
    )
    pairs = [(m, u) for m, u in rows.all()]
    pairs.sort(key=lambda p: (p[0].created_at or _now(), str(p[1].email)))
    return pairs


async def _membership(
    session: "AsyncSession", tenant_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
) -> TenantMember | None:
    stmt = select(TenantMember).where(
        TenantMember.tenant_id == tenant_id, TenantMember.user_id == user_id
    )
    if lock:
        stmt = stmt.with_for_update()
    member: TenantMember | None = (await session.execute(stmt)).scalar_one_or_none()
    return member


async def _lock_owners(session: "AsyncSession", tenant_id: uuid.UUID) -> list[TenantMember]:
    """The tenant's owner rows, locked until the transaction ends.

    Rows, not `count(*)`: PostgreSQL refuses FOR UPDATE with an aggregate. A concurrent
    demotion waits here, then re-reads the rows, so it sees the other one's change.
    """
    rows = await session.execute(
        select(TenantMember)
        .where(TenantMember.tenant_id == tenant_id, TenantMember.role == "owner")
        .order_by(TenantMember.user_id)
        .with_for_update()
    )
    return list(rows.scalars().all())


def _last_owner() -> APIError:
    return APIError(
        409,
        "A workspace needs at least one owner. Make someone else an owner first.",
        "last_owner",
    )


# -------------------------------------------------------------------- team


async def list_team(session: "AsyncSession", tenant_id: uuid.UUID) -> dict[str, Any]:
    """Members (earliest first) and open, unexpired invitations (newest first)."""
    members = [member_item(m, u) for m, u in await _member_rows(session, tenant_id)]
    now = _now()
    rows = await session.execute(
        select(Invitation).where(
            Invitation.tenant_id == tenant_id,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
    )
    open_ = [inv for inv in rows.scalars().all() if inv.expires_at > now]
    open_.sort(key=lambda i: i.created_at or now, reverse=True)
    return {"members": members, "invitations": [invitation_item(i) for i in open_]}


async def invite(
    session: "AsyncSession",
    tenant: Any,
    *,
    email: str,
    role: str,
    request: "Request",
    for_admin: bool,
) -> tuple[Invitation, str]:
    """Create an invitation (revoking an open one for the same address). Returns the row
    and the raw token, which the caller e-mails (and, for the admin, returns).

    Raises:
        APIError 409 `superadmin_email` / `already_member`; 503 `email_not_configured`
        (owners only: the admin gets the link in the response instead).
    """
    settings = _settings(request)
    email = normalize_email(email)
    if _is_superadmin_address(email, settings):
        raise _superadmin_email()
    if not for_admin and not email_configured(settings):
        raise email_not_configured()

    # One invite at a time per tenant: the revoke-then-insert below cannot race another.
    await session.execute(select(Tenant.id).where(Tenant.id == tenant.id).with_for_update())
    already = await session.execute(
        select(TenantMember.user_id)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == tenant.id, User.email == email)
    )
    if already.first() is not None:
        raise APIError(409, "This person is already in the workspace", "already_member")

    now = _now()
    await session.execute(
        update(Invitation)
        .where(
            Invitation.tenant_id == tenant.id,
            Invitation.email == email,
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    token = new_token()
    inviter_id, label = _inviter(request)
    inv = Invitation(
        tenant_id=tenant.id,
        email=email,
        role=role,
        token_hash=sha256_hex(token),
        invited_by_user_id=inviter_id,
        invited_by_label=label,
        expires_at=now + timedelta(days=INVITATION_DAYS),
        created_at=now,
    )
    session.add(inv)
    await session.flush()
    await record_audit(
        session,
        request,
        "member.invited",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="invitation",
        target_id=str(inv.id),
        details={"email": email, "role": role},
    )
    return inv, token


async def _open_invitation_of(
    session: "AsyncSession", tenant_id: uuid.UUID, invitation_id: uuid.UUID
) -> Invitation:
    """This tenant's invitation that is neither accepted nor revoked (expired is fine:
    it can still be re-sent or revoked). 404 otherwise."""
    inv: Invitation | None = (
        await session.execute(
            select(Invitation).where(Invitation.id == invitation_id).with_for_update()
        )
    ).scalar_one_or_none()
    if (
        inv is None
        or inv.tenant_id != tenant_id
        or inv.accepted_at is not None
        or inv.revoked_at is not None
    ):
        raise _invitation_not_found()
    return inv


async def resend_invitation(
    session: "AsyncSession",
    tenant: Any,
    invitation_id: uuid.UUID,
    *,
    request: "Request",
    for_admin: bool = False,
) -> tuple[Invitation, str]:
    """A new token and 7 more days for an open invitation; the old link stops working.

    `for_admin`: the operator gets the new link in the response, so e-mail is optional
    (as for `invite`).

    Raises:
        HTTPException 404; APIError 503 `email_not_configured` (owners only).
    """
    if not for_admin and not email_configured(_settings(request)):
        raise email_not_configured()
    inv = await _open_invitation_of(session, tenant.id, invitation_id)
    token = new_token()
    inv.token_hash = sha256_hex(token)
    inv.expires_at = _now() + timedelta(days=INVITATION_DAYS)
    await record_audit(
        session,
        request,
        "member.invited",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="invitation",
        target_id=str(inv.id),
        details={"email": inv.email, "role": inv.role, "resent": True},
    )
    return inv, token


async def revoke_invitation(
    session: "AsyncSession", tenant: Any, invitation_id: uuid.UUID, *, request: "Request"
) -> None:
    """Revoke an open invitation. 404 when it is not this tenant's or not open."""
    inv = await _open_invitation_of(session, tenant.id, invitation_id)
    inv.revoked_at = _now()
    await record_audit(
        session,
        request,
        "invitation.revoked",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="invitation",
        target_id=str(inv.id),
        details={"email": inv.email},
    )


def invitation_mail(
    settings: "Settings", tenant_name: str, inv: Invitation, token: str
) -> PendingEmails:
    emails = PendingEmails()
    emails.add(
        "invitation",
        inv.email,
        {
            "inviter_name": inv.invited_by_label,
            "tenant_name": tenant_name,
            "role": inv.role,
            "accept_url": accept_url(settings, token),
            "days": INVITATION_DAYS,
        },
    )
    return emails


def send_invitation(
    settings: "Settings", tenant_name: str, inv: Invitation, token: str, *, strict: bool
) -> None:
    """Queue the invitation e-mail (after the commit). `strict`: a broker failure is 503
    `email_unavailable` (an owner has no other way to deliver the link); otherwise it is
    logged (the admin has the link in the response)."""
    try:
        invitation_mail(settings, tenant_name, inv, token).send(strict=strict)
    except EmailError as exc:
        raise email_unavailable() from exc


# ------------------------------------------------------------ view / accept


async def _by_token(session: "AsyncSession", token: str, *, lock: bool) -> Invitation:
    if not token.startswith(TOKEN_PREFIX):
        raise invitation_invalid()
    stmt = select(Invitation).where(Invitation.token_hash == sha256_hex(token))
    if lock:
        stmt = stmt.with_for_update()
    inv: Invitation | None = (await session.execute(stmt)).scalar_one_or_none()
    if inv is None or not is_open(inv):
        raise invitation_invalid()
    return inv


async def view_invitation(session: "AsyncSession", token: str) -> dict[str, Any]:
    """What the accept page shows. Only the holder of the link sees the address."""
    inv = await _by_token(session, token, lock=False)
    tenant = await session.get(Tenant, inv.tenant_id)
    if tenant is None:
        raise invitation_invalid()
    user = await service.user_by_email(session, inv.email)
    return {
        "tenant_name": tenant.name,
        "role": inv.role,
        "email": inv.email,
        "invited_by": inv.invited_by_label,
        "expires_at": inv.expires_at,
        "account_exists": user is not None and user.email_verified_at is not None,
    }


def _tenant_info(tenant: Tenant, role: str) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "name": tenant.name,
        "role": role,
        "status": service.tenant_status(tenant),
    }


async def _join(
    session: "AsyncSession", inv: Invitation, user: User, request: "Request", tenant: Tenant
) -> str:
    """Add the membership (unless there is one), mark the invitation accepted and audit.
    Returns the user's role in the tenant."""
    existing = await _membership(session, inv.tenant_id, user.id, lock=True)
    if existing is None:
        session.add(TenantMember(tenant_id=inv.tenant_id, user_id=user.id, role=inv.role))
        await session.flush()
        role = inv.role
    else:
        role = existing.role
    inv.accepted_at = _now()
    inv.accepted_by_user_id = user.id
    await record_audit(
        session,
        request,
        "invitation.accepted",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="invitation",
        target_id=str(inv.id),
        details={"user_id": str(user.id), "role": role, "already_member": existing is not None},
        actor=("user", user.id, user.email),
    )
    return role


async def accept_invitation(
    session: "AsyncSession",
    token: str,
    *,
    name: str | None,
    password: str | None,
    request: "Request",
) -> dict[str, Any]:
    """Accept. With a sign-in: `{"accepted": true, "tenant": ...}`. Without: the new
    account's `TokenResponse` body, signed in to the invited workspace.

    Raises:
        APIError 404 `invitation_invalid`; 403 `invitation_email_mismatch`; 409
        `account_exists` / `superadmin_email`; 422 `weak_password`; HTTPException 422 when
        a new account lacks a name or password.
    """
    settings = _settings(request)
    inv = await _by_token(session, token, lock=True)
    tenant = await session.get(Tenant, inv.tenant_id)
    if tenant is None:
        raise invitation_invalid()

    signed_in = getattr(request.state, "user", None)
    if signed_in is not None:
        user = await session.get(User, signed_in.id)
        if user is None:
            raise APIError(401, "This session has ended. Sign in again.", "session_revoked")
        if user.email != inv.email:
            raise APIError(
                403,
                "This invitation was sent to a different e-mail address",
                "invitation_email_mismatch",
            )
        role = await _join(session, inv, user, request, tenant)
        await session.commit()
        return {"accepted": True, "tenant": _tenant_info(tenant, role)}

    if _is_superadmin_address(inv.email, settings):
        raise _superadmin_email()
    existing = await service.user_by_email(session, inv.email)
    if existing is not None and existing.email_verified_at is not None:
        raise APIError(
            409, "An account with this e-mail exists. Sign in to accept.", "account_exists"
        )
    if not name or not name.strip() or not password:
        raise HTTPException(
            status_code=422, detail="A name and a password are needed to create your account"
        )
    reasons = validate_password(password, email=inv.email)
    if reasons:
        raise APIError(422, "That password is too weak", "weak_password", reasons=reasons)

    now = _now()
    password_hash = await hash_password(password)
    if existing is None:
        user = User(email=inv.email, name=name.strip(), password_hash=password_hash)
        session.add(user)
    else:  # a sign-up that never verified: the invitation's link verifies the address
        user = existing
        user.name = name.strip()
        user.password_hash = password_hash
    user.email_verified_at = now
    user.password_changed_at = now
    user.last_login_at = now
    await session.flush()
    await _join(session, inv, user, request, tenant)
    row, raw = await create_session(session, user, tenant.id, request)
    payload = await service.principal_payload(session, user, tenant.id, settings)
    body = service.token_response(settings, row, raw, payload)
    await session.commit()
    return body


# ------------------------------------------------------------ roles / removal


async def change_role(
    session: "AsyncSession", tenant: Any, user_id: uuid.UUID, role: str, *, request: "Request"
) -> dict[str, Any]:
    """Make a member an owner or an owner a member. 404 not a member; 409 `last_owner`."""
    owners = await _lock_owners(session, tenant.id)
    member = await _membership(session, tenant.id, user_id, lock=True)
    user = await session.get(User, user_id)
    if member is None or user is None:
        raise _member_not_found()
    if member.role != role:
        if member.role == "owner" and len(owners) <= 1:
            raise _last_owner()
        previous = member.role
        member.role = role
        await record_audit(
            session,
            request,
            "member.role_changed",
            tenant_id=tenant.id,
            tenant_label=tenant.name,
            target_type="user",
            target_id=str(user_id),
            details={"email": user.email, "from": previous, "to": role},
        )
    return member_item(member, user)


async def remove_member(
    session: "AsyncSession", tenant: Any, user_id: uuid.UUID, *, request: "Request"
) -> None:
    """Remove someone (or, when they are the caller, leave). Ends their sessions acting
    for this tenant; the account stays. 404 not a member; 409 `last_owner`."""
    owners = await _lock_owners(session, tenant.id)
    member = await _membership(session, tenant.id, user_id, lock=True)
    if member is None:
        raise _member_not_found()
    if member.role == "owner" and len(owners) <= 1:
        raise _last_owner()
    user = await session.get(User, user_id)
    role = member.role
    await session.execute(
        delete(TenantMember).where(
            TenantMember.tenant_id == tenant.id, TenantMember.user_id == user_id
        )
    )
    revoked = await session.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.tenant_id == tenant.id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now(), revoked_reason="member_removed")
    )
    caller = getattr(request.state, "user", None)
    await record_audit(
        session,
        request,
        "member.removed",
        tenant_id=tenant.id,
        tenant_label=tenant.name,
        target_type="user",
        target_id=str(user_id),
        details={
            "email": user.email if user is not None else None,
            "role": role,
            "left": caller is not None and caller.id == user_id,
            "sessions_revoked": int(getattr(revoked, "rowcount", 0) or 0),
        },
    )
