"""The audit trail, read by the operator (accounts A8).

`GET /api/v1/admin/audit` (admin key or super-admin sign-in): the rows `record_audit`
wrote (accounts/audit.py), newest first, filtered by tenant, action (exact, or a catalog
prefix ending in `.` such as `tenant.`), actor (substring of the label) and a time range
of at most 366 days (default: the last 90). Rows are kept indefinitely and there is no
delete API: the trail is the operator's record. Tenants have no view of it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import AUDIT_ACTIONS, RETIRED_AUDIT_ACTIONS
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.db.models.audit_log import AuditLog

router = APIRouter(
    prefix="/api/v1/admin/audit", tags=["audit"], dependencies=[Depends(require_admin)]
)

DEFAULT_WINDOW = timedelta(days=90)
MAX_WINDOW = timedelta(days=366)
_KNOWN_ACTIONS = AUDIT_ACTIONS | RETIRED_AUDIT_ACTIONS
ACTION_PREFIXES = frozenset(a[: i + 1] for a in _KNOWN_ACTIONS for i, c in enumerate(a) if c == ".")


class AuditEntry(BaseModel):
    id: UUID
    created_at: datetime
    actor_kind: str
    actor_user_id: UUID | None
    actor_label: str
    action: str
    tenant_id: UUID | None
    tenant_label: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    ip: str | None


class AuditListResponse(BaseModel):
    items: list[AuditEntry]
    total: int
    offset: int
    limit: int


def _utc(value: datetime) -> datetime:
    """A datetime given without an offset is UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _time_range(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """The [start, end] window: the 90 days up to `end` (default now) when `start` is
    not given, 90 days from `start` when only it is given (never past now).

    Raises:
        HTTPException(422): start after end, or a window longer than 366 days.
    """
    now = datetime.now(UTC)
    if start is not None and end is None:
        start = _utc(start)
        end = min(start + DEFAULT_WINDOW, now) if start < now else start
    else:
        end = _utc(end) if end is not None else now
        start = _utc(start) if start is not None else end - DEFAULT_WINDOW
    if start > end:
        raise HTTPException(status_code=422, detail="start must not be after end")
    if end - start > MAX_WINDOW:
        raise HTTPException(status_code=422, detail="The time range is at most 366 days")
    return start, end


def _action_filter(action: str) -> ColumnElement[bool]:
    """Exact action, or every action under a prefix ending in `.`.

    Raises:
        HTTPException(422): neither a catalog action nor a catalog prefix.
    """
    if action in _KNOWN_ACTIONS:
        return AuditLog.action == action
    if action in ACTION_PREFIXES:
        return AuditLog.action.startswith(action, autoescape=True)
    raise HTTPException(
        status_code=422,
        detail=f"Unknown audit action {action!r}: use a catalog action or a prefix such as "
        "'tenant.'",
    )


@router.get("", response_model=AuditListResponse)
async def list_audit(
    tenant_id: UUID | None = Query(None, description="Only rows about this tenant"),
    action: str | None = Query(
        None, max_length=64, description="An action, or a prefix ending in '.' (e.g. 'tenant.')"
    ),
    actor: str | None = Query(
        None, min_length=1, max_length=320, description="Case-insensitive part of the actor"
    ),
    start: datetime | None = Query(None, description="ISO datetime (UTC when no offset)"),
    end: datetime | None = Query(None, description="ISO datetime (UTC when no offset)"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> AuditListResponse:
    """Audit rows, newest first. Without `start`/`end`: the last 90 days; a range may
    span at most 366 days."""
    since, until = _time_range(start, end)
    where: list[ColumnElement[bool]] = [
        AuditLog.created_at >= since,
        AuditLog.created_at <= until,
    ]
    if tenant_id is not None:
        where.append(AuditLog.tenant_id == tenant_id)
    if action is not None:
        where.append(_action_filter(action))
    if actor is not None:
        where.append(AuditLog.actor_label.icontains(actor, autoescape=True))

    total = (
        await session.execute(select(func.count()).select_from(AuditLog).where(*where))
    ).scalar_one()
    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(*where)
                .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
                .offset(offset)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return AuditListResponse(
        items=[AuditEntry.model_validate(row, from_attributes=True) for row in rows],
        total=int(total),
        offset=offset,
        limit=limit,
    )
