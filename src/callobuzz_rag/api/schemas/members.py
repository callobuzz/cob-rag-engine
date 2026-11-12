"""Request and response schemas for teams and invitations (accounts A5).

Request models forbid unknown fields. Invitation tokens only ever travel in a POST body
(never a path or query string, which end up in access logs and `Referer`).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.api.schemas.auth import TenantInfo

Role = Literal["owner", "member"]


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    name: str
    role: Role
    joined_at: datetime | None
    last_login_at: datetime | None


class InvitationOut(BaseModel):
    id: UUID
    email: str
    role: Role
    invited_by: str = Field(description="The inviter's name, 'tenant key' or 'admin'.")
    created_at: datetime | None
    expires_at: datetime


class AdminInvitationOut(InvitationOut):
    accept_url: str = Field(
        description=(
            "The link the invitee opens (the token is in its #fragment). Only the operator "
            "sees it: send it yourself when the engine has no SMTP."
        )
    )


class TeamResponse(BaseModel):
    members: list[MemberOut]
    invitations: list[InvitationOut] = Field(description="Open, unexpired invitations.")


class InviteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: ValidatedEmail = Field(max_length=320)
    role: Role


class RoleChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class InvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128, description="The inv_... token.")


class InvitationView(BaseModel):
    tenant_name: str
    role: Role
    email: str = Field(description="The address the invitation was sent to.")
    invited_by: str
    expires_at: datetime
    account_exists: bool = Field(
        description="True: sign in as this address to accept. False: set a name and password."
    )


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=128, description="The inv_... token.")
    name: str | None = Field(
        None, min_length=1, max_length=255, description="Your name (new accounts only)."
    )
    password: str | None = Field(
        None, min_length=1, max_length=1024, description="Your password (new accounts only)."
    )


class AcceptedResponse(BaseModel):
    """Accepted with a session: switch to the workspace with /auth/switch-tenant."""

    accepted: Literal[True] = True
    tenant: TenantInfo
