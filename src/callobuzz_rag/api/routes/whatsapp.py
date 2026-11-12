"""WhatsApp for a knowledge base: the number's connection and settings, and its chats (TV6).

Scoped like every knowledge-base route (`path_corpus`): admin any, a tenant its own, a corpus key
its one. Connecting by Meta's sign-up window, changing settings, choosing the brochures and
disconnecting are owner-only; the deployer connects a number by hand (admin only). Reading and
answering chats is for the whole team (`write` for named keys, since a chat shows a phone number).

The routes enforce the workspace's plan themselves (403 `whatsapp_not_in_plan`): WhatsApp is an
entitlement of the plan, not a screen the owner may hide. Chats stay readable after the plan
loses it or the number is disconnected.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.api.deps import get_session, path_corpus, require_admin, require_owner
from callobuzz_rag.api.schemas.whatsapp import (
    CheckOut,
    ConnectionRow,
    ConnectionsResponse,
    DocumentsRequest,
    EmbeddedSignupRequest,
    ManualConnectRequest,
    ReplyRequest,
    ReplyResponse,
    ThreadDetail,
    ThreadItem,
    ThreadListResponse,
    ThreadPatch,
    WhatsAppOut,
    WhatsAppSettingsPatch,
)
from callobuzz_rag.channels.whatsapp import documents, fees, service, signup
from callobuzz_rag.cob.system import refuse_system_write
from callobuzz_rag.db.models.channel import ChannelConnection
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.tenant import Tenant

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/corpora/{corpus_id}/whatsapp", tags=["whatsapp"])


def _now() -> datetime:
    return datetime.now(UTC)


@router.get("/threads", response_model=ThreadListResponse)
async def list_threads(
    request: Request,
    unread: bool = Query(False, description="Only chats with messages nobody has read"),
    offset: int = Query(0, ge=0),
    limit: int = Query(service.THREAD_PAGE, ge=1, le=service.THREAD_PAGE),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ThreadListResponse:
    """The WhatsApp chats of this knowledge base, the most recent customer message first. A chat
    is named by the customer's first name and last initial, else the end of their number."""
    crypto = service.crypto_for(request.app.state.settings)
    items, total = await service.list_threads(
        session, crypto, corpus.id, unread=unread, offset=offset, limit=limit, now=_now()
    )
    return ThreadListResponse(items=[ThreadItem(**i) for i in items], total=total)


@router.get("/threads/{thread_id}", response_model=ThreadDetail)
async def get_thread(
    thread_id: uuid.UUID,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ThreadDetail:
    """One chat: the customer's number in full and the last 100 messages (the customer's, the
    assistant's and yours). Opening it marks it read."""
    await service.get_thread(session, corpus.id, thread_id)  # an unknown chat is a 404 first
    crypto = service.crypto_for(request.app.state.settings)
    detail = await service.thread_detail(session, crypto, corpus.id, thread_id, now=_now())
    return ThreadDetail(**detail)


@router.post("/threads/{thread_id}/reply", response_model=ReplyResponse, status_code=201)
async def reply(
    thread_id: uuid.UUID,
    body: ReplyRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ReplyResponse:
    """Answer a customer from the dashboard. Text only, up to 4,096 characters, and only within
    24 hours of the customer's last message (409 `whatsapp_window_closed`). The assistant then
    stays quiet in this chat for the number of hours set for the number."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    service.require_engine(settings)
    await service.require_plan(session, corpus.tenant_id)
    connection = await service.require_connection(session, corpus.id)
    now = _now()
    message_id, wamid = await service.send_reply(
        session,
        settings=settings,
        session_factory=request.app.state.session_factory,
        crypto=service.crypto_for(settings),
        graph=service.make_graph(settings),
        connection=connection,
        corpus_id=corpus.id,
        thread_id=thread_id,
        text=body.text,
        now=now,
        after_send=fees.charge_send,
    )
    await record_audit(
        session,
        request,
        "whatsapp.reply_sent",
        tenant_id=corpus.tenant_id,
        target_type="whatsapp_thread",
        target_id=str(thread_id),
        details={"corpus_id": str(corpus.id), "thread_id": str(thread_id)},
    )
    await session.commit()
    return ReplyResponse(message_id=message_id, whatsapp_message_id=wamid)


@router.patch("/threads/{thread_id}", response_model=ThreadItem)
async def update_thread(
    thread_id: uuid.UUID,
    body: ThreadPatch,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> ThreadItem:
    """Keep the assistant quiet in one chat (`bot_paused: true`), or let it answer again at once
    (`false`)."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    connection = await service.require_connection(session, corpus.id)
    now = _now()
    thread = await service.set_bot_paused(
        session,
        corpus.id,
        thread_id,
        paused=body.bot_paused,
        connection=connection,
        settings=settings,
        now=now,
    )
    crypto = service.crypto_for(settings)
    return ThreadItem(**service._item(thread, crypto, now))


# ------------------------------------------------------------------ the connection


def _created_by(request: Request) -> str:
    return actor_from_request(request)[2][:320]


async def _out(
    session: AsyncSession, request: Request, corpus: Corpus, connection: ChannelConnection | None
) -> WhatsAppOut:
    return WhatsAppOut(
        **await service.connection_out(session, request.app.state.settings, corpus, connection)
    )


@router.get("", response_model=WhatsAppOut)
async def get_connection(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WhatsAppOut:
    """Whether WhatsApp can be used here, and this knowledge base's number if it has one:
    its status, who pays Meta, the settings, the end of the token. For a business that has not
    connected one, `status` is null and `embedded_signup` carries what the sign-up window needs."""
    connection = await service.connection_of(session, corpus.id)
    return await _out(session, request, corpus, connection)


@router.post("/embedded-signup", response_model=WhatsAppOut, status_code=201)
async def embedded_signup(
    body: EmbeddedSignupRequest,
    request: Request,
    _owner: None = Depends(require_owner),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WhatsAppOut:
    """Connect the business's own number from Meta's sign-up window: the code and ids the window
    returned. The server exchanges the code, registers the number, subscribes to its messages and
    reads it back; if any step fails (502 `whatsapp_signup_failed`, with `step`) nothing is
    saved. The business pays Meta for its messages itself."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    service.require_engine(settings)
    service.require_public_address(request)
    await service.require_plan(session, corpus.tenant_id)
    connected = await signup.connect_embedded(
        session,
        service.crypto_for(settings),
        service.make_graph(settings),
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        code=body.code,
        phone_number_id=body.phone_number_id,
        waba_id=body.waba_id,
        coexistence=body.coexistence,
        created_by=_created_by(request),
    )
    await _audit_connected(session, request, corpus, connected)
    await session.commit()
    return await _out(session, request, corpus, connected.connection)


async def _audit_connected(
    session: AsyncSession,
    request: Request,
    corpus: Corpus,
    connected: signup.Connected,
    *,
    token_only: bool = False,
) -> None:
    connection = connected.connection
    await record_audit(
        session,
        request,
        "whatsapp.token_updated" if token_only else "whatsapp.connected",
        tenant_id=corpus.tenant_id,
        target_type="whatsapp_connection",
        target_id=str(connection.id),
        details={
            "corpus_id": str(corpus.id),
            "setup": connection.setup,
            "display_phone_hint": signup.display_hint(connection.display_phone),
        },
    )


@router.put("/manual", response_model=WhatsAppOut, dependencies=[Depends(require_admin)])
async def connect_manually(
    body: ManualConnectRequest,
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WhatsAppOut:
    """The deployer connects a number by hand: its id, its WABA id and a permanent token from
    the deployer's own Meta app, and who pays Meta (`business` or `deployer`). The token is
    checked against the number before it is saved (422 `whatsapp_token_invalid`) and is never
    shown again. Putting a new token on a number that is already connected only replaces the token."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    service.require_engine(settings)
    service.require_public_address(request)
    await service.require_plan(session, corpus.tenant_id)
    before = await service.connection_of(session, corpus.id)
    same_number = (
        before is not None
        and not signup.is_disconnected(before)
        and before.phone_number_id == body.phone_number_id.strip()
    )
    connected = await signup.connect_manual(
        session,
        service.crypto_for(settings),
        service.make_graph(settings),
        corpus_id=corpus.id,
        tenant_id=corpus.tenant_id,
        phone_number_id=body.phone_number_id,
        waba_id=body.waba_id,
        access_token=body.access_token,
        meta_billing=body.meta_billing,
        created_by=_created_by(request),
        pin=body.pin,
    )
    await _audit_connected(session, request, corpus, connected, token_only=same_number)
    await session.commit()
    return await _out(session, request, corpus, connected.connection)


@router.patch("", response_model=WhatsAppOut)
async def update_settings(
    body: WhatsAppSettingsPatch,
    request: Request,
    _owner: None = Depends(require_owner),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WhatsAppOut:
    """Change the number's settings: switch it off or on (`enabled`), how long the assistant stays
    quiet after you reply (`human_pause_hours`, 1 to 72), and the opt-in follow-up (a template
    Meta has approved, its language and the delay). Only the fields sent change."""
    refuse_system_write(request, corpus)
    connection = await service.require_connection(session, corpus.id)
    await service.require_plan(session, corpus.tenant_id)
    changed = service.apply_settings_patch(connection, body.model_dump(exclude_unset=True))
    if changed:
        await record_audit(
            session,
            request,
            "whatsapp.settings_updated",
            tenant_id=corpus.tenant_id,
            target_type="whatsapp_connection",
            target_id=str(connection.id),
            details={"corpus_id": str(corpus.id), "changed": changed},
        )
    await session.commit()
    return await _out(session, request, corpus, connection)


@router.put("/documents", response_model=WhatsAppOut)
async def set_documents(
    body: DocumentsRequest,
    request: Request,
    _owner: None = Depends(require_owner),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> WhatsAppOut:
    """Choose which documents customers may be sent on WhatsApp (at most 20; PDF, JPG or PNG of
    this knowledge base: 422 `whatsapp_document_invalid`). Replaces the whole list."""
    refuse_system_write(request, corpus)
    connection = await service.require_connection(session, corpus.id)
    await service.require_plan(session, corpus.tenant_id)
    ids = await documents.validate_marked(session, corpus.id, body.document_ids)
    stored = dict(connection.settings or {})
    wanted = [str(i) for i in ids]
    if [str(i) for i in stored.get("shareable_document_ids") or []] != wanted:
        stored["shareable_document_ids"] = wanted
        connection.settings = stored
        await record_audit(
            session,
            request,
            "whatsapp.settings_updated",
            tenant_id=corpus.tenant_id,
            target_type="whatsapp_connection",
            target_id=str(connection.id),
            details={"corpus_id": str(corpus.id), "changed": ["shareable_documents"]},
        )
    await session.commit()
    return await _out(session, request, corpus, connection)


@router.delete("", status_code=204)
async def disconnect_number(
    request: Request,
    _owner: None = Depends(require_owner),
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Disconnect the number: its token is deleted and Meta stops sending its messages here
    (best effort). The chats stay readable."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    connection = await service.require_connection(session, corpus.id)
    await signup.disconnect(
        session, service.crypto_for(settings), service.make_graph(settings), connection
    )
    await record_audit(
        session,
        request,
        "whatsapp.disconnected",
        tenant_id=corpus.tenant_id,
        target_type="whatsapp_connection",
        target_id=str(connection.id),
        details={"corpus_id": str(corpus.id)},
    )
    await session.commit()
    return Response(status_code=204)


@router.post("/check", response_model=CheckOut)
async def check_connection(
    request: Request,
    corpus: Corpus = Depends(path_corpus),
    session: AsyncSession = Depends(get_session),
) -> CheckOut:
    """Ask Meta about the number with the saved token: is it reachable, what is it called, how
    healthy is its quality rating. A token Meta refuses puts the number in `error`."""
    refuse_system_write(request, corpus)
    settings = request.app.state.settings
    service.require_engine(settings)
    await service.require_plan(session, corpus.tenant_id)
    connection = await service.require_connection(session, corpus.id)
    result = await service.check_number(
        session, service.crypto_for(settings), service.make_graph(settings), connection
    )
    await session.commit()
    return CheckOut(**result)


# ------------------------------------------------------------------ the deployer's view

admin_router = APIRouter(
    prefix="/api/v1/admin/whatsapp", tags=["whatsapp"], dependencies=[Depends(require_admin)]
)


@admin_router.get("/connections", response_model=ConnectionsResponse)
async def list_connections(session: AsyncSession = Depends(get_session)) -> ConnectionsResponse:
    """Every WhatsApp number on the engine: whose it is, how it was set up, who pays Meta, when
    a customer last wrote, and its last error."""
    rows = (
        await session.execute(
            select(ChannelConnection, Corpus.name, Tenant.name)
            .join(Corpus, Corpus.id == ChannelConnection.corpus_id)
            .join(Tenant, Tenant.id == ChannelConnection.tenant_id)
            .order_by(Tenant.name, Corpus.name)
        )
    ).all()
    items = [
        ConnectionRow(
            corpus_id=c.corpus_id,
            corpus_name=corpus_name,
            tenant_id=c.tenant_id,
            business=tenant_name,
            display_phone=c.display_phone,
            status=c.status,
            setup=c.setup,
            meta_billing=c.meta_billing,
            last_inbound_at=c.last_inbound_at,
            last_error_code=c.last_error_code,
        )
        for c, corpus_name, tenant_name in rows
        if not signup.is_disconnected(c)
    ]
    return ConnectionsResponse(items=items, total=len(items))
