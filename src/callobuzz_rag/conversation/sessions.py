# src/callobuzz_rag/conversation/sessions.py
"""Widget chat sessions: an opaque `ws_` token the browser keeps in sessionStorage.

Only its sha256 is stored (`conversations.widget_session_hash`, unique). A token that is
unknown, expired (no message for `RAG_ASSISTANT_SESSION_TTL_HOURS`) or belongs to another
widget key silently starts a new conversation; a visitor never sees an error for it.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from callobuzz_rag.db.models.conversation import Conversation

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SESSION_PREFIX = "ws_"


def hash_session(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> tuple[str, str]:
    token = SESSION_PREFIX + secrets.token_urlsafe(32)
    return token, hash_session(token)


async def resolve_widget_session(
    session: "AsyncSession",
    *,
    widget_key_id: uuid.UUID,
    token: str | None,
    ttl_hours: int,
    now: datetime,
) -> Conversation | None:
    """The live conversation behind `token` for this widget key, or None. Never creates one
    (lead, handoff and event calls need an existing chat; README P11)."""
    if not token:
        return None
    found = (
        await session.execute(
            select(Conversation).where(Conversation.widget_session_hash == hash_session(token))
        )
    ).scalar_one_or_none()
    if (
        found is None
        or found.widget_key_id != widget_key_id
        or found.updated_at is None
        or now - found.updated_at > timedelta(hours=ttl_hours)
    ):
        return None
    return found


async def widget_conversation(
    session: "AsyncSession",
    *,
    corpus_id: uuid.UUID,
    widget_key_id: uuid.UUID,
    token: str | None,
    ttl_hours: int,
    page_url: str | None,
    utm: dict[str, str] | None,
    now: datetime,
) -> tuple[Conversation, str | None]:
    found = await resolve_widget_session(
        session, widget_key_id=widget_key_id, token=token, ttl_hours=ttl_hours, now=now
    )
    if found is not None and found.corpus_id == corpus_id:
        if page_url:
            found.page_url = page_url[:2048]
        return found, None
    new_token, digest = new_session_token()
    conversation = Conversation(
        corpus_id=corpus_id,
        channel="widget",
        widget_key_id=widget_key_id,
        widget_session_hash=digest,
        page_url=page_url[:2048] if page_url else None,
        utm=utm,
        message_count=0,
        total_tokens=0,
    )
    session.add(conversation)
    return conversation, new_token
