"""Storing what the webhook said (TV6): chats, and one event row per message.

The webhook answers 200 at once, so everything here is a few small statements: find the number
(`phone_number_id` is unique across the engine), find or create the chat of that customer, and
insert the event `ON CONFLICT DO NOTHING` on (connection, external id). Meta retries a message
for days; the unique key is what makes a retry a no-op, and only a newly stored event is worth
waking a worker for.

Phone numbers are stored encrypted with a keyed hash for lookup (`LeadCrypto.index_hash` of the
E.164 number, so a number hashes the same in a lead and in a chat). A payload is stored encrypted
too, and purged after seven days.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from callobuzz_rag.channels.whatsapp.webhook import ParsedEvent
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelEvent, ChannelThread
from callobuzz_rag.db.models.tenant import Tenant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.conversation.leads.crypto import LeadCrypto

# A connection answers the webhook in these states. `pending` has no working token yet and
# `disabled` was switched off (by its owner, or because its plan lost WhatsApp).
LIVE_STATUSES = frozenset({"active", "error"})


def e164(digits: str) -> str:
    """The customer's number the way a lead stores it: `+` and the digits."""
    return "+" + digits.lstrip("+")


def wa_number(contact_e164: str) -> str:
    """The number as the Cloud API takes it (`to`): digits only."""
    return contact_e164.lstrip("+")


async def connection_for_number(
    session: "AsyncSession", phone_number_id: str
) -> ChannelConnection | None:
    return (
        await session.execute(
            select(ChannelConnection).where(
                ChannelConnection.phone_number_id == phone_number_id,
                ChannelConnection.kind == "whatsapp",
            )
        )
    ).scalar_one_or_none()


async def get_or_create_thread(
    session: "AsyncSession",
    crypto: "LeadCrypto",
    connection: ChannelConnection,
    contact_digits: str,
    *,
    profile_name: str | None,
    now: datetime,
) -> ChannelThread:
    """The chat of this customer on this number, created on the first message."""
    number = e164(contact_digits)
    digest = crypto.index_hash(number)
    assert digest is not None
    # A chat written under an earlier key (mid-rotation) is the same chat: look under every key.
    found = (
        (
            await session.execute(
                select(ChannelThread)
                .where(
                    ChannelThread.connection_id == connection.id,
                    ChannelThread.contact_hash.in_(crypto.index_hashes(number)),
                )
                .order_by(ChannelThread.created_at)
            )
        )
        .scalars()
        .all()
    )
    thread = found[0] if found else None
    where = (
        ChannelThread.connection_id == connection.id,
        ChannelThread.contact_hash == digest,
    )
    if thread is None:
        await session.execute(
            pg_insert(ChannelThread)
            .values(
                connection_id=connection.id,
                corpus_id=connection.corpus_id,
                tenant_id=connection.tenant_id,
                contact_hash=digest,
                contact_encrypted=crypto.encryptor.encrypt(number),
                profile_name_encrypted=(
                    crypto.encryptor.encrypt(profile_name) if profile_name else None
                ),
                unread_count=0,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["connection_id", "contact_hash"])
        )
        thread = (await session.execute(select(ChannelThread).where(*where))).scalar_one()
    elif profile_name and not thread.profile_name_encrypted:
        thread.profile_name_encrypted = crypto.encryptor.encrypt(profile_name)
    return thread


async def store_events(
    session: "AsyncSession",
    crypto: "LeadCrypto",
    events: list[ParsedEvent],
    *,
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Store `events`; the ids of the chats that have something new to answer, once each, in the
    order their first new event arrived. The caller commits, then wakes a worker per chat."""
    moment = now or datetime.now(UTC)
    connections: dict[str, ChannelConnection | None] = {}
    tenant_active: dict[uuid.UUID, bool] = {}
    woken: list[uuid.UUID] = []
    for index, event in enumerate(events):
        received = moment + timedelta(microseconds=index)  # arrival order within one delivery
        if event.phone_number_id not in connections:
            connections[event.phone_number_id] = await connection_for_number(
                session, event.phone_number_id
            )
        connection = connections[event.phone_number_id]
        if connection is None or connection.status not in LIVE_STATUSES:
            continue  # a number we do not serve: Meta is told 200 and nothing is kept
        if connection.tenant_id not in tenant_active:
            tenant = await session.get(Tenant, connection.tenant_id)
            tenant_active[connection.tenant_id] = tenant is not None and tenant.status == "active"
        if not tenant_active[connection.tenant_id]:
            continue  # a pending or suspended workspace answers nobody and keeps nothing
        thread: ChannelThread | None = None
        if event.kind in ("message", "echo") and event.contact:
            thread = await get_or_create_thread(
                session,
                crypto,
                connection,
                event.contact,
                profile_name=event.profile_name,
                now=moment,
            )
        values: dict[str, Any] = {
            "connection_id": connection.id,
            "corpus_id": connection.corpus_id,
            "external_id": event.external_id,
            "kind": event.kind,
            "thread_id": thread.id if thread is not None else None,
            "payload_encrypted": crypto.encryptor.encrypt(
                json.dumps(event.to_payload(), ensure_ascii=False)
            ),
            "status": "received" if event.kind in ("message", "echo") else "done",
            "received_at": received,
            "processed_at": None if event.kind in ("message", "echo") else received,
        }
        result = await session.execute(
            pg_insert(ChannelEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["connection_id", "external_id"])
        )
        if not getattr(result, "rowcount", 0):
            continue  # a retry of something already stored
        if event.kind == "message":
            seen = event.timestamp or moment
            if thread is not None and (
                thread.last_inbound_at is None or seen > thread.last_inbound_at
            ):
                thread.last_inbound_at = seen
            if connection.last_inbound_at is None or seen > connection.last_inbound_at:
                connection.last_inbound_at = seen
        if thread is not None and thread.id not in woken:
            woken.append(thread.id)
    return woken
