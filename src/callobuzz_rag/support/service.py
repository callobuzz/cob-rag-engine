"""The rules of a support request (TV3): who may open one, the daily limit, how a reply moves
the status, and what counts as the provider's first response.

Statuses: `open` waits for the provider, `answered` means the provider replied, `waiting` means
the provider asked the business something, `closed` is done (a business's follow-up reopens
it). `first_response_at` is the first message by the provider and never moves again.
Nothing here commits; the routes do.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.db.models.support import SUPPORT_NUMBER_SEQ, SupportMessage, SupportRequest
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.support.context import build_context

DAILY_LIMIT: Final = 10  # new requests per workspace per rolling day
WINDOW: Final = timedelta(hours=24)
MESSAGE_LIMIT: Final = 20  # follow-up messages per workspace per rolling hour
MESSAGE_WINDOW: Final = timedelta(hours=1)


def not_found() -> APIError:
    return APIError(404, "That support request does not exist.", "support_request_not_found")


def disabled() -> APIError:
    return APIError(
        409,
        "Your provider has switched support requests off. Use the contact details they gave you.",
        "support_disabled",
    )


async def next_number(session: AsyncSession) -> int:
    """The next request number (`#1042`), from the engine-wide sequence."""
    return int((await session.execute(select(SUPPORT_NUMBER_SEQ.next_value()))).scalar_one())


async def check_daily_limit(session: AsyncSession, tenant_id: uuid.UUID, now: datetime) -> None:
    """Refuse an eleventh new request inside 24 hours.

    Raises:
        APIError 429 `support_rate_limited` with `Retry-After` (when the oldest of the ten
        leaves the window).
    """
    recent = sorted(
        (
            await session.execute(
                select(SupportRequest.created_at).where(
                    SupportRequest.tenant_id == tenant_id,
                    SupportRequest.created_at >= now - WINDOW,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(recent) >= DAILY_LIMIT:
        wait = max(1, int((recent[0] + WINDOW - now).total_seconds()))
        raise APIError(
            429,
            "You have sent the most requests allowed in a day. Add to an open request, or "
            "try again later.",
            "support_rate_limited",
            headers={"Retry-After": str(wait)},
        )


async def check_message_limit(session: AsyncSession, tenant_id: uuid.UUID, now: datetime) -> None:
    """Refuse a twenty-first message from one workspace inside an hour: every follow-up is a mail
    to the provider's inbox, so an endless loop must not flood it.

    Raises:
        APIError 429 `support_rate_limited` with `Retry-After` (when the oldest of the twenty
        leaves the window).
    """
    recent = sorted(
        (
            await session.execute(
                select(SupportMessage.created_at)
                .join(SupportRequest, SupportRequest.id == SupportMessage.request_id)
                .where(
                    SupportRequest.tenant_id == tenant_id,
                    SupportMessage.author_kind == "business",
                    SupportMessage.created_at >= now - MESSAGE_WINDOW,
                )
            )
        )
        .scalars()
        .all()
    )
    if len(recent) >= MESSAGE_LIMIT:
        wait = max(1, int((recent[0] + MESSAGE_WINDOW - now).total_seconds()))
        raise APIError(
            429,
            "You have sent a lot of messages in the last hour. Wait a little, the team has "
            "your earlier ones.",
            "support_rate_limited",
            headers={"Retry-After": str(wait)},
        )


async def create_request(
    session: AsyncSession,
    tenant: Tenant,
    *,
    subject: str,
    message: str,
    page: str | None,
    client_errors: list[str],
    author_email: str,
    author_user_id: uuid.UUID | None,
    now: datetime | None = None,
) -> tuple[SupportRequest, SupportMessage]:
    """Open a request with its first message and the workspace information attached."""
    moment = now or datetime.now(UTC)
    # Serialise this workspace's new requests: the count and the insert are one step, so
    # parallel requests cannot all read nine and pass the daily limit.
    await session.execute(select(Tenant.id).where(Tenant.id == tenant.id).with_for_update())
    await check_daily_limit(session, tenant.id, moment)
    row = SupportRequest(
        tenant_id=tenant.id,
        number=await next_number(session),
        subject=subject.strip(),
        status="open",
        page=page,
        context=await build_context(session, tenant, page=page, client_errors=client_errors),
        created_by_user_id=author_user_id,
        created_by_email=author_email[:320],
        last_message_at=moment,
        created_at=moment,
    )
    session.add(row)
    await session.flush()
    first = SupportMessage(
        request_id=row.id,
        author_kind="business",
        author_email=author_email[:320],
        body=message,
        created_at=moment,
    )
    session.add(first)
    await session.flush()
    return row, first


async def get_request(
    session: AsyncSession, request_id: uuid.UUID, *, tenant_id: uuid.UUID | None = None
) -> SupportRequest:
    """One request. With `tenant_id`, only that workspace's: another's is the same 404 as a
    request that does not exist.

    Raises:
        APIError 404 `support_request_not_found`.
    """
    row = (
        await session.execute(select(SupportRequest).where(SupportRequest.id == request_id))
    ).scalar_one_or_none()
    if row is None or (tenant_id is not None and row.tenant_id != tenant_id):
        raise not_found()
    return row


async def messages_of(session: AsyncSession, request_id: uuid.UUID) -> list[SupportMessage]:
    rows = (
        (
            await session.execute(
                select(SupportMessage).where(SupportMessage.request_id == request_id)
            )
        )
        .scalars()
        .all()
    )
    return sorted(rows, key=lambda m: m.created_at)


def _apply_status(row: SupportRequest, status: str, now: datetime) -> str | None:
    """Move `row` to `status`; returns the old status when it changed, else None."""
    old = row.status
    row.status = status
    row.closed_at = now if status == "closed" else None
    return old if old != status else None


async def add_business_message(
    session: AsyncSession,
    row: SupportRequest,
    *,
    body: str,
    author_email: str,
    now: datetime | None = None,
) -> tuple[SupportMessage, str | None]:
    """A follow-up from the business: the request is open again (a closed one is reopened).
    The first response time does not move. Returns the message and the old status when it
    changed.

    Raises:
        APIError 429 `support_rate_limited` (twenty messages from the workspace in an hour).
    """
    moment = now or datetime.now(UTC)
    # Serialise this workspace's messages: the count and the insert are one step
    await session.execute(select(Tenant.id).where(Tenant.id == row.tenant_id).with_for_update())
    await check_message_limit(session, row.tenant_id, moment)
    message = SupportMessage(
        request_id=row.id,
        author_kind="business",
        author_email=author_email[:320],
        body=body,
        created_at=moment,
    )
    session.add(message)
    row.last_message_at = moment
    changed = _apply_status(row, "open", moment)
    await session.flush()
    return message, changed


async def add_deployer_message(
    session: AsyncSession,
    row: SupportRequest,
    *,
    body: str,
    author_email: str,
    status: str | None,
    now: datetime | None = None,
) -> tuple[SupportMessage, str | None]:
    """The provider's reply: the first one sets `first_response_at`; the request becomes
    `answered` unless the reply says `waiting` (a question back) or `closed`."""
    moment = now or datetime.now(UTC)
    message = SupportMessage(
        request_id=row.id,
        author_kind="deployer",
        author_email=author_email[:320],
        body=body,
        created_at=moment,
    )
    session.add(message)
    row.last_message_at = moment
    if row.first_response_at is None:
        row.first_response_at = moment
    changed = _apply_status(row, status or "answered", moment)
    await session.flush()
    return message, changed


async def set_status(
    session: AsyncSession, row: SupportRequest, status: str, now: datetime | None = None
) -> str | None:
    """Set the status (the provider's `PATCH`, or the business closing its own request).
    Returns the old status when it changed."""
    changed = _apply_status(row, status, now or datetime.now(UTC))
    await session.flush()
    return changed


async def list_requests(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None = None,
    status: str | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[SupportRequest], int]:
    """Requests newest activity first, optionally of one workspace, one status, or matching `q`
    (a number such as `#1042` or `1042`, or words of the subject or the workspace's name)."""
    conditions: list[Any] = []
    if tenant_id is not None:
        conditions.append(SupportRequest.tenant_id == tenant_id)
    if status is not None:
        conditions.append(SupportRequest.status == status)
    if q and q.strip().lstrip("#"):
        needle = q.strip().lstrip("#")
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        by_name = (
            (
                await session.execute(
                    select(Tenant.id).where(Tenant.name.ilike(pattern, escape="\\"))
                )
            )
            .scalars()
            .all()
        )
        matches: list[Any] = [SupportRequest.subject.ilike(pattern, escape="\\")]
        if by_name:
            matches.append(SupportRequest.tenant_id.in_(list(by_name)))
        if needle.isdigit() and len(needle) <= 9:
            matches.append(SupportRequest.number == int(needle))
        conditions.append(or_(*matches))
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(SupportRequest).where(*conditions)
            )
        ).scalar_one()
    )
    rows = list(
        (
            await session.execute(
                select(SupportRequest)
                .where(*conditions)
                .order_by(SupportRequest.last_message_at.desc(), SupportRequest.number.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows, total


async def open_count(session: AsyncSession, tenant_id: uuid.UUID) -> int:
    """This workspace's requests that are not closed."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(SupportRequest)
                .where(SupportRequest.tenant_id == tenant_id, SupportRequest.status != "closed")
            )
        ).scalar_one()
    )


async def workspace_names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    out: dict[uuid.UUID, Any] = {}
    for tenant_id in ids:
        tenant = await session.get(Tenant, tenant_id)
        if tenant is not None:
            out[tenant_id] = tenant.name
    return {k: str(v) for k, v in out.items()}
