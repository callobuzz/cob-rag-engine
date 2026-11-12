"""Request/response schemas for AI Chat (interactive refinement) endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from callobuzz_rag.core.schemas import StrictRequest

VALID_TOOL_NAMES = {"split_chunk", "merge_chunks", "rewrite_chunk", "search_corpus"}


class ChatMessageRequest(StrictRequest):
    """User chat message to the AI assistant."""

    message: str = Field(..., min_length=1, max_length=10000)
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )
    stream: bool = False
    model: str | None = None


class ToolCallInfo(StrictRequest):
    """Structured tool call extracted from AI response."""

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict[str, Any])

    @field_validator("tool_name")
    @classmethod
    def validate_tool_name(cls, v: str) -> str:
        if v not in VALID_TOOL_NAMES:
            raise ValueError(f"Invalid tool name: {v}. Must be one of: {sorted(VALID_TOOL_NAMES)}")
        return v


class ChatMessageResponse(BaseModel):
    """AI assistant response to a chat message."""

    message_id: UUID
    conversation_id: UUID
    role: str = "assistant"
    content: str
    tool_calls: list[ToolCallInfo] | None = None
    token_usage: dict[str, Any] | None = None
    created_at: datetime


class ChatHistoryMessage(BaseModel):
    """A single message in the chat history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    tool_calls: list[ToolCallInfo] | None = None
    token_usage: dict[str, Any] | None = None
    created_at: datetime


class ChatHistoryResponse(BaseModel):
    """Paginated chat history for a workspace."""

    workspace_id: UUID
    conversation_id: UUID | None = None
    messages: list[ChatHistoryMessage]
    total: int


class ExecuteActionRequest(StrictRequest):
    """Request to execute a tool call action."""

    action: ToolCallInfo
    user: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description=(
            "Who is acting, for the audit trail and locks (API keys only: a signed-in user is "
            "always recorded by their account). Defaults to X-User-Id, else 'api_key_user'."
        ),
    )


class ExecuteActionResponse(BaseModel):
    """Result of executing a tool call action."""

    action: str
    success: bool
    message: str
    result: dict[str, Any] | None = None
