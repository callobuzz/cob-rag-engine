"""Erasing a visitor's chat also erases their question from the frozen value reports (TV4).

A closed month's report is frozen, with the questions people asked most and the ones the
assistant could not answer, as verbatim masked text (`value_reports.data.inputs`). When a lead or
a WhatsApp chat is erased its assistant turns are deleted; `delete_assistant_turns` is the one
place that does it, and before deleting it takes those turns' questions out of the frozen reports
of the month each was asked in, in the same transaction: a count drops by one per erased turn and
an entry that reaches zero goes. Every erasure path calls it instead of deleting the turns itself.

The turns counted are the ones `business/value.py` counts (a visitor channel, not a preview): a
question counts as asked when the turn has a question label, as unanswered when it has
`no_answer`.

What it cannot reach: an e-mail already sent, and a report older than the retention (deleted by
the daily clean-up, `value_report.RETENTION_MONTHS`).
"""

import copy
import re
import uuid
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from callobuzz_rag.business.value import QUESTION_LABELS, VISITOR_CHANNELS, month_of
from callobuzz_rag.business.value_report import workspace_context
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.value import ValueReport

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_SPACES = re.compile(r"\s+")


def normalise_question(text: str) -> str:
    """The key a report groups a question by (the same as `business/value.py`'s SQL: trimmed,
    lower-cased, runs of white space collapsed)."""
    return _SPACES.sub(" ", text.strip(" ").lower())


def _forget(entries: Any, erased: Counter[str]) -> tuple[list[Any], bool]:
    """`entries` (`[{question, count}]`) with `erased` taken off; whether anything changed."""
    kept: list[Any] = []
    changed = False
    for entry in entries if isinstance(entries, list) else []:
        key = normalise_question(str(entry.get("question", ""))) if isinstance(entry, dict) else ""
        gone = erased.get(key, 0)
        if not gone:
            kept.append(entry)
            continue
        changed = True
        left = int(entry.get("count", 0)) - gone
        if left > 0:
            kept.append({**entry, "count": left})
    return kept, changed


async def forget_turns(session: "AsyncSession", turn_filter: Any) -> None:
    """Take the questions of the turns matching `turn_filter` out of the frozen reports."""
    rows = (
        await session.execute(
            select(
                AssistantTurn.tenant_id,
                AssistantTurn.question_masked,
                AssistantTurn.label,
                AssistantTurn.no_answer,
                AssistantTurn.is_preview,
                AssistantTurn.channel,
                AssistantTurn.created_at,
            ).where(turn_filter)
        )
    ).all()
    asked: dict[uuid.UUID, list[tuple[Any, str, bool, bool]]] = {}
    for tenant_id, question, label, no_answer, preview, channel, created_at in rows:
        if preview or channel not in VISITOR_CHANNELS:
            continue
        key = normalise_question(question or "")
        if key:
            asked.setdefault(tenant_id, []).append(
                (created_at, key, label in QUESTION_LABELS, no_answer)
            )
    for tenant_id, turns in asked.items():
        timezone = (await workspace_context(session, tenant_id)).timezone
        by_month: dict[date, tuple[Counter[str], Counter[str]]] = {}
        for created_at, key, counted, unanswered in turns:
            top, missed = by_month.setdefault(
                month_of(created_at, timezone), (Counter(), Counter())
            )
            if counted:
                top[key] += 1
            if unanswered:
                missed[key] += 1
        reports = (
            (await session.execute(select(ValueReport).where(ValueReport.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        for report in reports:
            if report.month not in by_month:
                continue
            top, missed = by_month[report.month]
            data = copy.deepcopy(report.data)
            inputs = data.get("inputs")
            if not isinstance(inputs, dict):
                continue
            inputs["top_questions"], a = _forget(inputs.get("top_questions"), top)
            inputs["unanswered"], b = _forget(inputs.get("unanswered"), missed)
            if a or b:
                report.data = data


async def delete_assistant_turns(session: "AsyncSession", turn_filter: Any) -> None:
    """Delete the assistant turns matching `turn_filter`, forgetting their questions in the
    frozen value reports first (same transaction)."""
    await forget_turns(session, turn_filter)
    await session.execute(delete(AssistantTurn).where(turn_filter))
