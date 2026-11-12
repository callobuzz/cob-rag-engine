"""WhatsApp API schemas (TV6): the connection, its settings, the chats and the reply box.

Nothing here carries a token: a connection shows only the last characters (`token_hint`), and a
chat's contact is returned only by the single-chat route (a `write` operation for named keys).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest

MetaBilling = Literal["business", "deployer"]
SetupKind = Literal["embedded", "manual"]
ConnectionStatus = Literal["pending", "active", "error", "disabled"]

_TEMPLATE = r"^[a-z0-9_]{1,512}$"
_LANGUAGE = r"^[a-z]{2,3}(_[A-Za-z]{2})?$"


# ----------------------------------------------------------------- settings


class FollowUpSettings(BaseModel):
    """Follow-ups to a person who asked for a call and said yes to being messaged. Off by default;
    Meta must have approved the template (utility category) before it can be sent."""

    enabled: bool = False
    template_name: str | None = Field(None, pattern=_TEMPLATE)
    language: str | None = Field(None, pattern=_LANGUAGE)
    after_hours: int = Field(24, ge=1, le=72)


class WhatsAppSettings(BaseModel):
    """The owner's WhatsApp settings, as stored (minus the media cache, which is internal)."""

    human_pause_hours: int = Field(
        12, ge=1, le=72, description="Hours the assistant stays quiet in a chat after you reply"
    )
    follow_up: FollowUpSettings = Field(default_factory=FollowUpSettings)
    shareable_document_ids: list[UUID] = Field(default_factory=list, max_length=20)


class EmbeddedSignupInfo(BaseModel):
    """What the dashboard needs to open Meta's sign-up window (none of it is secret)."""

    app_id: str
    config_id: str
    graph_version: str


class WhatsAppOut(BaseModel):
    available: bool = Field(
        description="WhatsApp is set up on this engine and the workspace's plan includes it"
    )
    status: ConnectionStatus | None = Field(None, description="null: no number is connected")
    display_phone: str | None = None
    verified_name: str | None = None
    setup: SetupKind | None = None
    coexistence: bool = False
    meta_billing: MetaBilling | None = None
    settings: WhatsAppSettings = Field(default_factory=WhatsAppSettings)
    token_hint: str | None = Field(None, description="The last characters of the access token")
    last_error_code: str | None = None
    last_inbound_at: datetime | None = None
    embedded_signup: EmbeddedSignupInfo | None = Field(
        None, description="Set when the owner can connect a number by Meta's sign-up window"
    )


# ----------------------------------------------------------------- requests


class EmbeddedSignupRequest(StrictRequest):
    code: str = Field(..., min_length=1, max_length=2048, description="The code Meta's window gave")
    phone_number_id: str = Field(..., min_length=1, max_length=40)
    waba_id: str = Field(..., min_length=1, max_length=40)
    coexistence: bool = Field(
        False, description="The number is also used in the WhatsApp Business app"
    )


class ManualConnectRequest(StrictRequest):
    phone_number_id: str = Field(..., min_length=1, max_length=40)
    waba_id: str = Field(..., min_length=1, max_length=40)
    access_token: str = Field(..., min_length=10, max_length=1024)
    meta_billing: MetaBilling = Field(
        ..., description="Who pays Meta for the messages: the business itself, or the deployer"
    )
    pin: str | None = Field(
        None,
        pattern=r"^\d{6}$",
        description=(
            "Six digits. Give it to have the engine register the number with the Cloud API "
            "(a new number, or one that is already registered with this PIN). Leave it out for "
            "a number already registered in Meta's dashboard."
        ),
    )


class FollowUpPatch(StrictRequest):
    enabled: bool | None = None
    template_name: str | None = Field(None, pattern=_TEMPLATE)
    language: str | None = Field(None, pattern=_LANGUAGE)
    after_hours: int | None = Field(None, ge=1, le=72)


class WhatsAppSettingsPatch(StrictRequest):
    """Only the fields sent change."""

    enabled: bool | None = Field(
        None, description="false pauses the number: nothing is answered until it is true again"
    )
    human_pause_hours: int | None = Field(None, ge=1, le=72)
    follow_up: FollowUpPatch | None = None


class DocumentsRequest(StrictRequest):
    document_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ReplyRequest(StrictRequest):
    text: str = Field(..., min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """A message of spaces is refused here (422), not by Meta (a 400 shown as a 502)."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("write a message")
        return stripped


class ThreadPatch(StrictRequest):
    bot_paused: bool = Field(
        ..., description="true keeps the assistant quiet in this chat; false lets it answer again"
    )


# ----------------------------------------------------------------- responses


class CheckOut(BaseModel):
    ok: bool
    display_phone: str | None = None
    quality_rating: str | None = None
    error_code: str | None = None


class ThreadItem(BaseModel):
    id: UUID
    name_masked: str
    last_inbound_at: datetime | None
    unread_count: int
    bot_paused_until: datetime | None
    opted_out: bool
    window_open: bool = Field(description="The customer wrote in the last 24 hours")


class ThreadListResponse(BaseModel):
    items: list[ThreadItem]
    total: int


class ThreadMessage(BaseModel):
    id: UUID
    role: Literal["user", "assistant", "staff"]
    content: str
    created_at: datetime


class ThreadDetail(ThreadItem):
    contact: str = Field(description="The customer's number, in full")
    profile_name: str | None = None
    paused_by: Literal["echo", "dashboard"] | None = None
    messages: list[ThreadMessage]


class ReplyResponse(BaseModel):
    message_id: UUID = Field(description="The stored message")
    whatsapp_message_id: str = Field(description="Meta's id for the message")


class ConnectionRow(BaseModel):
    """One number on the engine, for the deployer."""

    corpus_id: UUID
    corpus_name: str
    tenant_id: UUID
    business: str
    display_phone: str
    status: ConnectionStatus
    setup: SetupKind
    meta_billing: MetaBilling
    last_inbound_at: datetime | None
    last_error_code: str | None


class ConnectionsResponse(BaseModel):
    items: list[ConnectionRow]
    total: int
