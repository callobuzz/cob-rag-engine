"""Webhook API request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from callobuzz_rag.core.events import WebhookEvent
from callobuzz_rag.core.schemas import StrictRequest


class WebhookCreateRequest(StrictRequest):
    """Request body for creating a webhook subscription."""

    url: HttpUrl = Field(
        ...,
        description="HTTPS endpoint URL to receive webhook POST requests.",
    )
    events: list[WebhookEvent] = Field(
        ...,
        min_length=1,
        description="List of event types to subscribe to.",
    )
    secret: str = Field(
        ...,
        min_length=16,
        max_length=256,
        description="Shared secret for HMAC-SHA256 signature verification (min 16 chars).",
    )


class WebhookUpdateRequest(BaseModel):
    """Pause or resume a webhook without deleting it (and its secret)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(..., description="false stops deliveries; true resumes them.")


class WebhookResponse(BaseModel):
    """Response body for a webhook config."""

    id: UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WebhookListResponse(BaseModel):
    """Response body for listing webhooks."""

    webhooks: list[WebhookResponse]
    count: int


class WebhookTestResponse(BaseModel):
    """Response body for the test webhook endpoint."""

    status: str = "sent"
    message: str = "Test event dispatched successfully"
