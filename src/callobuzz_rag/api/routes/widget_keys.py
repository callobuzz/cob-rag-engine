"""Manage a corpus's widget keys — the publishable keys behind the website chat.

Callable with the admin key, the owning tenant's key, or the corpus's own key,
so a SaaS built on the engine can let each customer manage their own widget.
"""

import logging  # BIZ1
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.caps import check_widget_key_room
from callobuzz_rag.api.deps import (
    authorized_corpus_ids,
    get_session,
    require_owner,
    resolve_scope,
)
from callobuzz_rag.api.features import (  # BIZ3 features
    hidden_on,
    refuse_hidden,
    refuse_hidden_fields,
)
from callobuzz_rag.api.schemas.widget import (
    CreateWidgetKeyRequest,
    UpdateWidgetKeyRequest,
    WidgetAppearance,
    WidgetKeyResponse,
)
from callobuzz_rag.cob.system import refuse_system_write
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.widget_key import WidgetKey
from callobuzz_rag.widget.security import generate_public_key

logger = logging.getLogger(__name__)  # BIZ1

router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/widget-keys", tags=["widget"])


# BIZ1 learning
async def _learning_follows(request: Request, session: AsyncSession, corpus: Corpus) -> None:
    """The allowed sites changed: start, pause or resume the reads of the business's website.

    Runs after the key's own commit and never fails the request: a problem here is logged and
    the owner can start a read from the Knowledge page.
    """
    try:
        from callobuzz_rag.website.service import on_origins_changed

        await on_origins_changed(
            session, corpus, request=request, settings=request.app.state.settings
        )
    except Exception:
        logger.warning("Website learning could not follow a widget-key change", exc_info=True)


# /BIZ1 learning


async def _authorized_corpus(
    request: Request, corpus_id: uuid.UUID, session: AsyncSession
) -> Corpus:
    await authorized_corpus_ids(resolve_scope(request), corpus_id, session)
    corpus = await session.get(Corpus, corpus_id)
    if corpus is None:
        raise HTTPException(status_code=404, detail="Corpus not found")
    refuse_system_write(request, corpus)
    return corpus


async def _get_key(session: AsyncSession, corpus_id: uuid.UUID, key_id: uuid.UUID) -> WidgetKey:
    key = await session.get(WidgetKey, key_id)
    if key is None or key.corpus_id != corpus_id:
        raise HTTPException(status_code=404, detail="Widget key not found")
    return key


# BIZ3 features
async def _key_count(session: AsyncSession, corpus_id: uuid.UUID) -> int:
    result = await session.execute(
        select(func.count(WidgetKey.id)).where(WidgetKey.corpus_id == corpus_id)
    )
    return int(result.scalar() or 0)


# /BIZ3 features


def engine_public_url(request: Request) -> str:
    """Where browsers reach this engine: RAG_PUBLIC_URL, else the request's own base."""
    configured: str = request.app.state.settings.public_url
    return (configured or str(request.base_url)).rstrip("/")


def _to_response(request: Request, key: WidgetKey) -> WidgetKeyResponse:
    snippet = (
        f'<script src="{engine_public_url(request)}/widget.js" '
        f'data-key="{key.public_key}" async></script>'
    )
    return WidgetKeyResponse(
        id=key.id,
        corpus_id=key.corpus_id,
        name=key.name,
        public_key=key.public_key,
        allowed_origins=list(key.allowed_origins),
        rate_limit_per_minute=key.rate_limit_per_minute,
        daily_message_limit=key.daily_message_limit,
        appearance=WidgetAppearance(**(key.appearance or {})),
        is_active=key.is_active,
        created_at=key.created_at,
        embed_snippet=snippet,
    )


@router.post("", response_model=WidgetKeyResponse, status_code=201)
async def create_widget_key(
    corpus_id: uuid.UUID,
    body: CreateWidgetKeyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> WidgetKeyResponse:
    """Create a publishable key and get the embed snippet for a website.

    The tenant's plan may limit its widget keys (409 `widget_key_limit_reached`);
    rotating a key never counts as a new one.
    """
    corpus = await _authorized_corpus(request, corpus_id, session)
    # BIZ3 features: a code-only plan gets the one key onboarding made, no more
    if "widget_settings" in hidden_on(request) and await _key_count(session, corpus_id) >= 1:
        await refuse_hidden(request, "widget_settings")
    # /BIZ3 features
    await check_widget_key_room(session, corpus.tenant_id)
    key = WidgetKey(
        corpus_id=corpus_id,
        name=body.name,
        public_key=generate_public_key(),
        allowed_origins=body.allowed_origins,
        rate_limit_per_minute=body.rate_limit_per_minute,
        daily_message_limit=body.daily_message_limit,
        appearance=body.appearance.model_dump(exclude_none=True),
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    response = _to_response(request, key)
    await _learning_follows(request, session, corpus)  # BIZ1 learning
    return response


@router.get("", response_model=list[WidgetKeyResponse])
async def list_widget_keys(
    corpus_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[WidgetKeyResponse]:
    await _authorized_corpus(request, corpus_id, session)
    rows = await session.execute(
        select(WidgetKey).where(WidgetKey.corpus_id == corpus_id).order_by(WidgetKey.created_at)
    )
    return [_to_response(request, key) for key in rows.scalars().all()]


@router.patch("/{key_id}", response_model=WidgetKeyResponse)
async def update_widget_key(
    corpus_id: uuid.UUID,
    key_id: uuid.UUID,
    body: UpdateWidgetKeyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> WidgetKeyResponse:
    """Change sites, limits, branding, or switch the key off (is_active=false)."""
    # BIZ3 features: a code-only plan may change sites, look and on/off only
    await refuse_hidden_fields(request, body)
    # /BIZ3 features
    corpus = await _authorized_corpus(request, corpus_id, session)
    key = await _get_key(session, corpus_id, key_id)
    changes = body.model_dump(exclude_unset=True)
    if "appearance" in changes and body.appearance is not None:
        # Merge, so changing the color does not wipe the greeting.
        merged = dict(key.appearance or {})
        merged.update(body.appearance.model_dump(exclude_unset=True))
        changes["appearance"] = {k: v for k, v in merged.items() if v is not None}
    for field, value in changes.items():
        setattr(key, field, value)
    key.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(key)
    response = _to_response(request, key)
    if "allowed_origins" in changes or "is_active" in changes:  # BIZ1 learning
        await _learning_follows(request, session, corpus)
    return response


@router.post("/{key_id}/rotate", response_model=WidgetKeyResponse)
async def rotate_widget_key(
    corpus_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> WidgetKeyResponse:
    """Issue a new public key. The old one stops working immediately."""
    await _authorized_corpus(request, corpus_id, session)
    key = await _get_key(session, corpus_id, key_id)
    key.public_key = generate_public_key()
    key.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(key)
    return _to_response(request, key)


@router.delete("/{key_id}", status_code=204)
async def delete_widget_key(
    corpus_id: uuid.UUID,
    key_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _owner: None = Depends(require_owner),
) -> None:
    corpus = await _authorized_corpus(request, corpus_id, session)
    key = await _get_key(session, corpus_id, key_id)
    await session.delete(key)
    await session.commit()
    await _learning_follows(request, session, corpus)  # BIZ1 learning
