"""COB, the built-in assistant: chat, history, thumbs and starter questions.

Session-only: COB is for people. An API key, even the admin key, is refused with 403
`session_required` before anything else runs. The dashboard's panel talks to these routes.
"""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.deps import get_session, require_session_user
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob import (
    CobActionResult,  # COB5
    CobCancelResult,  # COB5
    CobChatRequest,
    CobConfirmRequest,  # COB5
    CobConversationList,
    CobConversationOut,
    CobFeedbackRequest,
    CobStartersOut,
)
from callobuzz_rag.cob import history as cob_history
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.orchestrator import CobOrchestrator, CobTurn, caller_from_request
from callobuzz_rag.cob.starters import starters_for
from callobuzz_rag.cob.types import CobCaller

router = APIRouter(prefix="/api/v1/cob", tags=["cob"], dependencies=[Depends(require_session_user)])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _caller(request: Request) -> CobCaller:
    """The signed-in caller; 404 `cob_disabled` when COB is switched off on this engine."""
    caller = caller_from_request(request)
    if not request.app.state.settings.cob_enabled:
        raise APIError(404, "COB is switched off on this engine.", "cob_disabled")
    return caller


@router.post("/chat", summary="Ask COB", response_class=StreamingResponse)
async def chat(
    body: CobChatRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """Ask COB a question. Streams server-sent events: `meta`, `stage`, `sources`, `token`,
    `quick_replies`, `ui` (a link button or search results), `done`; or `error`.

    The answer is read from COB's own documentation, filtered to what your role may see, in the
    language you wrote in. It counts as one of your workspace's monthly questions; once those
    are used up COB shows matching help pages instead."""
    caller = _caller(request)
    orchestrator = CobOrchestrator(request)
    prepared = await orchestrator.prepare(
        session,
        CobTurn(
            caller=caller,
            message=body.message,
            conversation_id=body.conversation_id,
            context=body.context,
            language_hint=body.language,
        ),
    )
    await session.close()  # the stream writes through sessions of its own
    return StreamingResponse(
        orchestrator.stream(prepared), media_type="text/event-stream", headers=_SSE_HEADERS
    )


@router.get(
    "/conversations", response_model=CobConversationList, summary="List my COB conversations"
)
async def list_conversations(
    request: Request,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    session: AsyncSession = Depends(get_session),
) -> CobConversationList:
    """Your own COB conversations in this workspace, newest first, for the retention period."""
    caller = _caller(request)
    days = request.app.state.settings.cob_history_days
    items, total = await cob_history.list_for(
        session, caller, offset=offset, limit=limit, days=days
    )
    return CobConversationList(items=items, total=total)


@router.get(
    "/conversations/{conversation_id}",
    response_model=CobConversationOut,
    summary="Read a COB conversation",
)
async def get_conversation(
    conversation_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> CobConversationOut:
    """One of your own COB conversations with its messages (404 for anyone else's)."""
    return await cob_history.get_for(session, _caller(request), conversation_id)


@router.delete(
    "/conversations/{conversation_id}", status_code=204, summary="Delete a COB conversation"
)
async def delete_conversation(
    conversation_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Delete one of your own COB conversations for good."""
    await cob_history.delete_for(session, _caller(request), conversation_id)
    return Response(status_code=204)


@router.post("/messages/{message_id}/feedback", status_code=204, summary="Rate a COB answer")
async def feedback(
    message_id: uuid.UUID,
    body: CobFeedbackRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Thumbs up (1) or down (-1) on one of COB's answers to you, with an optional comment."""
    await cob_history.rate(
        session, _caller(request), message_id, rating=body.rating, comment=body.comment
    )
    return Response(status_code=204)


@router.get("/starters", response_model=CobStartersOut, summary="Starter questions for a screen")
async def starters(
    request: Request,
    route: str = Query(..., min_length=1, max_length=200),
    language: Literal["en", "hi", "hi-Latn"] = "en",
) -> CobStartersOut:
    """The questions COB offers on a dashboard screen: those of the screen's help page when
    your role may read it, else a general list."""
    caller = _caller(request)
    slug, questions = starters_for(
        load_docs_index(), role=caller.role, route=route, language=language
    )
    return CobStartersOut(route=route, page_slug=slug, starters=questions)


# COB5 proposals --------------------------------------------------------------------------------


@router.post(
    "/actions/{proposal_id}/confirm",
    response_model=CobActionResult,
    summary="Confirm a COB action card",
)
async def confirm_action(
    proposal_id: uuid.UUID,
    body: CobConfirmRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> CobActionResult:
    """Run the action on a card COB showed you, once.

    The card must be yours, made in this workspace, and at most 10 minutes old. COB runs the
    same engine route the dashboard would, with your own permissions, so anything you may not
    do is refused exactly as it would be there; such a refusal comes back as a `failed` result
    carrying the route's own status and error code, not as an error of this route. A card for
    a destructive action needs `confirm_name`: the exact name, as shown on the card. A rotated
    key is in `secret` of this response only."""
    from callobuzz_rag.cob import proposals

    caller = _caller(request)
    return await proposals.confirm(session, request, caller, proposal_id, body.confirm_name)


@router.post(
    "/actions/{proposal_id}/cancel",
    response_model=CobCancelResult,
    summary="Cancel a COB action card",
)
async def cancel_action(
    proposal_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> CobCancelResult:
    """Dismiss a card without doing anything. A card that was already used or has expired
    answers 409 `cob_proposal_used` / 410 `cob_proposal_expired`."""
    from callobuzz_rag.cob import proposals

    return await proposals.cancel(session, _caller(request), proposal_id)


# end COB5 proposals
