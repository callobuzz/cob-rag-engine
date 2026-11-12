"""Conversation and message API schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.api.schemas.assistant import (
    AssistantReplyFields,
    AssistantRequestOptions,
)
from callobuzz_rag.core.schemas import StrictRequest


class ConversationCreateRequest(StrictRequest):
    """Request body for creating a conversation."""

    title: str | None = None
    created_by: str | None = None
    metadata: dict[str, Any] | None = None


class ConversationUpdateRequest(StrictRequest):
    """Request body for updating a conversation."""

    title: str | None = None
    metadata: dict[str, Any] | None = None


class ConversationResponse(BaseModel):
    """Full conversation representation."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    corpus_id: UUID
    title: str | None = None
    created_by: str | None = None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_")
    message_count: int = 0
    total_tokens: int = 0
    summary: str | None = None
    channel: str = Field(
        "api",
        description="Where the chat happens: api, stream, widget, conversation or playground.",
    )
    created_at: datetime
    updated_at: datetime

    @field_validator("channel", mode="before")
    @classmethod
    def _default_channel(cls, value: object) -> object:
        """A row not yet reloaded after insert has no channel: it is the default, `api`."""
        return "api" if value is None else value


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    items: list[ConversationResponse]
    count: int
    offset: int
    limit: int


class MessageResponse(BaseModel):
    """Full message representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    role: str
    content: str
    token_count: int = 0
    retrieval_context: dict[str, Any] | None = None
    agent_log: dict[str, Any] | None = None
    created_at: datetime


class MessageListResponse(BaseModel):
    """Paginated list of messages."""

    items: list[MessageResponse]
    count: int
    offset: int
    limit: int


class MessageSendRequest(StrictRequest):
    """Request body for sending a message in a conversation."""

    content: str = Field(..., min_length=1, max_length=10000)
    metadata: dict[str, Any] | None = None
    assistant: AssistantRequestOptions | None = None


class MessageSendResponse(AssistantReplyFields):
    """Response from sending a message — includes both user and assistant messages."""

    user_message: MessageResponse
    assistant_message: MessageResponse
    retrieval_context: dict[str, Any] | None = None
