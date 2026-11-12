"""The public, docs-only COB: `POST /api/v1/public/cob/chat`, no credential.

For visitors on the sign-in pages. Answers come from the public pages only; nothing is stored
and no tool runs. The operator's key pays, so it is switched off by `RAG_COB_ENABLED`,
`RAG_COB_PUBLIC_ENABLED` or the "Ask COB" setting, and limited per visitor and per day.
A key or session sent along is ignored, never used to widen what is answered.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import get_engine_settings
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob import CobPublicChatRequest
from callobuzz_rag.cob.orchestrator import CobOrchestrator, CobTurn
from callobuzz_rag.cob.public import get_limiter, mask_history
from callobuzz_rag.cob.types import CobCaller, CobRole
from callobuzz_rag.widget.security import request_client_ip

router = APIRouter(prefix="/api/v1/public/cob", tags=["cob"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_VISITOR = CobCaller(CobRole.PUBLIC, None, None, None, None)


@router.post("/chat", summary="Ask COB without signing in", response_class=StreamingResponse)
async def public_chat(
    body: CobPublicChatRequest, request: Request, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """Ask COB about the engine from a sign-in page. Streams server-sent events like the
    signed-in chat (`meta`, `stage`, `sources`, `token`, `ui`, `done`; or `error`).

    Answers use the public help pages only: no account data, no actions, and nothing is
    stored. Send up to six earlier messages of this page view in `history`; they are masked
    and used for this answer only. Limited per visitor and per day: 429
    `cob_public_rate_limited` with `Retry-After`."""
    settings = request.app.state.settings
    engine = await get_engine_settings(request.app.state.session_factory)
    if not (settings.cob_enabled and settings.cob_public_enabled and engine.cob_public_sign_in):
        raise APIError(404, "Ask COB is switched off on this engine.", "cob_public_disabled")
    result = await get_limiter(request.app).hit(request_client_ip(request), datetime.now(UTC))
    if not result.allowed:
        raise APIError(
            429,
            "You have asked a lot of questions. Please try again later.",
            "cob_public_rate_limited",
            headers={"Retry-After": str(result.retry_after)},
            scope=result.scope,
        )
    orchestrator = CobOrchestrator(request)
    prepared = await orchestrator.prepare(
        session,
        CobTurn(
            caller=_VISITOR,
            message=body.message,
            conversation_id=None,
            context=None,
            language_hint=body.language,
            public=True,
            history=mask_history(body.history),
        ),
    )
    await session.close()  # the stream writes through sessions of its own
    return StreamingResponse(
        orchestrator.stream(prepared), media_type="text/event-stream", headers=_SSE_HEADERS
    )
