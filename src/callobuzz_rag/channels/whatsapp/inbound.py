"""Answering on WhatsApp (TV6): one chat's pending events, one at a time, in order.

The webhook stored each event and woke a worker. `process_thread` claims the chat's oldest
unprocessed event under the chat's row lock (at most one event of a chat is in flight, so two
workers never answer the same message and a customer's messages are answered in the order they
arrived), handles it, and loops until nothing is left.

A customer message goes through the SAME conversation director as the website widget: grounding,
languages, the assistant's rules, leads and the "talk to a person" path. What differs is only
what a channel must: the "talk to a person" check comes first and skips every gate; then the
monthly question cap, billing and fair use (`answer_gate_for`); the metering id is a UUID derived
from the WhatsApp message id, so Meta's retry of a message is never charged twice; replies are
shaped for WhatsApp (`render.py`); an owner's own message (a coexistence echo, or the dashboard
reply box) pauses the bot for that chat; STOP is honoured; a business-initiated message never goes
to someone who said STOP.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels import base
from callobuzz_rag.channels.whatsapp import alerts, documents, optout, outbound, render, strings
from callobuzz_rag.channels.whatsapp.availability import plan_allows
from callobuzz_rag.channels.whatsapp.graph import GraphClient, GraphError
from callobuzz_rag.channels.whatsapp.store import wa_number
from callobuzz_rag.conversation.human import is_human_request
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelEvent, ChannelThread
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)

SOURCE_WHATSAPP = "whatsapp"
SERVICE_WINDOW = timedelta(hours=24)
STAFF_ROLE = "staff"
_SUPPORTED_TYPES = frozenset({"text", "interactive", "button"})
# Things a customer's phone sends that are not a message to answer: a reaction, a system notice,
# the "request welcome" tap, a type Meta cannot deliver. Nothing is sent back (no reply, no fee).
_SILENT_TYPES = frozenset({"reaction", "system", "request_welcome", "unsupported"})
MAX_ATTEMPTS = 5  # a throttled send is queued again this many times
RETRY_BACKOFF = timedelta(seconds=30)
MAX_RETRY_DELAY = timedelta(hours=1)
OPT_IN_YES_ID = "optin_yes"
OPT_IN_NO_ID = "optin_no"


class InboundError(RAGKernelError):
    """A chat could not be processed for a reason the owner can act on (`code`)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class InboundDeps:
    """Everything the answer loop touches, so a test hands in fakes and the worker real ones."""

    session_factory: Callable[[], Any]
    settings: Any
    crypto: Any  # LeadCrypto
    graph: GraphClient
    director_factory: Callable[[Any, Any], Any]  # (corpus, profile) -> Director
    redis: Any = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    after_send: outbound.AfterSend | None = None
    storage: Any = None  # object storage with `download(bucket, key)`: brochures are read from it
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class Claimed:
    """An event this worker holds: nothing else touches it until it is finished."""

    event_id: uuid.UUID
    thread_id: uuid.UUID
    connection_id: uuid.UUID
    corpus_id: uuid.UUID
    kind: str
    external_id: str
    payload: dict[str, Any]
    received_at: datetime
    attempts: int = 0
    # The reply written by an earlier attempt that a throttle interrupted (see `_Pending`).
    pending: dict[str, Any] | None = None


@dataclass
class Ctx:
    """One event's world: the chat, its number, the knowledge base and how to send."""

    deps: InboundDeps
    claimed: Claimed
    connection: ChannelConnection
    thread: ChannelThread
    corpus: Corpus
    contact: str  # E.164
    now: datetime
    send: outbound.SendContext
    lang_hint: str | None = None

    @property
    def contact_digits(self) -> str:
        return wa_number(self.contact)


def request_id_for(wamid: str) -> uuid.UUID:
    """The billing request id of a WhatsApp message: the same message, the same id, so Meta's
    retry (or a worker that ran twice) is metered and charged once."""
    from callobuzz_rag.billing.charging import NAMESPACE_BILLING

    return uuid.uuid5(NAMESPACE_BILLING, f"wa:{wamid}")


def pause_hours(settings: Any, connection: Any) -> int:
    value = (connection.settings or {}).get("human_pause_hours")
    default = int(getattr(settings, "whatsapp_human_pause_hours", 12))
    if isinstance(value, int) and 1 <= value <= 72:
        return value
    return default


# ------------------------------------------------------------------------- claiming


async def claim_next(deps: InboundDeps, thread_id: uuid.UUID) -> Claimed | None:
    """The oldest unprocessed event of the chat, now marked `processing`; None when there is
    nothing to do or another worker has an event of this chat in flight."""
    async with deps.session_factory() as session:
        thread = (
            await session.execute(
                select(ChannelThread).where(ChannelThread.id == thread_id).with_for_update()
            )
        ).scalar_one_or_none()
        if thread is None:
            return None
        busy = (
            await session.execute(
                select(ChannelEvent.id).where(
                    ChannelEvent.thread_id == thread_id, ChannelEvent.status == "processing"
                )
            )
        ).first()
        if busy is not None:
            return None
        events = list(
            (
                await session.execute(
                    select(ChannelEvent)
                    .where(ChannelEvent.thread_id == thread_id, ChannelEvent.status == "received")
                    .order_by(ChannelEvent.received_at, ChannelEvent.id)
                    .limit(1)
                )
            )
            .scalars()
            .all()
        )
        if not events:
            return None
        event = events[0]
        if event.retry_at is not None and event.retry_at > deps.clock():
            return None  # Meta said "not now" for this one; the chat waits, in order
        event.status = "processing"
        event.claimed_at = deps.clock()
        claimed = Claimed(
            event_id=event.id,
            thread_id=thread_id,
            connection_id=event.connection_id,
            corpus_id=event.corpus_id,
            kind=event.kind,
            external_id=event.external_id,
            payload=_payload(deps, event.payload_encrypted),
            received_at=event.received_at,
            attempts=event.attempts or 0,
            pending=_pending_of(deps, event.pending_reply_encrypted),
        )
        await session.commit()
        return claimed


def _payload(deps: InboundDeps, token: str) -> dict[str, Any]:
    import json

    try:
        value = json.loads(deps.crypto.encryptor.decrypt(token))
    except Exception:
        logger.warning("Could not read a stored WhatsApp event", exc_info=True)
        return {}
    return value if isinstance(value, dict) else {}


def _pending_of(deps: InboundDeps, token: str | None) -> dict[str, Any] | None:
    """The kept reply of an event, or None (nothing kept, or unreadable: answered afresh)."""
    if not token:
        return None
    value = _payload(deps, token)
    return value if isinstance(value.get("parts"), list) else None


async def _save_pending(ctx: Ctx, pending: dict[str, Any]) -> None:
    import json

    token = ctx.deps.crypto.encryptor.encrypt(json.dumps(pending, ensure_ascii=False))
    async with ctx.deps.session_factory() as session:
        await session.execute(
            update(ChannelEvent)
            .where(ChannelEvent.id == ctx.claimed.event_id)
            .values(pending_reply_encrypted=token)
        )
        await session.commit()
    ctx.claimed.pending = pending


async def _finish(
    deps: InboundDeps, claimed: Claimed, status: str, code: str | None = None
) -> None:
    """End the event for good: whatever reply was kept is dropped with it."""
    async with deps.session_factory() as session:
        await session.execute(
            update(ChannelEvent)
            .where(ChannelEvent.id == claimed.event_id)
            .values(
                status=status,
                processed_at=deps.clock(),
                error_code=code,
                pending_reply_encrypted=None,
            )
        )
        await session.commit()


# ------------------------------------------------------------------------- the loop


async def process_thread(deps: InboundDeps, thread_id: uuid.UUID) -> dict[str, int]:
    """Answer every pending event of the chat, in order. Returns how many ended in each state."""
    counts = {"done": 0, "ignored": 0, "failed": 0}
    while True:
        claimed = await claim_next(deps, thread_id)
        if claimed is None:
            return counts
        try:
            status, code = await _handle(deps, claimed)
        except Exception:
            logger.exception("WhatsApp event %s failed", claimed.event_id)
            status, code = "failed", "internal"
        if status == "retry":  # put back by `_retry_later`: nothing to finish
            counts["retry"] = counts.get("retry", 0) + 1
            return counts
        await _finish(deps, claimed, status, code)
        counts[status] += 1


async def _handle(deps: InboundDeps, claimed: Claimed) -> tuple[str, str | None]:
    ctx = await _context(deps, claimed)
    if isinstance(ctx, tuple):
        return ctx
    try:
        if claimed.kind == "echo":
            return await _handle_echo(ctx)
        if claimed.kind == "message":
            return await _handle_message(ctx)
    except outbound.SendError as exc:
        return await _send_failed(ctx, exc)
    return "ignored", "kind"


async def _context(deps: InboundDeps, claimed: Claimed) -> "Ctx | tuple[str, str | None]":
    """The event's world, or the (status, code) that ends it before anything is sent."""
    async with deps.session_factory() as session:
        connection = await session.get(ChannelConnection, claimed.connection_id)
        thread = await session.get(ChannelThread, claimed.thread_id)
        corpus = await session.get(Corpus, claimed.corpus_id)
        if connection is None or thread is None or corpus is None:
            return "ignored", "gone"
        if connection.status not in ("active", "error"):
            return "ignored", "not_live"
        tenant = await session.get(Tenant, connection.tenant_id)
        if tenant is None or tenant.status != "active":
            return "ignored", "tenant_inactive"  # pending or suspended: nothing answers
        if not await plan_allows(session, connection.tenant_id):
            switched = await session.execute(
                update(ChannelConnection)
                .where(
                    ChannelConnection.id == connection.id,
                    ChannelConnection.status.in_(("active", "error")),
                )
                .values(status="disabled", last_error_code="plan")
            )
            await session.commit()
            if getattr(switched, "rowcount", 0):  # one mail per switch-off, after the commit
                await alerts.notify_owners(
                    deps.session_factory,
                    deps.settings,
                    tenant_id=connection.tenant_id,
                    number=connection.display_phone,
                    reason="plan",
                    enqueue=deps.extras.get("enqueue_email"),
                )
            return "ignored", "plan"
        token = deps.crypto.encryptor.decrypt(connection.access_token_encrypted)
        contact = deps.crypto.encryptor.decrypt(thread.contact_encrypted)
    send = outbound.SendContext(
        graph=deps.graph,
        session_factory=deps.session_factory,
        connection_id=connection.id,
        corpus_id=connection.corpus_id,
        tenant_id=connection.tenant_id,
        phone_number_id=connection.phone_number_id,
        token=token,
        thread_id=thread.id,
        to=wa_number(contact),
        meta_billing=connection.meta_billing,
        after_send=deps.after_send,
        clock=deps.clock,
    )
    return Ctx(
        deps=deps,
        claimed=claimed,
        connection=connection,
        thread=thread,
        corpus=corpus,
        contact=contact,
        now=deps.clock(),
        send=send,
    )


# ------------------------------------------------------------------------- conversations


def _event_time(ctx: Ctx) -> datetime:
    raw = ctx.claimed.payload.get("ts")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return ctx.claimed.received_at


async def conversation_for(ctx: Ctx, *, at: datetime) -> uuid.UUID:
    """The chat's conversation: the current one, or a new one when the last message of the
    current one is more than 24 hours old (a new service window, a fresh start for strikes and
    lead offers, like a new widget session)."""
    async with ctx.deps.session_factory() as session:
        thread = await session.get(ChannelThread, ctx.thread.id)
        assert thread is not None
        conversation = (
            await session.get(Conversation, thread.conversation_id)
            if thread.conversation_id is not None
            else None
        )
        if conversation is not None:
            last = conversation.updated_at or conversation.created_at
            if last is not None and at - last > SERVICE_WINDOW:
                conversation = None
        if conversation is None:
            conversation = Conversation(
                corpus_id=ctx.corpus.id,
                channel="whatsapp",
                channel_thread_id=thread.id,  # erasing the customer finds every window by this
                kind="assistant",
                created_by="whatsapp",
                message_count=0,
                total_tokens=0,
                created_at=ctx.now,
                updated_at=ctx.now,
            )
            session.add(conversation)
            await session.flush()
            thread.conversation_id = conversation.id
            await session.commit()
        elif conversation.channel_thread_id is None:  # a chat from before the link existed
            conversation.channel_thread_id = thread.id
            await session.commit()
        return conversation.id


async def store_messages(
    ctx: Ctx, conversation_id: uuid.UUID, items: list[tuple[str, str]]
) -> None:
    """Add messages the director did not write (what was said while the bot was quiet, and the
    fixed replies). `items` are `(role, text)`; a customer's text is masked like the director's."""
    from callobuzz_rag.conversation.pii import mask_pii

    async with ctx.deps.session_factory() as session:
        conversation = await session.get(Conversation, conversation_id)
        if conversation is None:
            return
        now = ctx.deps.clock()
        for index, (role, text) in enumerate(items):
            session.add(
                Message(
                    conversation_id=conversation_id,
                    role=role,
                    content=mask_pii(text).text if role == "user" else text,
                    token_count=0,
                    created_at=now + timedelta(microseconds=index),
                )
            )
        conversation.message_count = (conversation.message_count or 0) + len(items)
        conversation.updated_at = now
        await session.commit()


async def _update_thread(ctx: Ctx, **values: Any) -> None:
    async with ctx.deps.session_factory() as session:
        await session.execute(
            update(ChannelThread).where(ChannelThread.id == ctx.thread.id).values(**values)
        )
        await session.commit()


# ------------------------------------------------------------------------- takeover


async def pause_chat(
    deps: InboundDeps,
    thread_id: uuid.UUID,
    *,
    hours: int,
    by: str,
    since: datetime | None = None,
) -> datetime:
    """Keep the bot quiet in this chat until `hours` after `since` (default: now)."""
    until = (since or deps.clock()) + timedelta(hours=hours)
    async with deps.session_factory() as session:
        await session.execute(
            update(ChannelThread)
            .where(ChannelThread.id == thread_id)
            .values(bot_paused_until=until, paused_by=by)
        )
        await session.commit()
    return until


async def _handle_echo(ctx: Ctx) -> tuple[str, str | None]:
    """The owner wrote to this customer from the WhatsApp Business app: the bot steps back."""
    payload = ctx.claimed.payload
    at = _event_time(ctx)
    hours = pause_hours(ctx.deps.settings, ctx.connection)
    if at + timedelta(hours=hours) > ctx.now:  # a late echo of an old message pauses nothing
        await pause_chat(ctx.deps, ctx.thread.id, hours=hours, by="echo", since=at)
    text = str(payload.get("text") or "").strip() or f"[{payload.get('type') or 'message'}]"
    conversation_id = await conversation_for(ctx, at=at)
    await store_messages(ctx, conversation_id, [(STAFF_ROLE, text)])
    return "done", None


# ------------------------------------------------------------------------- messages


def _language(ctx: Ctx, runtime: Any, text: str, conversation_language: str | None) -> str:
    from callobuzz_rag.conversation.language import detect_language, reply_language

    config = runtime.config if runtime is not None else None
    if config is None:
        return conversation_language or "en"
    if text.strip():
        return reply_language(config, detect_language(text))[0]
    return conversation_language or config.language.primary


async def _say(
    ctx: Ctx,
    conversation_id: uuid.UUID,
    user_text: str,
    reply: str,
    *,
    keep: bool = True,
) -> None:
    """A fixed reply: sent, and written to the chat with what the customer said."""
    await outbound.send_text(ctx.send, reply)
    if keep:
        await store_messages(ctx, conversation_id, [("user", user_text), ("assistant", reply)])


def _is_text(payload: dict[str, Any]) -> bool:
    return payload.get("type") in _SUPPORTED_TYPES


async def _quiet(ctx: Ctx, conversation_id: uuid.UUID, shown: str) -> None:
    """Keep what the customer said for the owner, unread, and answer nothing."""
    await store_messages(ctx, conversation_id, [("user", shown)])
    await _update_thread(ctx, unread_count=(ctx.thread.unread_count or 0) + 1)


async def _human_runtime(ctx: Ctx, text: str) -> Any:
    """The assistant is off but the customer asked for a person: the owner's own card still
    exists, so a person is offered (and notified) exactly as when the assistant is on."""
    from callobuzz_rag.conversation.store import get_runtime_config

    if not is_human_request(text):
        return None
    async with ctx.deps.session_factory() as session:
        raw = await get_runtime_config(session, ctx.corpus.id, use_draft=False)
    if raw is None:
        return None
    return replace(raw, config=raw.config.model_copy(update={"enabled": True}))


async def _handle_message(ctx: Ctx) -> tuple[str, str | None]:
    from callobuzz_rag.conversation import surfaces

    payload = ctx.claimed.payload
    at = _event_time(ctx)
    if ctx.claimed.pending is not None:
        return await _resume_reply(ctx, at)
    if ctx.now - at > SERVICE_WINDOW:
        return "ignored", "stale"  # outside the service window: Meta retried it for days
    if payload.get("type") in _SILENT_TYPES:
        return "ignored", "silent"
    conversation_id = await conversation_for(ctx, at=at)
    text = str(payload.get("text") or "").strip()
    button_id = payload.get("button_id")
    shown = text or f"[{payload.get('type') or 'message'}]"
    opt_in_button = button_id in (OPT_IN_YES_ID, OPT_IN_NO_ID)
    if isinstance(button_id, str) and button_id and not opt_in_button:
        said = render.quick_reply_message(button_id)  # a quick reply stands for a sentence
        if said:
            text = said
    # STOP is honoured first, whatever else is going on in the chat (a paused bot included).
    intent = (
        optout.opt_out_intent(text) if (_is_text(payload) and text and not opt_in_button) else None
    )

    # The owner stepped in: the message is kept for them, and nobody answers it.
    paused_until = ctx.thread.bot_paused_until
    if intent is None and paused_until is not None and paused_until > ctx.now:
        await _quiet(ctx, conversation_id, shown)
        return "done", "paused"

    async with ctx.deps.session_factory() as session:
        runtime = await surfaces.assistant_for(session, ctx.corpus, use_draft=False)
        conversation = await session.get(Conversation, conversation_id)
        conversation_language = conversation.language if conversation is not None else None
    lang = _language(ctx, runtime, text, conversation_language)

    if opt_in_button:
        return await _handle_opt_in(
            ctx, conversation_id, shown, lang, yes=button_id == OPT_IN_YES_ID
        )
    if intent is not None:
        return await _handle_stop_start(ctx, conversation_id, text, lang, stop=intent == "stop")

    if ctx.connection.last_error_code == "token":
        # Meta refuses this number's token: answering would run (and meter) the model for a
        # reply that cannot be sent. The owner has been told; the message waits in the chat.
        await _quiet(ctx, conversation_id, shown)
        return "ignored", "token"

    if not _is_text(payload) or not text:
        await _say(ctx, conversation_id, shown, strings.say(strings.UNSUPPORTED, lang))
        return "done", None

    if (
        documents.is_document_request(text)
        and not is_human_request(text)
        and await _send_brochures(ctx, conversation_id, text, lang)
    ):
        return "done", None

    if runtime is None:
        runtime = await _human_runtime(ctx, text)
        if runtime is None:  # assistant off: the owner reads it, nobody answers
            await _quiet(ctx, conversation_id, shown)
            return "ignored", "assistant_off"
        lang = _language(ctx, runtime, text, conversation_language)

    await _mark_read(ctx)
    return await _answer(ctx, conversation_id, text, runtime, lang)


async def _send_brochures(ctx: Ctx, conversation_id: uuid.UUID, text: str, lang: str) -> bool:
    """The customer asked for a brochure, price list or menu: send the best-matching documents
    the owner marked as shareable. False (the normal answer takes the question) when nothing is
    marked, nothing matches, or the files cannot be sent."""
    deps = ctx.deps
    ids = (ctx.connection.settings or {}).get("shareable_document_ids") or []
    if not ids or deps.storage is None:
        return False
    async with deps.session_factory() as session:
        shareable = await documents.shareable_documents(session, ctx.corpus.id, list(ids))
    chosen = documents.choose(shareable, text)
    if not chosen:
        return False
    sent: list[str] = []
    try:
        for document in chosen:
            media_id = await documents.media_id_for(
                deps.session_factory,
                graph=deps.graph,
                storage=deps.storage,
                bucket=deps.settings.s3_bucket,
                connection=ctx.connection,
                token=ctx.send.token,
                document=document,
                now=ctx.now,
            )
            if not sent:
                await outbound.send_text(ctx.send, strings.say(strings.BROCHURE_INTRO, lang))
            await outbound.send_document(
                ctx.send, media_id=media_id, filename=document.file_name or "document"
            )
            sent.append(document.file_name or "document")
    except (GraphError, outbound.SendError, OSError, KeyError):
        logger.warning("Could not send a brochure on WhatsApp", exc_info=True)
        if not sent:
            return False
    except Exception:
        logger.warning("Could not send a brochure on WhatsApp", exc_info=True)
        if not sent:
            return False
    await store_messages(
        ctx, conversation_id, [("user", text), ("assistant", "Sent: " + ", ".join(sent))]
    )
    return True


async def _mark_read(ctx: Ctx) -> None:
    try:
        await ctx.deps.graph.mark_read(
            ctx.send.phone_number_id, token=ctx.send.token, message_id=ctx.claimed.external_id
        )
    except Exception:
        logger.debug("Could not mark a WhatsApp message read", exc_info=True)


async def _handle_stop_start(
    ctx: Ctx, conversation_id: uuid.UUID, text: str, lang: str, *, stop: bool
) -> tuple[str, str | None]:
    if stop:
        already = ctx.thread.opted_out_at is not None
        await _update_thread(ctx, opted_out_at=ctx.now if not already else ctx.thread.opted_out_at)
        # one confirmation, whatever the wording; a repeated STOP is still answered once
        await _say(ctx, conversation_id, text, strings.say(strings.STOPPED, lang))
    else:
        await _update_thread(ctx, opted_out_at=None)
        await _say(ctx, conversation_id, text, strings.say(strings.STARTED, lang))
    return "done", None


async def _handle_opt_in(
    ctx: Ctx, conversation_id: uuid.UUID, shown: str, lang: str, *, yes: bool
) -> tuple[str, str | None]:
    if yes and ctx.thread.opted_out_at is None:
        await _update_thread(ctx, opted_in_at=ctx.now)
        reply = strings.say(strings.OPT_IN_THANKS, lang)
    else:
        reply = strings.say(strings.OPT_IN_DECLINED, lang)
    await _say(ctx, conversation_id, shown, reply)
    return "done", None


# ------------------------------------------------------------------------- the answer


def _team_label(runtime: Any) -> str:
    name = (runtime.config.identity.business_name or "").strip() or "the business"
    return f"{name} team"


def _channel_runtime(runtime: Any) -> Any:
    """The same assistant, with one rule changed for WhatsApp: it is a business's number, so the
    assistant stays on topic. `open_with_guardrails` is read as `docs_plus_domain`."""
    scope = runtime.config.scope
    if scope.scope_mode != "open_with_guardrails":
        return runtime
    config = runtime.config.model_copy(
        update={"scope": scope.model_copy(update={"scope_mode": "docs_plus_domain"})}
    )
    return replace(runtime, config=config)


async def _disclosure_line(ctx: Ctx, runtime: Any, lang: str) -> str | None:
    """The fixed "you are talking to an AI" line (TV5): due when the deployer requires it or the
    owner has it on, worded as the widget words it. The model is also told to say it, but a line
    the model may or may not obey is not a disclosure."""
    from callobuzz_rag.conversation import disclosure

    required, editable = await disclosure.read_trust(ctx.deps.session_factory)
    return disclosure.forced_line(runtime.config, lang, required=required, editable=editable)


def _with_disclosure(parts: list[dict[str, Any]], line: str) -> list[dict[str, Any]]:
    """The reply's messages with the disclosure first, a message of its own (a long answer may
    already fill a whole WhatsApp message)."""
    return [{"t": "text", "text": line}, *parts]


async def _answer(
    ctx: Ctx, conversation_id: uuid.UUID, text: str, runtime: Any, lang: str
) -> tuple[str, str | None]:
    from callobuzz_rag.billing.enforcement import payer_hint
    from callobuzz_rag.conversation import learning as site_learning
    from callobuzz_rag.conversation import surfaces
    from callobuzz_rag.conversation.director import DirectorError
    from callobuzz_rag.fairuse.gate import answer_gate_for, pause_args
    from callobuzz_rag.tiers import profiles as tier_profiles

    deps = ctx.deps
    runtime = _channel_runtime(runtime)
    human = surfaces.is_human_turn(runtime, text)  # TV1: asked for a person; free, first
    request = base.ChannelRequest(
        settings=deps.settings, session_factory=deps.session_factory, redis=deps.redis
    )
    pause: dict[str, Any] = {}
    try:
        async with deps.session_factory() as session:
            if human:
                # TV1 D2: reaching a person costs the business nothing, no pass-through fee
                ctx.send = replace(ctx.send, after_send=None)
                profile = await tier_profiles.plan_profile(
                    session, ctx.corpus.tenant_id, settings=deps.settings
                )
            else:
                # P5 plans: a stored key pays only on a plan that includes it (shared with the gate)
                plan = await tier_profiles.plan_profile(
                    session, ctx.corpus.tenant_id, settings=deps.settings
                )
                try:
                    decision = await answer_gate_for(
                        session,
                        request,
                        ctx.corpus,
                        payer=payer_hint(
                            ctx.corpus, None, use_headers=False, byok_allowed=plan.byok_allowed
                        ),
                        plan_profile=plan,
                    )
                except APIError as exc:
                    if exc.code != "question_cap_reached":
                        raise
                    # The monthly cap: the widget's polite sentence, no model, nothing metered
                    # and no pass-through fee (a fixed reply the owner did not choose to send).
                    ctx.send = replace(ctx.send, after_send=None)
                    await _say(ctx, conversation_id, text, strings.say(strings.NOT_AVAILABLE, lang))
                    return "done", "cap"
                profile = decision.profile
                pause = pause_args(decision)
                if pause.get("paused_by"):  # the pause reply is fixed text: no fee for it
                    ctx.send = replace(ctx.send, after_send=None)
            _conv, state, history = await surfaces.conversation_context(
                session,
                ctx.corpus.id,
                conversation_id,
                deps.settings,
                staff_label=_team_label(runtime),
            )
            learning = await site_learning.kb_is_learning(session, ctx.corpus.id)
    finally:
        # the gate's alert tasks (claims already committed) finish before the worker's engine
        # closes at the end of its `asyncio.run`, or a notice would be cut off for good
        from callobuzz_rag.fairuse import alerts

        await alerts.drain(request.app)
        client = request.app.state.fair_use_redis
        if client is not None and deps.redis is None:  # the gate made it for this message
            try:
                await client.aclose()
            except Exception:
                logger.debug("Could not close the fair-use Redis client", exc_info=True)
    # TV5 disclosure: the first reply of a chat says it is an AI; the state remembers it (written
    # with the turn), so the line is sent once, and a failed answer sends and records nothing
    disclosure = None if state.ai_disclosed else await _disclosure_line(ctx, runtime, lang)
    if disclosure:
        state.ai_disclosed = True
    try:
        from callobuzz_rag.tiers.apply import runtime_with_profile

        turn_runtime = runtime_with_profile(runtime, profile)
        turn = surfaces.make_turn(
            corpus=ctx.corpus,
            runtime=turn_runtime,
            text=text,
            channel=Channel.WHATSAPP,
            usage_source=SOURCE_WHATSAPP,
            conversation_id=conversation_id,
            state=state,
            history=history,
            page_url=None,
            session_token=None,
            opts=None,
            one_off=None,
            learning=learning,
            human_only=human,
            request_id=request_id_for(ctx.claimed.external_id),
            contact_phone=ctx.contact,
            **pause,
        )
        turn = replace(turn, now=deps.clock())  # one clock for the whole chat
        lead_before = state.lead_id
        director = deps.director_factory(ctx.corpus, profile)
        try:
            done = await director.run(turn)
        finally:
            await director.aclose()
    except DirectorError:
        logger.warning("WhatsApp answer failed for corpus %s", ctx.corpus.id)
        await _say(ctx, conversation_id, text, strings.say(strings.COULD_NOT_ANSWER, lang))
        return "done", "answer_failed"
    rendered = render.render_done(
        done,
        business_name=(runtime.config.identity.business_name or "").strip(),
        lang=done.language or lang,
    )
    handoff = getattr(getattr(done, "ui", None), "kind", None) == "handoff_card" or (
        getattr(getattr(done, "route", None), "value", None) == "handoff"
    )
    parts = _parts_of(rendered)
    if disclosure:
        parts = _with_disclosure(parts, disclosure)
    pending: dict[str, Any] = {
        "parts": parts,
        "sent": 0,
        "lang": lang,
        "handoff": handoff,
        "lead_created": bool(turn.state.lead_id) and not lead_before,
        "no_fee": ctx.send.after_send is None,
    }
    # The turn is written (the director did it) and the model is paid for: keep the reply before
    # the first send, so a throttle's retry sends it and never answers the question again.
    await _save_pending(ctx, pending)
    return await _send_pending(ctx)


def _parts_of(rendered: Any) -> list[dict[str, Any]]:
    """The messages of a `render.Rendered`, in the order they are sent."""
    parts: list[dict[str, Any]] = [{"t": "text", "text": text} for text in rendered.texts]
    if rendered.buttons:
        parts.append(
            {
                "t": "buttons",
                "body": rendered.button_body,
                "buttons": [[bid, title] for bid, title in rendered.buttons],
            }
        )
    return parts


async def _send_pending(ctx: Ctx) -> tuple[str, str | None]:
    """Send the kept reply's messages that have not gone out, recording each one as it does. A
    throttle puts the event back with the rest still kept; a send Meta may have accepted
    (`maybe_sent`) or any other failure ends it, and nothing is ever sent twice."""
    pending = ctx.claimed.pending or {}
    parts: list[dict[str, Any]] = pending.get("parts") or []
    if pending.get("no_fee"):
        ctx.send = replace(ctx.send, after_send=None)
    try:
        for index in range(int(pending.get("sent") or 0), len(parts)):
            part = parts[index]
            if part.get("t") == "buttons":
                await outbound.send_buttons(
                    ctx.send, part["body"], [(str(b[0]), str(b[1])) for b in part["buttons"]]
                )
            else:
                await outbound.send_text(ctx.send, str(part.get("text") or ""))
            pending = {**pending, "sent": index + 1}
            await _save_pending(ctx, pending)
    except outbound.SendError as exc:
        return await _send_failed(ctx, exc)
    await _after_answer(
        ctx,
        str(pending.get("lang") or "en"),
        handoff=bool(pending.get("handoff")),
        lead_created=bool(pending.get("lead_created")),
    )
    return "done", None


async def _resume_reply(ctx: Ctx, at: datetime) -> tuple[str, str | None]:
    """A throttled send is tried again: the reply was written by the first attempt, so only its
    unsent messages go out and the model is not asked again. The rules of a fresh message still
    hold: a free-form message needs the 24 hour window, and an owner who stepped in since keeps
    the chat."""
    if ctx.now - at > SERVICE_WINDOW:
        # Too late for a free-form message: the owner reads it (the turn is already in the chat).
        await _update_thread(ctx, unread_count=(ctx.thread.unread_count or 0) + 1)
        return "failed", "window_closed"
    paused_until = ctx.thread.bot_paused_until
    if paused_until is not None and paused_until > ctx.now:
        return "done", "paused"
    if ctx.connection.last_error_code == "token":
        await _update_thread(ctx, unread_count=(ctx.thread.unread_count or 0) + 1)
        return "ignored", "token"
    return await _send_pending(ctx)


async def _after_answer(ctx: Ctx, lang: str, *, handoff: bool, lead_created: bool) -> None:
    """After a lead or a handoff, and only when the owner turned follow-ups on: ask once whether
    the customer is happy to be messaged here. The answer is recorded (`opted_in_at`), and
    nothing business-initiated is ever sent without it."""
    from callobuzz_rag.channels.whatsapp.followups import follow_up_settings

    thread = ctx.thread
    if not (handoff or lead_created):
        return
    if follow_up_settings(ctx.connection) is None:
        return
    if thread.opt_in_asked_at or thread.opted_in_at or thread.opted_out_at:
        return
    try:
        await outbound.send_buttons(
            ctx.send,
            strings.say(strings.OPT_IN_ASK, lang),
            [
                (OPT_IN_YES_ID, strings.say(strings.OPT_IN_YES, lang)),
                (OPT_IN_NO_ID, strings.say(strings.OPT_IN_NO, lang)),
            ],
        )
    except outbound.SendError:
        logger.warning("Could not ask for the WhatsApp opt-in for corpus %s", ctx.corpus.id)
        return
    await _update_thread(ctx, opt_in_asked_at=ctx.deps.clock())


async def _flag_token(ctx: Ctx) -> None:
    """A token Meta refuses puts the connection in `error` and tells the owners, once."""
    async with ctx.deps.session_factory() as session:
        changed = await session.execute(
            update(ChannelConnection)
            .where(
                ChannelConnection.id == ctx.connection.id,
                ChannelConnection.status.in_(("active", "error")),
                or_(
                    ChannelConnection.status != "error",
                    ChannelConnection.last_error_code.is_(None),
                    ChannelConnection.last_error_code != "token",
                ),
            )
            .values(status="error", last_error_code="token")
        )
        await session.commit()
    if getattr(changed, "rowcount", 0):  # the first failure of this kind: one mail, after commit
        await alerts.notify_owners(
            ctx.deps.session_factory,
            ctx.deps.settings,
            tenant_id=ctx.connection.tenant_id,
            number=ctx.connection.display_phone,
            reason="token",
            enqueue=ctx.deps.extras.get("enqueue_email"),
        )


async def _retry_later(ctx: Ctx, exc: outbound.SendError) -> tuple[str, str | None] | None:
    """Meta throttled the send (nothing went out): the event goes back to `received` with a
    backoff, to be answered later, in order. None when the attempts are used up."""
    attempts = ctx.claimed.attempts + 1
    if attempts >= MAX_ATTEMPTS:
        return None
    delay = min(
        max(timedelta(seconds=exc.retry_after or 0), RETRY_BACKOFF * (2 ** (attempts - 1))),
        MAX_RETRY_DELAY,
    )
    async with ctx.deps.session_factory() as session:
        await session.execute(
            update(ChannelEvent)
            .where(ChannelEvent.id == ctx.claimed.event_id)
            .values(
                status="received",
                attempts=attempts,
                retry_at=ctx.deps.clock() + delay,
                claimed_at=None,
                error_code="rate_limited",
            )
        )
        await session.commit()
    logger.warning("WhatsApp send throttled for corpus %s; retry %s", ctx.corpus.id, attempts)
    return "retry", "rate_limited"


async def _send_failed(ctx: Ctx, exc: outbound.SendError) -> tuple[str, str | None]:
    """Record why a send failed: a token Meta refuses puts the connection in `error` (and the
    owners are told); a throttle queues the event again."""
    if exc.code == "token_invalid":
        await _flag_token(ctx)
    elif exc.retryable:
        later = await _retry_later(ctx, exc)
        if later is not None:
            return later
    logger.warning("WhatsApp send failed (%s) for corpus %s", exc.code, ctx.corpus.id)
    return "failed", exc.code


__all__ = [
    "Ctx",
    "GraphError",
    "InboundDeps",
    "InboundError",
    "claim_next",
    "pause_chat",
    "process_thread",
    "request_id_for",
]
