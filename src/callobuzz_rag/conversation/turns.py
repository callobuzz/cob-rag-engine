# src/callobuzz_rag/conversation/turns.py
"""Write the `assistant_turns` row for one turn (added + flushed; the caller commits)."""

import uuid
from typing import TYPE_CHECKING, Any

from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.db.models.assistant_turn import AssistantTurn

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def record_turn(
    session: "AsyncSession",
    *,
    corpus_id: uuid.UUID,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    message_id: uuid.UUID | None,
    query_id: uuid.UUID | None,
    channel: Channel,
    assistant_version: int | None,
    label: str,
    label_source: str,
    label_confidence: float,
    language: str,
    script: str,
    route: str,
    topic_id: str | None,
    top_score: float | None,
    gate_passed: bool | None,
    claims_removed: int,
    claims_flagged: int,
    rules_fired: list[str],
    offers: list[str],
    no_answer: bool,
    question_masked: str,
    is_preview: bool,
    latency_ms: int,
    timings: dict[str, Any],
    office_open: bool | None = None,  # TV4 value
) -> AssistantTurn:
    row = AssistantTurn(
        corpus_id=corpus_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        message_id=message_id,
        query_id=query_id,
        channel=channel.value,
        assistant_version=assistant_version,
        label=label,
        label_source=label_source,
        label_confidence=label_confidence,
        language=language,
        script=script,
        route=route,
        topic_id=topic_id,
        top_score=top_score,
        gate_passed=gate_passed,
        claims_removed=claims_removed,
        claims_flagged=claims_flagged,
        rules_fired=rules_fired[:50],
        offers=offers[:10],
        no_answer=no_answer,
        question_masked=question_masked[:2000],
        is_preview=is_preview,
        latency_ms=latency_ms,
        timings=timings,
        office_open=office_open,  # TV4 value
    )
    session.add(row)
    await session.flush()
    return row
