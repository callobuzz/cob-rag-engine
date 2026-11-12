"""Connecting a WhatsApp number (TV6): by Meta's Embedded Signup, or by hand.

Two ways, one result (a `channel_connections` row with the token encrypted):

- **Embedded Signup** (v4), by the business's owner: the dashboard opens Meta's window through the
  deployer's Meta app; Meta returns a short-lived code (30 seconds) and the ids of the number and
  its WABA. The server exchanges the code for the business's token, registers the number with a
  generated six-digit PIN (a number also used in the WhatsApp Business app, "coexistence", is
  already registered and is left alone), subscribes the app to the WABA's webhooks, reads the
  number back, and only then saves. Any step that fails answers 502 `whatsapp_signup_failed`
  naming the step, and nothing is saved. The business pays Meta itself (`meta_billing =
  "business"`).
- **By hand**, by the deployer: the number's id, its WABA id and a permanent token the deployer
  made in their own Meta app. The token is checked against the number before it is saved. The
  deployer says who pays Meta. A number that is not registered with the Cloud API yet needs a
  six-digit PIN: when the deployer gives one it is registered with it (and kept encrypted); a
  number the deployer already registered in Meta's dashboard is connected without one.

Every id is digits before it reaches a URL path (`graph.check_id`). A token is never returned,
logged or put in an audit row; only its last four characters are shown.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.channels.whatsapp.graph import GraphClient, GraphError, WhatsAppIdError, check_id
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core.secrets import EncryptionError
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelThread

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

TOMBSTONE_PREFIX = "gone-"


@dataclass
class Connected:
    """The saved connection and what the audit row may say about it."""

    connection: ChannelConnection
    replaced_token: bool


def ids_or_422(*, phone_number_id: str, waba_id: str) -> tuple[str, str]:
    """Both ids, or 422 `whatsapp_id_invalid` (digits only, 5 to 20)."""
    try:
        return check_id(phone_number_id.strip()), check_id(waba_id.strip())
    except WhatsAppIdError as exc:
        raise APIError(
            422, "A phone number id and a WABA id are digits only.", "whatsapp_id_invalid"
        ) from exc


def derive_pin(crypto: LeadCrypto, phone_number_id: str) -> str:
    """The six-digit two-step-verification PIN for a number registered by Embedded Signup.

    Derived (keyed hash of the number id), not random: a sign-up that fails after the number was
    registered, or a number disconnected and connected again, asks for the same PIN every time.
    A random PIN that was lost with the failed attempt made every retry fail, because Meta
    refuses a register call whose PIN is not the one already set."""
    digest = crypto.index_hash(f"whatsapp-pin:{phone_number_id}") or ""
    return f"{int(digest, 16) % 10**6:06d}"


def _pin_for(crypto: LeadCrypto, existing: ChannelConnection | None, phone_number_id: str) -> str:
    """The PIN already kept for this very number, else the derived one."""
    if (
        existing is not None
        and existing.phone_number_id == phone_number_id
        and existing.pin_encrypted
    ):
        try:
            stored = crypto.encryptor.decrypt(existing.pin_encrypted)
        except EncryptionError:
            stored = ""
        if stored and stored.isdigit() and len(stored) == 6:
            return stored
    return derive_pin(crypto, phone_number_id)


def display_hint(display_phone: str) -> str:
    """The end of a number, for the audit row and logs: never the whole number."""
    digits = "".join(ch for ch in display_phone if ch.isdigit())
    return f"…{digits[-4:]}" if digits else ""


def _failed(step: str) -> APIError:
    return APIError(
        502,
        "Meta did not complete the WhatsApp sign-up.",
        "whatsapp_signup_failed",
        step=step,
    )


async def _number_free(
    session: "AsyncSession", phone_number_id: str, corpus_id: uuid.UUID
) -> ChannelConnection | None:
    """The knowledge base's own connection (to be replaced), or None; 409
    `whatsapp_number_in_use` when the number is on another knowledge base."""
    holder = (
        await session.execute(
            select(ChannelConnection).where(ChannelConnection.phone_number_id == phone_number_id)
        )
    ).scalar_one_or_none()
    if holder is not None and holder.corpus_id != corpus_id:
        raise APIError(
            409,
            "That WhatsApp number is already connected to another knowledge base.",
            "whatsapp_number_in_use",
        )
    return (
        await session.execute(
            select(ChannelConnection).where(
                ChannelConnection.corpus_id == corpus_id, ChannelConnection.kind == "whatsapp"
            )
        )
    ).scalar_one_or_none()


async def _start_fresh(session: "AsyncSession", connection: ChannelConnection) -> None:
    """The row is about to carry a different number. The chats stay readable, but nothing that was
    agreed with one WhatsApp number (a customer's opt-in or STOP, the follow-up already sent, a
    pause after a human replied) is carried to another, and the other number's uploaded media ids
    are not valid on this one."""
    await session.execute(
        update(ChannelThread)
        .where(ChannelThread.connection_id == connection.id)
        .values(
            opted_in_at=None,
            opted_out_at=None,
            opt_in_asked_at=None,
            follow_up_sent_at=None,
            bot_paused_until=None,
            paused_by=None,
        )
    )
    kept = dict(connection.settings or {})
    kept.pop("media_cache", None)
    connection.settings = kept


async def _save(
    session: "AsyncSession",
    crypto: LeadCrypto,
    *,
    existing: ChannelConnection | None,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    phone_number_id: str,
    waba_id: str,
    token: str,
    pin: str | None,
    setup: str,
    coexistence: bool,
    meta_billing: str,
    number: dict[str, Any],
    created_by: str,
    reactivate: bool,
) -> Connected:
    encryptor = crypto.encryptor
    values: dict[str, Any] = {
        "status": "active",
        "phone_number_id": phone_number_id,
        "waba_id": waba_id,
        "display_phone": str(number.get("display_phone_number") or "")[:32],
        "verified_name": (
            str(number["verified_name"])[:200] if number.get("verified_name") else None
        ),
        "access_token_encrypted": encryptor.encrypt(token),
        "token_hint": token[-4:],
        "pin_encrypted": encryptor.encrypt(pin) if pin else None,
        "setup": setup,
        "coexistence": coexistence,
        "meta_billing": meta_billing,
        "last_error_code": None,
    }
    if existing is not None:
        same_number = existing.phone_number_id == phone_number_id and not is_disconnected(existing)
        if same_number:
            # A new token on the same number. Whoever switched the number off (the owner, or the
            # plan) keeps it off unless this is the owner's own sign-up.
            if existing.status == "disabled" and not reactivate:
                values["status"] = "disabled"
        else:
            await _start_fresh(session, existing)
        for name, value in values.items():
            setattr(existing, name, value)
        existing.created_by = created_by[:320]
        return Connected(existing, replaced_token=True)
    connection = ChannelConnection(
        corpus_id=corpus_id,
        tenant_id=tenant_id,
        kind="whatsapp",
        settings={},
        created_by=created_by[:320],
        **values,
    )
    session.add(connection)
    await session.flush()
    return Connected(connection, replaced_token=False)


async def connect_embedded(
    session: "AsyncSession",
    crypto: LeadCrypto,
    graph: GraphClient,
    *,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    code: str,
    phone_number_id: str,
    waba_id: str,
    coexistence: bool,
    created_by: str,
) -> Connected:
    """The owner's Embedded Signup, start to finish. Nothing is saved unless every step worked."""
    phone_number_id, waba_id = ids_or_422(phone_number_id=phone_number_id, waba_id=waba_id)
    existing = await _number_free(session, phone_number_id, corpus_id)
    try:
        token = await graph.exchange_code(code)
    except GraphError as exc:
        raise _failed("exchange_code") from exc
    if not token:
        raise _failed("exchange_code")
    pin: str | None = None
    if not coexistence:
        pin = _pin_for(crypto, existing, phone_number_id)
        try:
            await graph.register_number(phone_number_id, token=token, pin=pin)
        except GraphError as exc:
            raise _failed("register") from exc
    try:
        await graph.subscribe_app(waba_id, token=token)
    except GraphError as exc:
        raise _failed("subscribe") from exc
    try:
        number = await graph.get_number(phone_number_id, token=token)
    except GraphError as exc:
        raise _failed("read_number") from exc
    return await _save(
        session,
        crypto,
        existing=existing,
        corpus_id=corpus_id,
        tenant_id=tenant_id,
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        token=token,
        pin=pin,
        setup="embedded",
        coexistence=coexistence,
        meta_billing="business",
        number=number,
        created_by=created_by,
        reactivate=True,
    )


async def connect_manual(
    session: "AsyncSession",
    crypto: LeadCrypto,
    graph: GraphClient,
    *,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    phone_number_id: str,
    waba_id: str,
    access_token: str,
    meta_billing: str,
    created_by: str,
    pin: str | None = None,
) -> Connected:
    """The deployer connects a number on their own WhatsApp account. The token is checked by
    reading the number with it (422 `whatsapp_token_invalid`). With a `pin` the number is also
    registered with the Cloud API (502 `whatsapp_signup_failed`, step `register`, and nothing
    saved if Meta refuses); without one it is taken as already registered. The app is subscribed
    to the WABA's webhooks as a best effort (the deployer may have done it in Meta's dashboard)."""
    phone_number_id, waba_id = ids_or_422(phone_number_id=phone_number_id, waba_id=waba_id)
    token = access_token.strip()
    existing = await _number_free(session, phone_number_id, corpus_id)
    try:
        number = await graph.get_number(phone_number_id, token=token)
    except GraphError as exc:
        raise APIError(
            422, "Meta did not accept this token for that number.", "whatsapp_token_invalid"
        ) from exc
    if pin:
        try:
            await graph.register_number(phone_number_id, token=token, pin=pin)
        except GraphError as exc:
            raise _failed("register") from exc
    try:
        await graph.subscribe_app(waba_id, token=token)
    except GraphError:
        logger.warning("Could not subscribe the app to WABA %s; the deployer may have", waba_id)
    return await _save(
        session,
        crypto,
        existing=existing,
        corpus_id=corpus_id,
        tenant_id=tenant_id,
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        token=token,
        pin=pin or None,
        setup="manual",
        coexistence=False,
        meta_billing=meta_billing,
        number=number,
        created_by=created_by,
        reactivate=False,
    )


async def _waba_shared(session: "AsyncSession", connection: ChannelConnection) -> bool:
    """Another number that is still connected sits on this connection's WABA."""
    others = (
        (
            await session.execute(
                select(ChannelConnection).where(
                    ChannelConnection.waba_id == connection.waba_id,
                    ChannelConnection.id != connection.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return any(not is_disconnected(other) for other in others)


async def disconnect(
    session: "AsyncSession", crypto: LeadCrypto, graph: GraphClient, connection: ChannelConnection
) -> None:
    """Remove the token and the PIN, stop the webhook subscription (best effort), and free the
    number. The row stays, so the chats stay readable: its number becomes a tombstone no
    webhook can match and the knowledge base can connect another (or the same) number.

    Meta's subscription belongs to the WABA, not to the number: it is dropped only when no other
    connected number (of any business) uses the same WABA, or their messages would go silent."""
    if not await _waba_shared(session, connection):
        try:
            token = crypto.encryptor.decrypt(connection.access_token_encrypted)
            if token:
                await graph.unsubscribe_app(connection.waba_id, token=token)
        except Exception:
            logger.warning("Could not unsubscribe the app from WABA %s", connection.waba_id)
    connection.status = "disabled"
    connection.access_token_encrypted = ""
    connection.token_hint = ""
    connection.pin_encrypted = None
    connection.phone_number_id = (TOMBSTONE_PREFIX + uuid.uuid4().hex)[:20]
    connection.last_error_code = None


def is_disconnected(connection: ChannelConnection) -> bool:
    return connection.phone_number_id.startswith(TOMBSTONE_PREFIX) or not (
        connection.access_token_encrypted
    )
