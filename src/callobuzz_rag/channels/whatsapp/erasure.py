"""Erasing a customer's WhatsApp chat with their lead (TV6, DPDP).

A lead's erasure (an owner's delete, the customer's own withdrawal, the retention purge) must
leave nothing that names the person. On WhatsApp that is more than the lead's own conversation:
the chat (`channel_threads`) keeps the number and the profile name (encrypted), the stored
webhook payloads keep what was typed, and every 24-hour window of the chat is its own
conversation. So the chat is found two ways, and all of it goes, in the caller's transaction:

- by the lead's conversation (`conversations.channel_thread_id`, or `channel_threads.
  conversation_id` for a chat that predates the link), and
- by the lead's phone: `channel_threads.contact_hash` is the same keyed hash as
  `leads.phone_hash`, so a lead made on the website erases the same person's WhatsApp chat on
  that knowledge base too.

Then the chat's conversations (messages cascade) and their assistant turns, the chat's events,
and the chat. A handoff keeps its row but loses its summary, as for any erased lead.
"""

import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy import delete, or_, select, update

from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.channel import ChannelEvent, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.handoff import Handoff

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# (corpus id, the lead's conversation id, the lead's phone hash): what a lead says about its chat
Target = tuple[uuid.UUID, uuid.UUID | None, str | None]
_CHUNK = 200


async def erase_chats(session: "AsyncSession", targets: Sequence[Target]) -> int:
    """Delete the WhatsApp chats of the erased leads `targets`, and everything in them. Does not
    commit. Returns how many chats were deleted."""
    wanted = [t for t in targets if t[1] is not None or t[2]]
    deleted = 0
    for start in range(0, len(wanted), _CHUNK):
        deleted += await _erase_chunk(session, wanted[start : start + _CHUNK])
    return deleted


async def _erase_chunk(session: "AsyncSession", targets: Sequence[Target]) -> int:
    conversation_ids = [c for _, c, _ in targets if c is not None]
    pairs = {(corpus, phone) for corpus, _, phone in targets if phone}
    thread_ids: set[uuid.UUID] = set()
    if conversation_ids:
        linked = await session.execute(
            select(Conversation.channel_thread_id).where(
                Conversation.id.in_(conversation_ids),
                Conversation.channel_thread_id.is_not(None),
            )
        )
        thread_ids.update(t for t in linked.scalars().all() if t is not None)
    conditions = []
    if conversation_ids:
        conditions.append(ChannelThread.conversation_id.in_(conversation_ids))
    if pairs:
        conditions.append(ChannelThread.contact_hash.in_({phone for _, phone in pairs}))
    if conditions:
        rows = await session.execute(
            select(
                ChannelThread.id,
                ChannelThread.corpus_id,
                ChannelThread.contact_hash,
                ChannelThread.conversation_id,
            ).where(or_(*conditions))
        )
        for thread_id, corpus_id, contact_hash, conversation_id in rows.all():
            by_conversation = conversation_id is not None and conversation_id in conversation_ids
            if by_conversation or (corpus_id, contact_hash) in pairs:
                thread_ids.add(thread_id)
    if not thread_ids:
        return 0
    chats = list(thread_ids)
    # every conversation of these chats: the linked ones, and the one the chat points at now
    owned = await session.execute(
        select(Conversation.id).where(Conversation.channel_thread_id.in_(chats))
    )
    current = await session.execute(
        select(ChannelThread.conversation_id).where(ChannelThread.id.in_(chats))
    )
    doomed = {c for c in owned.scalars().all() if c is not None}
    doomed.update(c for c in current.scalars().all() if c is not None)
    doomed.update(conversation_ids)
    if doomed:
        found = await session.execute(
            select(Conversation.id).where(
                Conversation.id.in_(list(doomed)), Conversation.channel == "whatsapp"
            )
        )
        ids = [c for c in found.scalars().all() if c is not None]
        if ids:
            await session.execute(
                update(Handoff)
                .where(Handoff.conversation_id.in_(ids))
                .values(summary_encrypted=None)
            )
            # the questions leave the frozen value reports with the turns (TV4)
            from callobuzz_rag.business.value_privacy import delete_assistant_turns

            await delete_assistant_turns(session, AssistantTurn.conversation_id.in_(ids))
            await session.execute(delete(Conversation).where(Conversation.id.in_(ids)))
    await session.execute(delete(ChannelEvent).where(ChannelEvent.thread_id.in_(chats)))
    await session.execute(delete(ChannelThread).where(ChannelThread.id.in_(chats)))
    return len(chats)
