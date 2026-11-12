"""COB's proposals: a card the person must confirm before anything changes.

`create_proposal` stores what COB proposes. `confirm` is the only way an action tool ever runs:

1. the row is claimed with one `UPDATE ... WHERE status = 'pending' AND expires_at > now`, so a
   card runs at most once however many times (or from how many tabs) it is confirmed;
2. the person, workspace and role are checked again as they are now, not as they were;
3. a destructive card needs the resource's current name typed exactly;
4. the tool calls the real route with the caller's own token (the route applies its own
   permission checks, caps and audit), and its answer, good or bad, is reported on the card.

A confirmed action writes `cob.action` next to the route's own audit row, and an assistant
message into the conversation. A rotated key's secret is returned in the response and goes
nowhere else: not the row, not the audit, not the message, not a log.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Final, NoReturn

from sqlalchemy import select, update

from callobuzz_rag.accounts.audit import record_audit
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.cob import (
    CobActionCardBlock,
    CobActionResult,
    CobArg,
    CobCancelResult,
    CobLinkBlock,
)
from callobuzz_rag.cob.tools.actions.common import ActionBase, ActionOutcome, ProposalDraft
from callobuzz_rag.cob.tools.base import ToolContext
from callobuzz_rag.cob.tools.cards import esc, ts
from callobuzz_rag.cob.tools.http import ToolFailure, tool_client
from callobuzz_rag.cob.tools.registry import REGISTRY, tools_for
from callobuzz_rag.cob.types import CobCaller
from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message

logger = logging.getLogger(__name__)

TTL_SECONDS: Final = 600
LANG_KEY: Final = "_lang"  # the language of the card, kept with the ids so the result reads alike

P = CobActionProposal


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


# ------------------------------------------------------------------------ create


async def create_proposal(
    session: Any,
    caller: CobCaller,
    conversation_id: uuid.UUID,
    tool_name: str,
    draft: ProposalDraft,
    *,
    language: str = "en",
    now: datetime | None = None,
) -> CobActionProposal:
    """Store a proposal for `caller` (the caller commits). Expires `TTL_SECONDS` from now."""
    assert caller.user_id is not None
    made = _now(now)
    row = CobActionProposal(
        id=uuid.uuid4(),
        user_id=caller.user_id,
        tenant_id=caller.tenant_id,
        conversation_id=conversation_id,
        session_id=caller.session_id,
        tool=tool_name,
        args={**draft.exec_args, LANG_KEY: language},
        summary=draft.summary,
        risk=draft.risk,
        confirm_name=draft.confirm_name,
        status="pending",
        created_at=made,
        expires_at=made + timedelta(seconds=TTL_SECONDS),
    )
    session.add(row)
    await session.flush()
    return row


def card_for(row: CobActionProposal, draft: ProposalDraft, tool: ActionBase) -> CobActionCardBlock:
    return CobActionCardBlock(
        proposal_id=row.id,
        tool=row.tool,
        title=draft.title,
        summary=draft.summary,
        args=[CobArg(label=label, value=value) for label, value in draft.args],
        risk=draft.risk,
        destructive=tool.destructive,
        confirm_name=draft.confirm_name,
        expires_at=_aware(row.expires_at),
    )


async def card_statuses(
    session: Any, ids: list[uuid.UUID], *, now: datetime | None = None
) -> dict[uuid.UUID, str]:
    """The state of cards read back from history: the proposal's status, `expired` once a
    pending card is past its time."""
    if not ids:
        return {}
    rows = (await session.execute(select(P).where(P.id.in_(ids)))).scalars().all()
    moment = _now(now)
    out: dict[uuid.UUID, str] = {}
    for row in rows:
        status = row.status
        if status in ("pending", "executing") and _aware(row.expires_at) <= moment:
            status = "expired"
        out[row.id] = "pending" if status == "executing" else status
    return out


# ----------------------------------------------------------------------- confirm


async def _diagnose(
    session: Any, caller: CobCaller, proposal_id: uuid.UUID, moment: datetime
) -> NoReturn:
    """Why a claim found nothing: unknown or someone else's, used, or too old."""
    row = await session.get(P, proposal_id)
    if row is None or row.user_id != caller.user_id:
        raise APIError(404, "I can't find that card.", "cob_proposal_not_found")
    if row.status == "pending" and _aware(row.expires_at) <= moment:
        row.status = "expired"
        await session.commit()
        raise APIError(410, "This card expired. Ask me again.", "cob_proposal_expired")
    if row.status == "expired":
        raise APIError(410, "This card expired. Ask me again.", "cob_proposal_expired")
    raise APIError(409, "This card was already used.", "cob_proposal_used")


async def _fail(
    session: Any, row: CobActionProposal, status: int, code: str, message: str
) -> APIError:
    row.status = "failed"
    row.result = {"ok": False, "status_code": status, "code": code, "message": message}
    await session.commit()
    return APIError(status, message, code)


async def confirm(
    session: Any,
    request: Any,
    caller: CobCaller,
    proposal_id: uuid.UUID,
    confirm_name: str | None,
    *,
    client: Any | None = None,
    now: datetime | None = None,
) -> CobActionResult:
    """Run a confirmed card. Raises `APIError` for a card that cannot run (not found, used,
    expired, the person's scope changed, a wrong typed name); a route that refuses is not an
    error here, it is a `failed` result with the route's own status and code."""
    moment = _now(now)
    claimed = await session.execute(
        update(P)
        .where(
            P.id == proposal_id,
            P.user_id == caller.user_id,
            P.status == "pending",
            P.expires_at > moment,
        )
        .values(status="executing", used_at=moment)
        .returning(P.id)
    )
    if claimed.first() is None:
        await _diagnose(session, caller, proposal_id, moment)
    await session.commit()  # the claim is visible before anything runs
    row = await session.get(P, proposal_id)
    await session.refresh(row)

    tool = REGISTRY.get(row.tool)
    allowed = tool is not None and tool in tools_for(caller.role)
    if row.tenant_id != caller.tenant_id or not allowed or not isinstance(tool, ActionBase):
        raise await _fail(
            session,
            row,
            409,
            "cob_proposal_scope_changed",
            "Your workspace or role changed since this card was made. Ask me again.",
        )

    stored = dict(row.args)
    language = str(stored.pop(LANG_KEY, "en"))
    try:
        exec_args = tool.exec_model.model_validate(stored).model_dump(mode="json")
    except ValueError:
        raise await _fail(
            session, row, 422, "cob_proposal_invalid", "This card is no longer valid. Ask me again."
        ) from None

    if client is None:
        from callobuzz_rag.cob import orchestrator

        client = orchestrator.make_internal_client(request, caller)
    ctx = ToolContext(
        caller=caller,
        client=tool_client(tool, client),
        language=language,
        page=None,
        now=moment,
    )

    if tool.destructive:
        try:
            current = await tool.current_name(ctx, exec_args)
        except ToolFailure as failure:
            status, code, message = _read_failure(failure.kind, row, language)
            outcome = ActionOutcome(
                method=tool.primary.method,
                path=tool.primary.path,
                status=status,
                ok=False,
                message=message,
                code=code,
            )
            return await _finish(session, request, caller, row, tool, outcome, language, moment)
        if (confirm_name or "").strip() != current.strip():
            row.status = "pending"
            row.used_at = None
            await session.commit()
            raise APIError(
                422,
                "The name you typed doesn't match. Type it exactly as shown.",
                "confirm_mismatch",
            )
        exec_args["confirm_name"] = current

    try:
        outcome = await tool.execute(ctx, exec_args)
    except Exception:
        logger.warning("COB action %s failed", tool.name, exc_info=True)
        outcome = ActionOutcome(
            method=tool.primary.method,
            path=tool.primary.path,
            status=500,
            ok=False,
            message=ts("action_failed_generic", language),
            code="cob_action_failed",
        )
    return await _finish(session, request, caller, row, tool, outcome, language, moment)


def _read_failure(kind: str, row: CobActionProposal, language: str) -> tuple[int, str, str]:
    """Status, code and sentence for a failed read of the resource's current name, so the card
    says what the route said rather than "not found" for everything."""
    if kind == "owner":
        return 403, "owner_required", ts("action_owner_only", language)
    if kind == "forbidden":
        return 403, "forbidden", ts("forbidden", language)
    if kind == "not_found":
        name = f"**{esc(row.confirm_name or '', 60)}**"
        return 404, "not_found", ts("not_found", language, name=name)
    return 503, "unavailable", ts("unavailable", language)


async def _after_last_message(
    session: Any, conversation_id: uuid.UUID, moment: datetime
) -> datetime:
    """`moment`, or one microsecond past the conversation's newest message when that is later."""
    newest = (
        await session.execute(
            select(Message.created_at)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if newest is None:
        return moment
    floor = _aware(newest) + timedelta(microseconds=1)
    return max(_aware(moment), floor)


async def _finish(
    session: Any,
    request: Any,
    caller: CobCaller,
    row: CobActionProposal,
    tool: ActionBase,
    outcome: ActionOutcome,
    language: str,
    moment: datetime,
) -> CobActionResult:
    """One transaction: the row's final state (without the secret), `cob.action`, and the
    assistant message that tells the conversation what happened."""
    status = "done" if outcome.ok else "failed"
    row.status = status
    row.result = {
        "ok": outcome.ok,
        "status_code": outcome.status,
        "code": outcome.code,
        "message": outcome.message,
        **outcome.result,
    }
    await record_audit(
        session,
        request,
        "cob.action",
        tenant_id=caller.tenant_id,
        target_type="cob_proposal",
        target_id=str(row.id),
        details={
            "tool": tool.name,
            "method": outcome.method,
            "path": outcome.path,
            "proposal_id": str(row.id),
            "status_code": outcome.status,
            "ok": outcome.ok,
        },
    )
    prefix = ts("action_done" if outcome.ok else "action_didnt_work", language)
    ui = [outcome.link.model_dump(mode="json")] if outcome.link is not None else []
    conversation = await session.get(Conversation, row.conversation_id)
    if conversation is not None:
        # History is ordered by `created_at`, and a clock only ticks so often: a confirm that
        # lands in the same tick as the turn that proposed it would sort before that turn's
        # reply (stored a microsecond after the question). The ending always reads last.
        stamp = await _after_last_message(session, conversation.id, moment)
        session.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content=f"{prefix} {outcome.message}",
                token_count=0,
                created_at=stamp,
                agent_log={
                    "cob": {
                        "intent": "action",
                        "tool": tool.name,
                        "proposal_id": str(row.id),
                        "ok": outcome.ok,
                        "ui": ui,
                    }
                },
            )
        )
        conversation.message_count = (conversation.message_count or 0) + 1
        conversation.updated_at = stamp
    await session.commit()
    return CobActionResult(
        proposal_id=row.id,
        status=status,
        message=outcome.message,
        link=outcome.link if isinstance(outcome.link, CobLinkBlock) else None,
        secret=outcome.secret,
        http_status=outcome.status,
        code=outcome.code,
    )


# ------------------------------------------------------------------------ cancel


async def cancel(
    session: Any, caller: CobCaller, proposal_id: uuid.UUID, *, now: datetime | None = None
) -> CobCancelResult:
    moment = _now(now)
    done = await session.execute(
        update(P)
        .where(
            P.id == proposal_id,
            P.user_id == caller.user_id,
            P.status == "pending",
            P.expires_at > moment,
        )
        .values(status="cancelled", used_at=moment)
        .returning(P.id)
    )
    if done.first() is None:
        await _diagnose(session, caller, proposal_id, moment)
    await session.commit()
    return CobCancelResult(proposal_id=proposal_id)
