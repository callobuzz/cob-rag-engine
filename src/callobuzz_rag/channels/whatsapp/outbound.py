"""Sending on WhatsApp (TV6): one place that talks to the Graph client for a chat.

Every send records itself: the message id goes into `channel_events` (`kind='other'`, already
`done`), so when the owner's WhatsApp Business app echoes a message the bot itself sent, the
unique key makes the echo a no-op instead of a "the owner stepped in" pause; the chat's
`last_outbound_at` moves; and the pass-through fee for the message (when one applies) is charged
in the same transaction (`fees.charge_send`).

A send that Meta may or may not have accepted (`GraphError.maybe_sent`) is never repeated here.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from callobuzz_rag.channels.whatsapp.graph import GraphClient, GraphError
from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.channel import ChannelEvent, ChannelThread

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

Category = Literal["service", "utility", "marketing", "authentication"]
AfterSend = Callable[["AsyncSession", "SendContext", str, Category], Awaitable[None]]


class SendError(RAGKernelError):
    """A message did not go out. `code` is what the owner and the logs see; never the token."""

    def __init__(
        self,
        code: str,
        *,
        meta_code: int | None = None,
        maybe_sent: bool = False,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.meta_code = meta_code
        self.maybe_sent = maybe_sent
        self.status = status
        # A throttle: nothing went out, so the same message can be sent again later.
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def window_closed(self) -> bool:
        return self.code == "window_closed"


@dataclass
class SendContext:
    """What a send to one customer on one number needs."""

    graph: GraphClient
    session_factory: Callable[[], "AsyncSession"]
    connection_id: uuid.UUID
    corpus_id: uuid.UUID
    tenant_id: uuid.UUID
    phone_number_id: str
    token: str
    thread_id: uuid.UUID
    to: str  # digits only, as the Cloud API takes it
    meta_billing: str
    after_send: AfterSend | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)


def _failure(exc: GraphError) -> SendError:
    if exc.window_closed:
        code = "window_closed"
    elif exc.status == 401 or exc.meta_code == 190:
        code = "token_invalid"
    elif exc.retryable:
        code = "rate_limited"
    elif exc.maybe_sent:
        code = "maybe_sent"
    else:
        code = "send_failed"
    return SendError(
        code,
        meta_code=exc.meta_code,
        maybe_sent=exc.maybe_sent,
        status=exc.status,
        retryable=exc.retryable,
        retry_after=exc.retry_after,
    )


async def _record(ctx: SendContext, wamid: str, category: Category) -> None:
    """Note the sent message: its id as a finished event, the chat's last send, its fee."""
    if not wamid:
        return
    now = ctx.clock()
    try:
        async with ctx.session_factory() as session:
            await session.execute(
                pg_insert(ChannelEvent)
                .values(
                    connection_id=ctx.connection_id,
                    corpus_id=ctx.corpus_id,
                    external_id=wamid[:128],
                    kind="other",
                    thread_id=ctx.thread_id,
                    payload_encrypted="",
                    status="done",
                    received_at=now,
                    processed_at=now,
                )
                .on_conflict_do_nothing(index_elements=["connection_id", "external_id"])
            )
            await session.execute(
                update(ChannelThread)
                .where(ChannelThread.id == ctx.thread_id)
                .values(last_outbound_at=now)
            )
            if ctx.after_send is not None:
                await ctx.after_send(session, ctx, wamid, category)
            await session.commit()
    except Exception:
        # The message is out; failing to note it must not look like a failed send.
        logger.warning("Could not record the WhatsApp message %s", wamid, exc_info=True)


async def _run(ctx: SendContext, category: Category, call: Awaitable[str]) -> str:
    try:
        wamid = await call
    except GraphError as exc:
        raise _failure(exc) from exc
    await _record(ctx, wamid, category)
    return wamid


async def send_text(ctx: SendContext, text: str, *, category: Category = "service") -> str:
    return await _run(
        ctx,
        category,
        ctx.graph.send_text(ctx.phone_number_id, token=ctx.token, to=ctx.to, text=text),
    )


async def send_buttons(
    ctx: SendContext,
    body: str,
    buttons: list[tuple[str, str]],
    *,
    category: Category = "service",
) -> str:
    return await _run(
        ctx,
        category,
        ctx.graph.send_buttons(
            ctx.phone_number_id, token=ctx.token, to=ctx.to, body=body, buttons=buttons
        ),
    )


async def send_document(
    ctx: SendContext,
    *,
    media_id: str,
    filename: str,
    caption: str = "",
    category: Category = "service",
) -> str:
    return await _run(
        ctx,
        category,
        ctx.graph.send_document(
            ctx.phone_number_id,
            token=ctx.token,
            to=ctx.to,
            media_id=media_id,
            filename=filename,
            caption=caption,
        ),
    )


async def send_template(
    ctx: SendContext,
    *,
    name: str,
    language: str,
    params: list[str],
    category: Category = "utility",
) -> str:
    return await _run(
        ctx,
        category,
        ctx.graph.send_template(
            ctx.phone_number_id,
            token=ctx.token,
            to=ctx.to,
            name=name,
            language=language,
            params=params,
        ),
    )


async def send_rendered(ctx: SendContext, rendered: Any) -> list[str]:
    """The messages of `render.Rendered`: its texts, then its buttons. Stops at the first
    failure (the caller decides what to do); what already went out stays sent."""
    sent: list[str] = []
    for text in rendered.texts:
        sent.append(await send_text(ctx, text))
    if rendered.buttons:
        sent.append(await send_buttons(ctx, rendered.button_body, rendered.buttons))
    return sent
