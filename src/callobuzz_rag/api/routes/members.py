"""Teams: a tenant's members and invitations (accounts A5).

- `/api/v1/tenants/me/...` (`me_router`): the calling tenant (a `tk_` key or a workspace
  sign-in). Everyone in the workspace may list the team; inviting, re-sending, revoking,
  changing roles and removing people are owner-only (`require_owner`), except that anyone
  may remove themselves (leave).
- `/api/v1/admin/tenants/{tenant_id}/...` (`admin_router`): the operator, on any tenant.
  Only here does an invitation's `accept_url` appear in a response, so the operator can
  onboard someone with no SMTP.
- `/api/v1/invitations/view|accept` (`public_router`): the accept page. Public; the token
  travels in the POST body. `accept` also resolves a Bearer sign-in when one is sent
  (`OPTIONAL_AUTH_PATHS` in api/auth.py).

The rules live in `accounts/members.py`. Routes commit, then queue the e-mail.
"""

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts import members
from callobuzz_rag.accounts.email.factory import email_configured
from callobuzz_rag.accounts.sessions import request_ip
from callobuzz_rag.api.deps import (
    get_authenticated_tenant,
    get_session,
    require_admin,
    require_owner,
)
from callobuzz_rag.api.routes.auth import account_limiter, require_accounts
from callobuzz_rag.api.schemas.auth import TokenResponse
from callobuzz_rag.api.schemas.members import (
    AcceptedResponse,
    AcceptInvitationRequest,
    AdminInvitationOut,
    InvitationOut,
    InvitationTokenRequest,
    InvitationView,
    InviteRequest,
    MemberOut,
    RoleChangeRequest,
    TeamResponse,
)
from callobuzz_rag.cob.system import refuse_system_tenant_write
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

me_router = APIRouter(prefix="/api/v1/tenants/me", tags=["team"])
admin_router = APIRouter(prefix="/api/v1/admin/tenants/{tenant_id}", tags=["team"])
public_router = APIRouter(
    prefix="/api/v1/invitations", tags=["team"], dependencies=[Depends(require_accounts)]
)


async def _hit(request: Request, limit: tuple[str, int, int], identity: str) -> None:
    await account_limiter(request).hit(limit[0], identity, limit[1], limit[2])


# ------------------------------------------------------------------- tenant


@me_router.get("/members", response_model=TeamResponse)
async def list_own_team(
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The workspace's members and open invitations. Any member of the workspace."""
    return await members.list_team(session, tenant.id)


@me_router.post("/members", response_model=InvitationOut, status_code=201)
async def invite_member(
    body: InviteRequest,
    request: Request,
    _owner: None = Depends(require_owner),
    tenant: Tenant = Depends(get_authenticated_tenant),
    _accounts: None = Depends(require_accounts),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Invite someone by e-mail as `owner` or `member`. Owners only.

    They get a link valid 7 days. Inviting an address that has an open invitation
    replaces it (the old link stops working). The link is only ever e-mailed.
    """
    await _hit(request, members.INVITE_TENANT_LIMIT, str(tenant.id))
    inv, token = await members.invite(
        session, tenant, email=body.email, role=body.role, request=request, for_admin=False
    )
    await session.commit()
    members.send_invitation(request.app.state.settings, tenant.name, inv, token, strict=True)
    return members.invitation_item(inv)


@me_router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_own_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    _owner: None = Depends(require_owner),
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke an open invitation: its link stops working. Owners only."""
    await members.revoke_invitation(session, tenant, invitation_id, request=request)
    await session.commit()
    return Response(status_code=204)


@me_router.post("/invitations/{invitation_id}/resend", response_model=InvitationOut)
async def resend_own_invitation(
    invitation_id: uuid.UUID,
    request: Request,
    _owner: None = Depends(require_owner),
    tenant: Tenant = Depends(get_authenticated_tenant),
    _accounts: None = Depends(require_accounts),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """E-mail the invitation again with a new link and 7 more days. Owners only."""
    await _hit(request, members.INVITE_TENANT_LIMIT, str(tenant.id))
    inv, token = await members.resend_invitation(session, tenant, invitation_id, request=request)
    await session.commit()
    members.send_invitation(request.app.state.settings, tenant.name, inv, token, strict=True)
    return members.invitation_item(inv)


@me_router.patch("/members/{user_id}", response_model=MemberOut)
async def change_own_member_role(
    user_id: uuid.UUID,
    body: RoleChangeRequest,
    request: Request,
    _owner: None = Depends(require_owner),
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Make someone an owner or a member. Owners only; the last owner stays an owner."""
    item = await members.change_role(session, tenant, user_id, body.role, request=request)
    await session.commit()
    return item


@me_router.delete("/members/{user_id}", status_code=204)
async def remove_own_member(
    user_id: uuid.UUID,
    request: Request,
    tenant: Tenant = Depends(get_authenticated_tenant),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove someone from the workspace (owners), or leave it (anyone, with their own
    user id). Their sessions in this workspace end; the account stays. The last owner
    cannot leave."""
    caller = getattr(request.state, "user", None)
    if caller is None or caller.id != user_id:
        require_owner(request)
    await members.remove_member(session, tenant, user_id, request=request)
    await session.commit()
    return Response(status_code=204)


# -------------------------------------------------------------------- admin


async def _tenant(
    session: AsyncSession, tenant_id: uuid.UUID, request: Request | None = None
) -> Tenant:
    """The tenant, 404 when absent. With the request, a write on COB's own workspace is refused."""
    tenant = await session.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if request is not None:
        refuse_system_tenant_write(request, tenant)
    return tenant


@admin_router.get("/members", response_model=TeamResponse)
async def admin_list_team(
    tenant_id: uuid.UUID,
    _admin: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A tenant's members and open invitations. Admin only."""
    tenant = await _tenant(session, tenant_id)
    return await members.list_team(session, tenant.id)


@admin_router.post("/invitations", response_model=AdminInvitationOut, status_code=201)
async def admin_invite(
    tenant_id: uuid.UUID,
    body: InviteRequest,
    request: Request,
    _admin: None = Depends(require_admin),
    _accounts: None = Depends(require_accounts),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Invite someone to any tenant. Admin only.

    The response carries `accept_url`: send it yourself if the engine has no SMTP (the
    e-mail goes out too when it does), e.g. to onboard a customer's owner in closed mode.
    """
    tenant = await _tenant(session, tenant_id, request)
    inv, token = await members.invite(
        session, tenant, email=body.email, role=body.role, request=request, for_admin=True
    )
    await session.commit()
    settings = request.app.state.settings
    if email_configured(settings):
        members.send_invitation(settings, tenant.name, inv, token, strict=False)
    return {**members.invitation_item(inv), "accept_url": members.accept_url(settings, token)}


@admin_router.post("/invitations/{invitation_id}/resend", response_model=AdminInvitationOut)
async def admin_resend_invitation(
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    _admin: None = Depends(require_admin),
    _accounts: None = Depends(require_accounts),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """A new link and 7 more days for a tenant's open invitation (the old link stops
    working). Admin only; like the admin invitation, the response carries `accept_url` and
    the e-mail goes out too when the engine has SMTP."""
    tenant = await _tenant(session, tenant_id, request)
    inv, token = await members.resend_invitation(
        session, tenant, invitation_id, request=request, for_admin=True
    )
    await session.commit()
    settings = request.app.state.settings
    if email_configured(settings):
        members.send_invitation(settings, tenant.name, inv, token, strict=False)
    return {**members.invitation_item(inv), "accept_url": members.accept_url(settings, token)}


@admin_router.delete("/invitations/{invitation_id}", status_code=204)
async def admin_revoke_invitation(
    tenant_id: uuid.UUID,
    invitation_id: uuid.UUID,
    request: Request,
    _admin: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke a tenant's open invitation. Admin only."""
    tenant = await _tenant(session, tenant_id, request)
    await members.revoke_invitation(session, tenant, invitation_id, request=request)
    await session.commit()
    return Response(status_code=204)


@admin_router.patch("/members/{user_id}", response_model=MemberOut)
async def admin_change_role(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    body: RoleChangeRequest,
    request: Request,
    _admin: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Change a member's role in any tenant. Admin only; the last owner stays an owner."""
    tenant = await _tenant(session, tenant_id, request)
    item = await members.change_role(session, tenant, user_id, body.role, request=request)
    await session.commit()
    return item


@admin_router.delete("/members/{user_id}", status_code=204)
async def admin_remove_member(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    _admin: None = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove someone from any tenant (e.g. the owner left the company, after promoting
    someone else). Admin only; the last owner cannot be removed."""
    tenant = await _tenant(session, tenant_id, request)
    await members.remove_member(session, tenant, user_id, request=request)
    await session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------- public


@public_router.post("/view", response_model=InvitationView)
async def view_invitation(
    body: InvitationTokenRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """What the accept page shows: workspace, role, address, and whether that address
    already has an account (then sign in to accept). 404 `invitation_invalid` for an
    unknown, expired, revoked or used link."""
    await _hit(request, members.VIEW_IP_LIMIT, request_ip(request))
    return await members.view_invitation(session, body.token)


@public_router.post("/accept", response_model=TokenResponse | AcceptedResponse)
async def accept_invitation(
    body: AcceptInvitationRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Accept an invitation.

    - Signed in (`Authorization: Bearer`) as the invited address: joins the workspace and
      answers `{"accepted": true, "tenant": ...}` (then `POST /auth/switch-tenant`).
    - Not signed in, no account yet: send `name` and `password`; the account is created
      (already verified), joins, and the answer is a sign-in (`TokenResponse`).
    - Not signed in, account exists: 409 `account_exists` (sign in, then accept).
    """
    await _hit(request, members.ACCEPT_IP_LIMIT, request_ip(request))
    return await members.accept_invitation(
        session, body.token, name=body.name, password=body.password, request=request
    )
