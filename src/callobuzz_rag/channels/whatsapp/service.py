"""What the owner does with WhatsApp (TV6): look at the connection, read and answer chats.

Route-level logic, so the routes stay thin and the rules are tested without HTTP. Nothing here
logs or returns a token; a number is shown masked in lists and in full only on one chat's page.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels.whatsapp import outbound
from callobuzz_rag.channels.whatsapp.availability import engine_ready, plan_allows
from callobuzz_rag.channels.whatsapp.graph import GraphClient, GraphError, graph_client
from callobuzz_rag.channels.whatsapp.inbound import (
    SERVICE_WINDOW,
    STAFF_ROLE,
    pause_hours,
)
from callobuzz_rag.channels.whatsapp.signup import is_disconnected
from callobuzz_rag.conversation.leads.crypto import (
    LeadCrypto,
    LeadCryptoUnavailableError,
    lead_crypto_from_settings,
)
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

THREAD_PAGE = 50
MESSAGES_SHOWN = 100


# ------------------------------------------------------------------ gates the routes share


def require_engine(settings: Any) -> None:
    """409 `whatsapp_not_enabled` when the engine has no Meta app configured."""
    if not engine_ready(settings):
        raise APIError(409, "WhatsApp is not set up on this engine.", "whatsapp_not_enabled")


async def require_plan(session: "AsyncSession", tenant_id: uuid.UUID) -> None:
    """403 `whatsapp_not_in_plan` when the business's plan does not include WhatsApp."""
    if not await plan_allows(session, tenant_id):
        raise APIError(403, "Your plan does not include WhatsApp.", "whatsapp_not_in_plan")


# OS2 public address
def require_public_address(request: Any) -> None:
    """403 `whatsapp_needs_public_address` at a development address. The operator is told what
    to set; anyone else gets a plain sentence."""
    from callobuzz_rag.channels.whatsapp.availability import public_address_ok

    if public_address_ok(request.app.state.settings):
        return
    if getattr(request.state, "is_admin", False):
        raise APIError(
            403,
            "WhatsApp needs a public https address. Set RAG_PUBLIC_URL to the address of this "
            "server and restart.",
            "whatsapp_needs_public_address",
        )
    raise APIError(
        403,
        "WhatsApp is not available right now. Please contact the platform administrator.",
        "whatsapp_needs_public_address",
    )


# /OS2 public address


def crypto_for(settings: Any) -> LeadCrypto:
    try:
        return lead_crypto_from_settings(settings)
    except LeadCryptoUnavailableError as exc:
        raise APIError(503, "WhatsApp needs RAG_ENCRYPTION_KEY.", "encryption_unavailable") from exc


def make_graph(settings: Any) -> GraphClient:
    """The Graph client for the engine's settings (tests replace this to inject a transport)."""
    return graph_client(settings)


async def connection_of(session: "AsyncSession", corpus_id: uuid.UUID) -> ChannelConnection | None:
    return (
        await session.execute(
            select(ChannelConnection).where(
                ChannelConnection.corpus_id == corpus_id, ChannelConnection.kind == "whatsapp"
            )
        )
    ).scalar_one_or_none()


async def require_connection(session: "AsyncSession", corpus_id: uuid.UUID) -> ChannelConnection:
    connection = await connection_of(session, corpus_id)
    if connection is None or is_disconnected(connection):
        raise APIError(
            404, "No WhatsApp number is connected to this knowledge base.", "whatsapp_not_connected"
        )
    return connection


# ------------------------------------------------------------------ chats


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def window_open(thread: ChannelThread, now: datetime) -> bool:
    last = _utc(thread.last_inbound_at)
    return last is not None and now - last <= SERVICE_WINDOW


def mask_name(profile_name: str | None, number: str) -> str:
    """How a chat is named in a list: the first name and the initial of the last, else the last
    four digits of the number. Never the whole number."""
    name = (profile_name or "").strip()
    if name:
        parts = name.split()
        if len(parts) > 1:
            return f"{parts[0]} {parts[-1][0]}."
        return parts[0]
    digits = re.sub(r"\D", "", number)
    return f"•••• {digits[-4:]}" if digits else "•••• ••••"


def _item(thread: ChannelThread, crypto: LeadCrypto, now: datetime) -> dict[str, Any]:
    encryptor = crypto.encryptor
    name = (
        encryptor.decrypt(thread.profile_name_encrypted) if thread.profile_name_encrypted else None
    )
    number = encryptor.decrypt(thread.contact_encrypted)
    paused = _utc(thread.bot_paused_until)
    return {
        "id": thread.id,
        "name_masked": mask_name(name, number),
        "last_inbound_at": thread.last_inbound_at,
        "unread_count": thread.unread_count or 0,
        "bot_paused_until": paused if paused is not None and paused > now else None,
        "opted_out": thread.opted_out_at is not None,
        "window_open": window_open(thread, now),
    }


async def list_threads(
    session: "AsyncSession",
    crypto: LeadCrypto,
    corpus_id: uuid.UUID,
    *,
    unread: bool,
    offset: int,
    limit: int,
    now: datetime,
) -> tuple[list[dict[str, Any]], int]:
    where = [ChannelThread.corpus_id == corpus_id]
    if unread:
        where.append(ChannelThread.unread_count > 0)
    rows = (
        (
            await session.execute(
                select(ChannelThread)
                .where(*where)
                .order_by(ChannelThread.last_inbound_at.desc(), ChannelThread.id)
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    total = (
        await session.execute(select(func.count()).select_from(ChannelThread).where(*where))
    ).scalar_one()
    return [_item(t, crypto, now) for t in rows], int(total or 0)


async def get_thread(
    session: "AsyncSession", corpus_id: uuid.UUID, thread_id: uuid.UUID
) -> ChannelThread:
    """The chat, or 404 `whatsapp_thread_not_found` (also for another knowledge base's)."""
    thread = await session.get(ChannelThread, thread_id)
    if thread is None or thread.corpus_id != corpus_id:
        raise APIError(404, "Chat not found.", "whatsapp_thread_not_found")
    return thread


async def thread_detail(
    session: "AsyncSession",
    crypto: LeadCrypto,
    corpus_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    now: datetime,
) -> dict[str, Any]:
    """One chat with its number in full and its last messages; opening it marks it read."""
    thread = await get_thread(session, corpus_id, thread_id)
    encryptor = crypto.encryptor
    messages: list[Message] = []
    if thread.conversation_id is not None:
        messages = list(
            reversed(
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == thread.conversation_id)
                        .order_by(Message.created_at.desc())
                        .limit(MESSAGES_SHOWN)
                    )
                )
                .scalars()
                .all()
            )
        )
    if thread.unread_count:
        await session.execute(
            update(ChannelThread).where(ChannelThread.id == thread.id).values(unread_count=0)
        )
        await session.commit()
        thread.unread_count = 0
    item = _item(thread, crypto, now)
    return {
        **item,
        "unread_count": 0,
        "contact": encryptor.decrypt(thread.contact_encrypted),
        "profile_name": (
            encryptor.decrypt(thread.profile_name_encrypted)
            if thread.profile_name_encrypted
            else None
        ),
        "paused_by": thread.paused_by if item["bot_paused_until"] else None,
        "messages": [
            {
                "id": m.id,
                "role": m.role if m.role in ("user", "assistant", STAFF_ROLE) else "assistant",
                "content": m.content,
                "created_at": m.created_at,
            }
            for m in messages
            if m.role in ("user", "assistant", STAFF_ROLE)
        ],
    }


async def set_bot_paused(
    session: "AsyncSession",
    corpus_id: uuid.UUID,
    thread_id: uuid.UUID,
    *,
    paused: bool,
    connection: ChannelConnection,
    settings: Any,
    now: datetime,
) -> ChannelThread:
    """Pause the bot in this chat for the connection's pause hours, or let it answer now."""
    thread = await get_thread(session, corpus_id, thread_id)
    if paused:
        thread.bot_paused_until = now + timedelta(hours=pause_hours(settings, connection))
        thread.paused_by = "dashboard"
    else:
        thread.bot_paused_until = None
        thread.paused_by = None
    await session.commit()
    return thread


async def send_reply(
    session: "AsyncSession",
    *,
    settings: Any,
    session_factory: Any,
    crypto: LeadCrypto,
    graph: GraphClient,
    connection: ChannelConnection,
    corpus_id: uuid.UUID,
    thread_id: uuid.UUID,
    text: str,
    now: datetime,
    after_send: outbound.AfterSend | None = None,
) -> tuple[uuid.UUID, str]:
    """The owner answers a customer from the dashboard: sent, written to the chat as a `staff`
    message, and the assistant stays quiet in this chat for the pause hours. Only inside the
    customer's 24-hour window (409 `whatsapp_window_closed`)."""
    thread = await get_thread(session, corpus_id, thread_id)
    if connection.status not in ("active", "error"):
        raise APIError(409, "This WhatsApp number is not active.", "whatsapp_not_connected")
    if not window_open(thread, now):
        raise APIError(
            409,
            "More than 24 hours have passed since this customer last wrote. "
            "WhatsApp only allows a free-form reply within 24 hours.",
            "whatsapp_window_closed",
        )
    encryptor = crypto.encryptor
    ctx = outbound.SendContext(
        graph=graph,
        session_factory=session_factory,
        connection_id=connection.id,
        corpus_id=connection.corpus_id,
        tenant_id=connection.tenant_id,
        phone_number_id=connection.phone_number_id,
        token=encryptor.decrypt(connection.access_token_encrypted),
        thread_id=thread.id,
        to=encryptor.decrypt(thread.contact_encrypted).lstrip("+"),
        meta_billing=connection.meta_billing,
        after_send=after_send,
        clock=lambda: now,
    )
    try:
        wamid = await outbound.send_text(ctx, text)
    except outbound.SendError as exc:
        if exc.window_closed:
            raise APIError(
                409, "WhatsApp refused: the 24-hour window has closed.", "whatsapp_window_closed"
            ) from exc
        extra: dict[str, Any] = {"meta_code": exc.meta_code} if exc.meta_code else {}
        raise APIError(
            502, "WhatsApp did not accept the message.", "whatsapp_send_failed", **extra
        ) from exc
    conversation = (
        await session.get(Conversation, thread.conversation_id)
        if thread.conversation_id is not None
        else None
    )
    if conversation is None:
        conversation = Conversation(
            corpus_id=corpus_id,
            channel="whatsapp",
            kind="assistant",
            created_by="whatsapp",
            message_count=0,
            total_tokens=0,
            created_at=now,
            updated_at=now,
        )
        session.add(conversation)
        await session.flush()
        thread.conversation_id = conversation.id
    message = Message(
        conversation_id=conversation.id,
        role=STAFF_ROLE,
        content=text,
        token_count=0,
        created_at=now,
    )
    session.add(message)
    await session.flush()
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.updated_at = now
    thread.bot_paused_until = now + timedelta(hours=pause_hours(settings, connection))
    thread.paused_by = "dashboard"
    return message.id, wamid


# ------------------------------------------------------------------ the connection (TV6 Task 7)


def public_settings(connection: ChannelConnection) -> dict[str, Any]:
    """The connection's settings as the owner sees them (the media-id cache is internal)."""
    raw = dict(connection.settings or {})
    follow: dict[str, Any] = raw.get("follow_up") or {}
    return {
        "human_pause_hours": raw.get("human_pause_hours") or 12,
        "follow_up": {
            "enabled": bool(follow.get("enabled", False)),
            "template_name": follow.get("template_name"),
            "language": follow.get("language"),
            "after_hours": follow.get("after_hours") or 24,
        },
        "shareable_document_ids": [str(i) for i in raw.get("shareable_document_ids") or []],
    }


async def connection_out(
    session: "AsyncSession",
    settings: Any,
    corpus: Any,
    connection: ChannelConnection | None,
) -> dict[str, Any]:
    """The `WhatsAppOut` of a knowledge base: whether WhatsApp is available to it, and its number
    when there is one. Never a token."""
    from callobuzz_rag.channels.whatsapp.availability import embedded_ready
    from callobuzz_rag.channels.whatsapp.documents import shareable_documents
    from callobuzz_rag.channels.whatsapp.signup import is_disconnected

    ready = engine_ready(settings)
    available = ready and await plan_allows(session, corpus.tenant_id)
    out: dict[str, Any] = {
        "available": available,
        "embedded_signup": (
            {
                "app_id": str(settings.whatsapp_app_id),
                "config_id": str(settings.whatsapp_config_id),
                "graph_version": str(settings.whatsapp_graph_version),
            }
            if available and embedded_ready(settings)
            else None
        ),
    }
    if connection is None or is_disconnected(connection):
        return out
    shown = public_settings(connection)
    existing = await shareable_documents(session, corpus.id, shown["shareable_document_ids"])
    shown["shareable_document_ids"] = [str(d.id) for d in existing]  # a deleted file drops out
    out.update(
        {
            "status": connection.status,
            "display_phone": connection.display_phone,
            "verified_name": connection.verified_name,
            "setup": connection.setup,
            "coexistence": connection.coexistence,
            "meta_billing": connection.meta_billing,
            "settings": shown,
            "token_hint": connection.token_hint or None,
            "last_error_code": connection.last_error_code,
            "last_inbound_at": connection.last_inbound_at,
        }
    )
    return out


def apply_settings_patch(connection: ChannelConnection, patch: dict[str, Any]) -> list[str]:
    """Apply an owner's settings patch (already validated field by field) and say which settings
    changed, by name. A follow-up cannot be switched on without its template and language."""
    stored = dict(connection.settings or {})
    changed: list[str] = []
    if patch.get("enabled") is not None:
        if patch["enabled"] and connection.status == "disabled":
            connection.status = "active"
            changed.append("enabled")
        elif not patch["enabled"] and connection.status in ("active", "error"):
            connection.status = "disabled"
            changed.append("enabled")
    hours = patch.get("human_pause_hours")
    if hours is not None and stored.get("human_pause_hours") != hours:
        stored["human_pause_hours"] = hours
        changed.append("human_pause_hours")
    follow = patch.get("follow_up")
    if follow:
        current = dict(stored.get("follow_up") or {})
        merged = {**current, **{k: v for k, v in follow.items() if v is not None}}
        if merged.get("enabled") and not (merged.get("template_name") and merged.get("language")):
            raise APIError(
                422,
                "A follow-up needs the name and language of a template Meta has approved.",
                "whatsapp_follow_up_incomplete",
            )
        for key in ("enabled", "template_name", "language", "after_hours"):
            if current.get(key) != merged.get(key):
                changed.append(f"follow_up.{key}")
        stored["follow_up"] = merged
    connection.settings = stored
    return changed


async def check_number(
    session: "AsyncSession",
    crypto: LeadCrypto,
    graph: GraphClient,
    connection: ChannelConnection,
) -> dict[str, Any]:
    """Read the number from Meta with the saved token. A token Meta accepts clears an `error`
    status; one it refuses sets it."""
    token = crypto.encryptor.decrypt(connection.access_token_encrypted)
    try:
        number = await graph.get_number(connection.phone_number_id, token=token)
    except GraphError as exc:
        if exc.status == 401 or exc.meta_code == 190:
            code = "token_invalid"
        else:
            code = str(exc.meta_code) if exc.meta_code else "unreachable"
        if connection.status == "active":
            connection.status = "error"
        connection.last_error_code = code[:40]
        return {"ok": False, "error_code": code}
    if connection.status == "error":
        connection.status = "active"
    connection.last_error_code = None
    shown = str(number.get("display_phone_number") or "")
    if shown:
        connection.display_phone = shown[:32]
    if number.get("verified_name"):
        connection.verified_name = str(number["verified_name"])[:200]
    return {
        "ok": True,
        "display_phone": connection.display_phone,
        "quality_rating": number.get("quality_rating"),
    }
