"""The assistant's picture: an owner uploads it, anyone's browser can load it (BIZ4).

`POST /corpora/{id}/assistant/avatar` takes a PNG, JPEG or WebP and stores a re-encoded 256x256
PNG (`assets/avatars.py`); `GET /public/avatars/<uuid>.png` serves that PNG and nothing else.
"""

import logging

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.deps import get_session, path_corpus, require_owner
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.onboarding import AvatarOut
from callobuzz_rag.assets.avatars import (
    SERVE_HEADERS,
    avatar_path,
    load_avatar,
    prune_avatars,
    read_limited,
    save_avatar,
)
from callobuzz_rag.cob.system import refuse_system_write
from callobuzz_rag.db.models.corpus import Corpus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assistant"])


def _author(request: Request) -> str:
    user = getattr(request.state, "user", None)
    email = getattr(user, "email", None)
    return str(email)[:320] if email else "api"


async def _within_rate(request: Request) -> bool:
    """120 pictures a minute per client address; a cached picture never reaches the engine again.
    Redis trouble lets the request through."""
    try:
        from callobuzz_rag.api.routes.widget import _limiter
        from callobuzz_rag.widget.security import request_client_ip

        ok, _ = await _limiter(request)._hit(f"avatar:ip:{request_client_ip(request)}", 120, 60)
        return bool(ok)
    except Exception:
        logger.debug("avatar rate limit unavailable", exc_info=True)
        return True


@router.post(
    "/api/v1/corpora/{corpus_id}/assistant/avatar",
    response_model=AvatarOut,
    status_code=201,
    dependencies=[Depends(require_owner)],
)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(..., description="PNG, JPEG or WebP, at least 64 px square."),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> AvatarOut:
    """Upload the assistant's picture. Returns the path to put in `identity.avatar_url`."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    data = await read_limited(file, settings.avatar_max_bytes)
    if len(data) > settings.avatar_max_bytes:
        raise APIError(
            413,
            f"That picture is too large. Use one under {settings.avatar_max_bytes // 1024} KB.",
            "avatar_too_large",
        )
    asset = await save_avatar(session, settings, corpus, data, created_by=_author(request))
    await record_audit(
        session,
        request,
        "assistant.avatar_uploaded",
        tenant_id=corpus.tenant_id,
        target_type="corpus",
        target_id=str(corpus.id),
        details={"corpus_id": str(corpus.id), "asset_id": str(asset.id), "bytes": asset.bytes},
    )
    await session.commit()
    await prune_avatars(session, settings, corpus)  # after the commit: the new one is kept
    return AvatarOut(avatar_url=avatar_path(asset.id))


@router.get("/api/v1/public/avatars/{name}", include_in_schema=True)
async def public_avatar(
    name: str, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """An assistant's picture, by id. No credential: the widget loads it on any website."""
    if not await _within_rate(request):
        raise APIError(429, "Too many requests. Please wait a moment.", "rate_limited")
    data = await load_avatar(session, request.app.state.settings, name)
    if data is None:
        raise APIError(404, "That picture was not found.", "avatar_not_found")
    return Response(content=data, media_type="image/png", headers=dict(SERVE_HEADERS))
