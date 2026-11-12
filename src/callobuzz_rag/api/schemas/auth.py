"""Request and response schemas for /api/v1/auth (accounts A3).

Request models forbid unknown fields. E-mail fields are plain strings, normalized by the
service: a malformed address is simply an unknown one (401 on login), never a 422 that
would treat it differently.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from callobuzz_rag.api.schemas.legal import AcceptLegal  # P3 legal

_EMAIL = Field(min_length=1, max_length=320)
# Login accepts up to 1024 characters (hashing input stays bounded); new passwords are
# checked against the 128-character policy with a `weak_password` 422.
_PASSWORD = Field(min_length=1, max_length=1024)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = _EMAIL
    password: str = _PASSWORD
    tenant_id: UUID | None = Field(
        None, description="Workspace to sign in to; default: the earliest you joined."
    )


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1, max_length=256)


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=1, max_length=255, description="The workspace's name.")
    name: str = Field(min_length=1, max_length=255, description="Your name.")
    email: str = _EMAIL
    password: str = _PASSWORD
    # P3 legal: required only while the operator has published both the Terms and the Privacy
    # notice; the versions the person was shown (`GET /public/legal`)
    accept_legal: AcceptLegal | None = Field(
        None,
        description="The Terms and Privacy versions you agree to; required when the operator "
        "has published both pages",
    )
    # /P3 legal
    # P6 plan choice
    plan: str | None = Field(
        None,
        max_length=64,
        description="The plan chosen on the pricing page (`?plan=<slug or id>`); only a plan "
        "businesses may choose themselves counts, anything else starts on the default plan",
    )
    # /P6 plan choice


class SignupResponse(BaseModel):
    status: Literal["verification_sent"] = "verification_sent"
    email: str = Field(description="The normalized address the code was sent to.")


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = _EMAIL
    code: str = Field(min_length=1, max_length=16, description="The 6-digit code e-mailed.")


class ResendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = _EMAIL
    purpose: Literal["verify", "reset"]


class ForgotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = _EMAIL


class SentResponse(BaseModel):
    status: Literal["sent_if_applicable"] = "sent_if_applicable"


class ResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = _EMAIL
    code: str = Field(min_length=1, max_length=16, description="The 6-digit code e-mailed.")
    new_password: str = _PASSWORD


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = _PASSWORD
    new_password: str = _PASSWORD


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)


class SwitchTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID


class UserInfo(BaseModel):
    id: UUID
    email: str
    name: str
    is_superadmin: bool


class TenantInfo(BaseModel):
    id: UUID
    name: str
    role: Literal["owner", "member"]
    status: Literal["active", "suspended", "pending"]
    is_operator: bool = Field(
        False, description="The operator's own workspace (a super admin may switch into it)"
    )


class PlanChoiceInfo(BaseModel):  # P6 plan choice
    """The plan chosen at sign-up and what happens next."""

    status: Literal["active", "awaiting_payment", "on_approval"] = Field(
        description="`active`: the workspace is on it; `awaiting_payment`: it starts once paid "
        "(the Plan & billing page); `on_approval`: it applies when the workspace is approved"
    )
    plan_id: UUID
    plan_name: str
    amount_due_minor: int = 0


class TokenResponse(BaseModel):
    access_token: str = Field(description="JWT; send as Authorization: Bearer <token>.")
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access-token lifetime in seconds.")
    refresh_token: str = Field(description="Rotating; exchange at /api/v1/auth/refresh.")
    refresh_expires_at: datetime
    user: UserInfo
    tenant: TenantInfo | None = Field(description="Current workspace; null for the super admin.")
    tenants: list[TenantInfo]
    plan_choice: PlanChoiceInfo | None = Field(  # P6 plan choice
        None, description="Sign-up only: the plan chosen on the pricing page, if any"
    )


class MeResponse(BaseModel):
    user: UserInfo
    tenant: TenantInfo | None
    tenants: list[TenantInfo]
    session_id: UUID


class ResetResponse(BaseModel):
    reset: bool = True


class SessionItem(BaseModel):
    id: UUID
    created_at: datetime | None
    last_used_at: datetime | None
    expires_at: datetime
    user_agent: str | None
    ip: str | None
    current: bool


class SessionList(BaseModel):
    items: list[SessionItem]


class RevokedCount(BaseModel):
    revoked: int


class SwitchTenantResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    tenant: TenantInfo


class SwitchAdminResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    tenant: None = Field(None, description="Always null: the admin view has no workspace")
