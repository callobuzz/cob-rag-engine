"""Opt-in follow-ups on WhatsApp (TV6): off by default, one message, only to a person who said yes.

A business-initiated message (one sent outside the customer's 24-hour window, which Meta only
allows as an approved template) goes only to a customer who

1. was offered a person (a handoff) or left their details (a lead),
2. was then asked "May we message you here?" and tapped "Yes, message me" (`opted_in_at`),
3. has not said STOP (`opted_out_at`), and
4. got no reply from the owner since (no `staff` message after the question), after `after_hours`.

It is sent once (`follow_up_sent_at`), as the owner's approved *utility* template (a template
without variables), and is metered at the utility rate like any message. The owner turns it on in
the number's settings; the deployer's rates decide what it costs.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update

from callobuzz_rag.channels.whatsapp import outbound
from callobuzz_rag.channels.whatsapp.availability import plan_allows
from callobuzz_rag.channels.whatsapp.inbound import STAFF_ROLE, InboundDeps
from callobuzz_rag.channels.whatsapp.store import wa_number
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelThread
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

BATCH = 200
MAX_PAGES = 50  # 10,000 due chats per run; the next run takes the rest


def follow_up_settings(connection: Any) -> dict[str, Any] | None:
    """The owner's follow-up settings when they are complete and on, else None."""
    raw = (connection.settings or {}).get("follow_up")
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None
    if not raw.get("template_name") or not raw.get("language"):
        return None
    after = raw.get("after_hours")
    return {
        "template_name": str(raw["template_name"]),
        "language": str(raw["language"]),
        "after_hours": after if isinstance(after, int) and 1 <= after <= 72 else 24,
    }


async def _owner_replied(deps: InboundDeps, thread: ChannelThread) -> bool:
    if thread.conversation_id is None or thread.opt_in_asked_at is None:
        return False
    async with deps.session_factory() as session:
        found = (
            await session.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == thread.conversation_id,
                    Message.role == STAFF_ROLE,
                    Message.created_at > thread.opt_in_asked_at,
                )
                .limit(1)
            )
        ).first()
    return found is not None


async def _eligible_connections(
    deps: InboundDeps,
) -> dict[int, list[ChannelConnection]]:
    """The numbers that may send a follow-up right now, by the owner's `after_hours`: live,
    follow-ups on, the workspace active and its plan on WhatsApp. Chats of every other number
    never reach the queue below, so they cannot crowd out a chat that is due."""
    out: dict[int, list[ChannelConnection]] = {}
    verdict: dict[uuid.UUID, bool] = {}
    async with deps.session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(ChannelConnection).where(
                        ChannelConnection.kind == "whatsapp",
                        ChannelConnection.status.in_(("active", "error")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for connection in rows:
            settings = follow_up_settings(connection)
            if settings is None:
                continue
            tenant_id = connection.tenant_id
            if tenant_id not in verdict:
                tenant = await session.get(Tenant, tenant_id)
                verdict[tenant_id] = (
                    tenant is not None
                    and tenant.status == "active"
                    and await plan_allows(session, tenant_id)
                )
            if verdict[tenant_id]:
                out.setdefault(settings["after_hours"], []).append(connection)
    return out


async def run_follow_ups(deps: InboundDeps, now: datetime) -> dict[str, int]:
    """Send each due follow-up once. Returns how many were sent, skipped and failed."""
    counts = {"sent": 0, "skipped": 0, "failed": 0}
    seen: set[uuid.UUID] = set()
    for after_hours, connections in (await _eligible_connections(deps)).items():
        by_id = {c.id: c for c in connections}
        due = now - timedelta(hours=after_hours)
        for _page in range(MAX_PAGES):
            async with deps.session_factory() as session:
                threads = list(
                    (
                        await session.execute(
                            select(ChannelThread)
                            .where(
                                ChannelThread.connection_id.in_(list(by_id)),
                                ChannelThread.opted_in_at.is_not(None),
                                ChannelThread.opted_out_at.is_(None),
                                ChannelThread.follow_up_sent_at.is_(None),
                                ChannelThread.follow_up_skipped_at.is_(None),
                                ChannelThread.opt_in_asked_at.is_not(None),
                                ChannelThread.opt_in_asked_at <= due,
                            )
                            .order_by(ChannelThread.opt_in_asked_at)
                            .limit(BATCH)
                        )
                    )
                    .scalars()
                    .all()
                )
            fresh = [t for t in threads if t.id not in seen]
            if not fresh:
                break
            for thread in fresh:
                seen.add(thread.id)
                if await _owner_replied(deps, thread):
                    # For good: the owner has taken this customer. Marked, so the chat leaves
                    # the queue instead of being looked at (and crowding the page) every run.
                    await _mark_skipped(deps, thread, now)
                    counts["skipped"] += 1
                    continue
                outcome = await _send(deps, by_id[thread.connection_id], thread, now)
                counts[outcome] += 1
    return counts


async def _mark_skipped(deps: InboundDeps, thread: ChannelThread, now: datetime) -> None:
    async with deps.session_factory() as session:
        await session.execute(
            update(ChannelThread)
            .where(ChannelThread.id == thread.id)
            .values(follow_up_skipped_at=now)
        )
        await session.commit()


async def _send(
    deps: InboundDeps,
    connection: ChannelConnection,
    thread: ChannelThread,
    now: datetime,
) -> str:
    settings = follow_up_settings(connection)
    assert settings is not None
    encryptor = deps.crypto.encryptor
    ctx = outbound.SendContext(
        graph=deps.graph,
        session_factory=deps.session_factory,
        connection_id=connection.id,
        corpus_id=connection.corpus_id,
        tenant_id=connection.tenant_id,
        phone_number_id=connection.phone_number_id,
        token=encryptor.decrypt(connection.access_token_encrypted),
        thread_id=thread.id,
        to=wa_number(encryptor.decrypt(thread.contact_encrypted)),
        meta_billing=connection.meta_billing,
        after_send=deps.after_send,
        clock=lambda: now,
    )
    outcome = "sent"
    try:
        await outbound.send_template(
            ctx,
            name=settings["template_name"],
            language=settings["language"],
            params=[],
            category="utility",
        )
    except outbound.SendError as exc:
        # Never tried twice: a template Meta refused will not be accepted on the next run, and
        # one that may have gone must not be repeated.
        logger.warning(
            "WhatsApp follow-up failed (%s) for corpus %s", exc.code, connection.corpus_id
        )
        outcome = "failed"
    async with deps.session_factory() as session:
        await session.execute(
            update(ChannelThread).where(ChannelThread.id == thread.id).values(follow_up_sent_at=now)
        )
        await session.commit()
    return outcome
