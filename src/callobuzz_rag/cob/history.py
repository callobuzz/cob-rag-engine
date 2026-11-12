"""COB's conversations: private to their author, per workspace, kept for a limited time.

A COB chat is a `conversations` row with `kind = 'cob'` on COB's system knowledge base, owned by
a `user_id` and scoped to the workspace it was started in (`scope_tenant_id`, NULL for the
operator view). Switching workspace never shows another workspace's COB chats, and nobody else
ever sees them: every lookup here filters on author and scope, and a miss is always the same
404 `conversation_not_found`, whether the chat exists or not.

Messages are the director's own (masked) rows. What COB adds for the dashboard (the help pages
cited, the link buttons, the thumbs) lives in `messages.agent_log["cob"]`.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob import (
    CobActionCardBlock,  # COB5
    CobConversationItem,
    CobConversationOut,
    CobMessageOut,
    CobSource,
)
from callobuzz_rag.cob.types import CobCaller
from callobuzz_rag.conversation.pii import mask_pii
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message

logger = logging.getLogger(__name__)

KIND: Final = "cob"
# The history list is bounded by the retention window; this is only a safety ceiling.
MAX_LISTED: Final = 500


def not_found() -> APIError:
    return APIError(404, "Conversation not found", "conversation_not_found")


def _mine(caller: CobCaller) -> list[Any]:
    scope = (
        Conversation.scope_tenant_id.is_(None)
        if caller.tenant_id is None
        else Conversation.scope_tenant_id == caller.tenant_id
    )
    return [Conversation.kind == KIND, Conversation.user_id == caller.user_id, scope]


def owns(caller: CobCaller, conv: Conversation | None) -> bool:
    return (
        conv is not None
        and conv.kind == KIND
        and conv.user_id == caller.user_id
        and conv.scope_tenant_id == caller.tenant_id
    )


async def find_or_create(
    session: Any, caller: CobCaller, corpus_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> Conversation:
    """The caller's own conversation, or a new one. Someone else's (or a missing, or an
    assistant-kind) id is a 404, never a new chat."""
    if conversation_id is not None:
        existing: Conversation | None = await session.get(Conversation, conversation_id)
        if existing is None or not owns(caller, existing) or existing.corpus_id != corpus_id:
            raise not_found()
        return existing
    conv = Conversation(
        corpus_id=corpus_id,
        kind=KIND,
        channel="cob",
        user_id=caller.user_id,
        scope_tenant_id=caller.tenant_id,
        created_by=str(caller.user_id) if caller.user_id else None,
    )
    session.add(conv)
    await session.flush()
    return conv


async def list_for(
    session: Any, caller: CobCaller, *, offset: int, limit: int, days: int
) -> tuple[list[CobConversationItem], int]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rows = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    *_mine(caller),
                    Conversation.updated_at >= cutoff,
                    Conversation.message_count > 0,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(MAX_LISTED)
            )
        )
        .scalars()
        .all()
    )
    items = [
        CobConversationItem(
            id=c.id,
            title=c.title,
            created_at=c.created_at,
            updated_at=c.updated_at,
            message_count=c.message_count or 0,
        )
        for c in rows
    ]
    return items[offset : offset + limit], len(items)


def message_out(message: Message) -> CobMessageOut:
    """A stored message as the dashboard shows it, rebuilt from `agent_log["cob"]`."""
    extra = (message.agent_log or {}).get("cob") or {}
    ui = [b for b in extra.get("ui") or [] if isinstance(b, dict)]
    return CobMessageOut(
        id=message.id,
        role="assistant" if message.role == "assistant" else "user",
        content=message.content,
        created_at=message.created_at,
        sources=[CobSource.model_validate(s) for s in extra.get("sources") or []],
        ui=ui,
        rating=extra.get("rating"),
    )


async def get_for(
    session: Any, caller: CobCaller, conversation_id: uuid.UUID
) -> CobConversationOut:
    conv = await session.get(Conversation, conversation_id)
    if not owns(caller, conv):
        raise not_found()
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    messages = [message_out(m) for m in rows if m.role != "system"]
    await _card_states(session, messages)  # COB5
    return CobConversationOut(id=conv.id, title=conv.title, messages=messages)


async def _card_states(session: Any, messages: list[CobMessageOut]) -> None:
    """COB5: an action card read back from history shows how it ended (done, failed, cancelled,
    expired) instead of looking confirmable."""
    cards = [b for m in messages for b in m.ui if isinstance(b, CobActionCardBlock)]
    if not cards:
        return
    from callobuzz_rag.cob import proposals

    states = await proposals.card_statuses(session, [c.proposal_id for c in cards])
    for card in cards:
        card.status = states.get(card.proposal_id, "expired")  # type: ignore[assignment]


async def delete_for(session: Any, caller: CobCaller, conversation_id: uuid.UUID) -> None:
    conv = await session.get(Conversation, conversation_id)
    if not owns(caller, conv):
        raise not_found()
    await session.delete(conv)
    await session.commit()


async def rate(
    session: Any, caller: CobCaller, message_id: uuid.UUID, *, rating: int, comment: str | None
) -> None:
    """Thumbs on one of the caller's own COB answers (404 for anything else)."""
    message = await session.get(Message, message_id)
    if message is None or message.role != "assistant":
        raise not_found()
    conv = await session.get(Conversation, message.conversation_id)
    if not owns(caller, conv):
        raise not_found()
    log = dict(message.agent_log or {})
    extra = dict(log.get("cob") or {})
    extra["rating"] = rating
    extra["comment"] = mask_pii(comment).text if comment else None
    log["cob"] = extra
    message.agent_log = log
    await session.commit()


async def write_agent_log(
    session_factory: Callable[[], Any] | None,
    message_id: uuid.UUID | None,
    data: dict[str, Any],
    *,
    content: str | None = None,
) -> None:
    """Attach COB's own data to an answer the director stored (and, when COB replaced the
    director's text, the text the person saw). Best-effort like the director's own writes."""
    if session_factory is None or message_id is None:
        return
    try:
        async with session_factory() as s:
            message = await s.get(Message, message_id)
            if message is None:
                return
            log = dict(message.agent_log or {})
            log["cob"] = {**(log.get("cob") or {}), **data}
            message.agent_log = log
            if content is not None:
                message.content = content
            await s.commit()
    except Exception:
        logger.warning("Could not store COB's message details", exc_info=True)


async def append_turn(
    session_factory: Callable[[], Any] | None,
    conversation_id: uuid.UUID,
    *,
    question: str,
    answer: str,
    data: dict[str, Any],
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Store a turn COB answered itself (search mode, the administrator-only reply, a tool
    card): the person's question masked, COB's reply, and COB's details. Returns the ids of
    (question, reply); (None, None) when it could not be written."""
    if session_factory is None:
        return None, None
    try:
        async with session_factory() as s:
            conv = await s.get(Conversation, conversation_id)
            if conv is None:
                return None, None
            masked = mask_pii(question).text
            asked = datetime.now(UTC)
            user = Message(
                conversation_id=conv.id,
                role="user",
                content=masked,
                token_count=0,
                created_at=asked,
            )
            reply = Message(
                conversation_id=conv.id,
                role="assistant",
                content=answer,
                token_count=0,
                created_at=asked + timedelta(microseconds=1),
                agent_log={"cob": data},
            )
            s.add(user)
            s.add(reply)
            await s.flush()
            conv.message_count = (conv.message_count or 0) + 2
            if conv.title is None:
                conv.title = masked[:60]
            conv.updated_at = datetime.now(UTC)
            await s.commit()
            return user.id, reply.id
    except Exception:
        logger.warning("Could not store a COB turn", exc_info=True)
        return None, None
