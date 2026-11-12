"""AI Chat routes — conversational refinement for Content Portal workspaces."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, get_settings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.content_portal.models.workspace import Workspace
from callobuzz_rag.content_portal.routes._access import (
    authorize_workspace,
    portal_actor,
    scoped_workspace,
)
from callobuzz_rag.content_portal.schemas.ai_chat import (
    ChatHistoryResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ExecuteActionRequest,
    ExecuteActionResponse,
    ToolCallInfo,
)
from callobuzz_rag.content_portal.services.ai_chat_service import (
    ActionExecutionError,
    AIChatError,
    AIChatService,
    ChunkNotInWorkspaceError,
    ConversationNotFoundError,
    ParentChunkActionError,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces",
    tags=["portal-chat"],
    dependencies=[Depends(authorize_workspace)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_llm(settings: "Settings") -> "LLMProvider":  # type: ignore[name-defined]
    """Create an LLM provider instance (deferred import)."""
    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider

    return LiteLLMProvider(settings)


def _get_chat_service(settings: "Settings") -> AIChatService:  # type: ignore[name-defined]
    """Create an AIChatService with a fresh LLM provider."""
    llm = _get_llm(settings)
    return AIChatService(llm=llm)


def _handle_chat_error(exc: Exception) -> HTTPException:
    """Map chat exceptions to HTTP status codes."""
    if isinstance(exc, ParentChunkActionError):
        return APIError(409, str(exc), ParentChunkActionError.code)
    if isinstance(exc, ConversationNotFoundError | ChunkNotInWorkspaceError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ActionExecutionError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, AIChatError):
        return HTTPException(status_code=500, detail=str(exc))
    return HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{workspace_id}/chat",
    response_model=ChatMessageResponse,
)
async def send_chat_message(
    workspace_id: uuid.UUID,
    body: ChatMessageRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
    settings: "Settings" = Depends(get_settings),  # type: ignore[name-defined]
) -> ChatMessageResponse | StreamingResponse:
    """Send a chat message to the AI assistant for a workspace."""
    service = _get_chat_service(settings)

    if body.stream:
        stream = service.send_message_stream(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            message=body.message,
            model=body.model,
        )
        return StreamingResponse(
            stream,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        result = await service.send_message(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            message=body.message,
            model=body.model,
        )
    except AIChatError as e:
        raise _handle_chat_error(e) from e

    await session.commit()

    tool_calls = None
    if result.get("tool_calls"):
        tool_calls = [ToolCallInfo(**tc) for tc in result["tool_calls"]]

    return ChatMessageResponse(
        message_id=result["message_id"],
        conversation_id=result["conversation_id"],
        role=result["role"],
        content=result["content"],
        tool_calls=tool_calls,
        token_usage=result.get("token_usage"),
        created_at=result["created_at"],
    )


@router.get(
    "/{workspace_id}/chat/history",
    response_model=ChatHistoryResponse,
)
async def get_chat_history(
    workspace_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
    settings: "Settings" = Depends(get_settings),  # type: ignore[name-defined]
) -> ChatHistoryResponse:
    """Get the chat history for a workspace."""
    service = _get_chat_service(settings)

    try:
        result = await service.get_history(
            session,
            workspace_id,
            offset=offset,
            limit=limit,
        )
    except AIChatError as e:
        raise _handle_chat_error(e) from e

    return ChatHistoryResponse(**result)


@router.post(
    "/{workspace_id}/chat/execute",
    response_model=ExecuteActionResponse,
)
async def execute_chat_action(
    workspace_id: uuid.UUID,
    body: ExecuteActionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    workspace: Workspace = Depends(scoped_workspace),
    settings: "Settings" = Depends(get_settings),  # type: ignore[name-defined]
) -> ExecuteActionResponse:
    """Execute a tool call action suggested by the AI assistant."""
    service = _get_chat_service(settings)

    try:
        result = await service.execute_action(
            session,
            workspace_id,
            user=portal_actor(request, body.user),
            tool_name=body.action.tool_name,
            arguments=body.action.arguments,
        )
    except AIChatError as e:
        raise _handle_chat_error(e) from e

    await session.commit()
    return ExecuteActionResponse(**result)
