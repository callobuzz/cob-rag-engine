"""Request and response bodies of the support routes (TV3): a business writes to the provider
that runs the product, the provider answers, both see one thread."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints

from callobuzz_rag.core.schemas import StrictRequest

SupportStatus = Literal["open", "answered", "waiting", "closed"]
ClientError = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]+$", max_length=40)]


class SupportRequestCreate(StrictRequest):
    subject: str = Field(..., min_length=3, max_length=150)
    message: str = Field(..., min_length=1, max_length=10_000)
    page: str | None = Field(
        None, max_length=300, pattern=r"^/", description="The dashboard page it was written on"
    )
    client_errors: list[ClientError] = Field(
        default_factory=list,
        max_length=3,
        description="The last error codes the dashboard saw (codes only, never text)",
    )


class SupportMessageCreate(StrictRequest):
    body: str = Field(..., min_length=1, max_length=10_000)


class SupportMessageOut(BaseModel):
    id: uuid.UUID
    author_kind: Literal["business", "deployer"]
    author: str | None = Field(
        None, description="Who wrote it for a business's own people; null for the provider"
    )
    body: str
    created_at: datetime


class SupportRequestOut(BaseModel):
    id: uuid.UUID
    number: int
    subject: str
    status: SupportStatus
    page: str | None = None
    created_by_email: str
    created_at: datetime
    last_message_at: datetime
    first_response_at: datetime | None = None
    closed_at: datetime | None = None


class SupportRequestDetail(SupportRequestOut):
    messages: list[SupportMessageOut]


class SupportListOut(BaseModel):
    items: list[SupportRequestOut]
    total: int


class SupportInfoOut(BaseModel):
    enabled: bool = Field(description="False when the provider switched support requests off")
    provider_name: str
    sla_hours: int | None = Field(None, description="'Usually replies within N hours', if set")
    open_count: int = Field(description="This workspace's requests that are not closed")


# ------------------------------------------------------------------ the provider's inbox


class AdminSupportRequestOut(SupportRequestOut):
    tenant_id: uuid.UUID
    workspace_name: str
    source: Literal["workspace", "website"] = Field(
        "workspace", description="`website`: written by a visitor on the public contact page"
    )


class AdminSupportMessageOut(BaseModel):
    id: uuid.UUID
    author_kind: Literal["business", "deployer"]
    author_email: str
    body: str
    created_at: datetime


class AdminSupportDetail(AdminSupportRequestOut):
    context: dict[str, Any]
    messages: list[AdminSupportMessageOut]


class AdminSupportListOut(BaseModel):
    items: list[AdminSupportRequestOut]
    total: int


class AdminReplyRequest(StrictRequest):
    body: str = Field(..., min_length=1, max_length=10_000)
    status: Literal["answered", "waiting", "closed"] | None = Field(
        None, description="What the request becomes; omitted: answered"
    )


class AdminStatusRequest(StrictRequest):
    status: SupportStatus


class SupportStatsOut(BaseModel):
    open: int = Field(description="Requests waiting for the provider")
    answered: int = Field(description="Requests in the window that have had a first reply")
    median_first_response_minutes: int | None = None
    within_sla_pct: int | None = Field(
        None, description="Share answered within the promised time; null without an SLA"
    )
    sla_hours: int | None = None
    days: int
