"""The review inbox: what visitors could not get answered, and the one-click fixes (C7).

Read model over `assistant_turns` (no answer, low confidence) and `feedback` (thumbs down).
Handoff requests join once C5's `handoffs` table exists. An item is a group of similar turns:
by the turn's topic, else by its first three search terms. Acting on an item hides it until a
newer turn of the same group arrives.
"""

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.assistant_quality import (
    ReviewActionName,
    ReviewActionOut,
    ReviewActionRequest,
    ReviewExample,
    ReviewItemOut,
    ReviewKind,
)
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.store import get_runtime_config
from callobuzz_rag.db.models import AssistantReviewAction
from callobuzz_rag.retrieval.sparse import analyze

# A grounded answer whose top score cleared the gate by less than this is a near miss.
LOW_MARGIN = 0.05
LOW_LABEL_CONFIDENCE = 0.6
MAX_ROWS = 5000
KNOWN_WINDOW = timedelta(days=90)
_SUGGESTED: dict[str, list[ReviewActionName]] = {
    "no_answer": ["added_answer", "added_document", "added_rule", "dismissed"],
    "low_confidence": ["added_answer", "added_document", "dismissed"],
    "thumbs_down": ["added_answer", "added_rule", "dismissed"],
    "handoff": ["added_rule", "added_answer", "dismissed"],
}


@dataclass(frozen=True)
class ReviewTurn:
    id: uuid.UUID | None
    kind: str
    group: str
    title: str
    question: str
    at: datetime
    conversation_id: uuid.UUID | None
    topic_id: str | None


def group_key(topic_id: str | None, question: str) -> str:
    """The turn's topic, else the first three of its distinct search terms in alphabetical
    order, so rewording or reordering a question keeps it in the same group."""
    if topic_id:
        return f"topic:{topic_id}"
    terms = sorted(set(analyze(question)))[:3]
    return "terms:" + " ".join(terms) if terms else "text:" + question.strip().lower()[:80]


def item_key(kind: str, group: str) -> str:
    return hashlib.sha256(f"{kind}:{group}".encode()).hexdigest()[:32]


def build_items(rows: list[ReviewTurn], acted: dict[str, datetime]) -> list[ReviewItemOut]:
    """Group, hide what was acted on after its newest turn, sort by how many visitors asked."""
    groups: dict[tuple[str, str], list[ReviewTurn]] = defaultdict(list)
    for row in rows:
        groups[(row.kind, row.group)].append(row)
    items: list[ReviewItemOut] = []
    for (kind, group), members in groups.items():
        members.sort(key=lambda r: r.at, reverse=True)
        key = item_key(kind, group)
        newest = members[0].at
        if key in acted and acted[key] >= newest:
            continue
        items.append(
            ReviewItemOut(
                item_key=key,
                kind=kind,
                title=members[0].title,
                count=len(members),
                last_at=newest,
                topic_id=members[0].topic_id,
                examples=[
                    ReviewExample(
                        turn_id=m.id,
                        question=m.question,
                        at=m.at,
                        conversation_id=m.conversation_id,
                    )
                    for m in members[:5]
                ],
                suggested=list(_SUGGESTED[kind]),
            )
        )
    items.sort(key=lambda i: (-i.count, -i.last_at.timestamp()))
    return items


async def _rows(
    session: AsyncSession, corpus_id: uuid.UUID, since: datetime, until: datetime
) -> list[ReviewTurn]:
    from callobuzz_rag.db.models import AssistantTurn, Feedback

    base = (
        AssistantTurn.corpus_id == corpus_id,
        AssistantTurn.is_preview.is_(False),
        AssistantTurn.created_at >= since,
        AssistantTurn.created_at < until,
    )
    rows: list[ReviewTurn] = []
    turns = (
        (
            await session.execute(
                select(AssistantTurn)
                .where(
                    *base,
                    or_(
                        AssistantTurn.no_answer.is_(True),
                        and_(
                            AssistantTurn.label_source == "model",
                            AssistantTurn.label_confidence < LOW_LABEL_CONFIDENCE,
                        ),
                        # Near misses are chosen here, not after the row cap: the newest
                        # grounded answers must not push older problems out of the window.
                        and_(
                            AssistantTurn.gate_passed.is_(True),
                            AssistantTurn.top_score.is_not(None),
                            AssistantTurn.top_score
                            - AssistantTurn.timings["gate_threshold"].as_float()
                            < LOW_MARGIN,
                        ),
                    ),
                )
                .order_by(AssistantTurn.created_at.desc())
                .limit(MAX_ROWS)
            )
        )
        .scalars()
        .all()
    )
    for t in turns:
        question = t.question_masked or ""
        # The gate's threshold is kept in the persisted timings (not in the public `done`).
        threshold = (t.timings or {}).get("gate_threshold")
        near_miss = (
            t.gate_passed is True
            and t.top_score is not None
            and isinstance(threshold, int | float)
            and t.top_score - float(threshold) < LOW_MARGIN
        )
        shaky = t.label_source == "model" and t.label_confidence < LOW_LABEL_CONFIDENCE
        kind = "no_answer" if t.no_answer else "low_confidence" if (near_miss or shaky) else None
        if kind:
            rows.append(
                ReviewTurn(
                    t.id,
                    kind,
                    group_key(t.topic_id, question),
                    question,
                    question,
                    t.created_at,
                    t.conversation_id,
                    t.topic_id,
                )
            )
    disliked = (
        (
            await session.execute(
                select(AssistantTurn)
                .join(Feedback, Feedback.query_log_id == AssistantTurn.query_id)
                .where(*base, Feedback.rating <= 2)
                .order_by(AssistantTurn.created_at.desc())
                .limit(MAX_ROWS)
            )
        )
        .scalars()
        .all()
    )
    for t in disliked:
        question = t.question_masked or ""
        rows.append(
            ReviewTurn(
                t.id,
                "thumbs_down",
                f"turn:{t.id}",
                question,
                question,
                t.created_at,
                t.conversation_id,
                t.topic_id,
            )
        )
    return rows


async def _acted(session: AsyncSession, corpus_id: uuid.UUID) -> dict[str, datetime]:
    actions = (
        await session.execute(
            select(AssistantReviewAction.item_key, AssistantReviewAction.created_at).where(
                AssistantReviewAction.corpus_id == corpus_id
            )
        )
    ).all()
    latest: dict[str, datetime] = {}
    for key, at in actions:
        if key not in latest or at > latest[key]:
            latest[key] = at
    return latest


async def list_review_items(
    session: AsyncSession,
    corpus_id: uuid.UUID,
    *,
    kind: ReviewKind | None,
    since: datetime,
    until: datetime,
) -> list[ReviewItemOut]:
    rows = await _rows(session, corpus_id, since, until)
    if kind:
        rows = [r for r in rows if r.kind == kind]
    return build_items(rows, await _acted(session, corpus_id))


async def _known_keys(session: AsyncSession, corpus_id: uuid.UUID) -> dict[str, str]:
    now = datetime.now(UTC)
    rows = await _rows(session, corpus_id, now - KNOWN_WINDOW, now + timedelta(minutes=1))
    return {item_key(r.kind, r.group): r.kind for r in rows}


def _store(session: AsyncSession, request: Any) -> Any:
    from callobuzz_rag.conversation.store import AssistantConfigStore

    return AssistantConfigStore(session, request, request.app.state.settings)


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "topic"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _with_answer(config: Any, body: ReviewActionRequest) -> str:
    from callobuzz_rag.conversation.schema import Topic

    assert body.answer is not None
    target = _slug(body.answer.topic_name, {t.id for t in config.scope.topics})
    config.scope.topics.append(
        Topic(
            id=target,
            name=body.answer.topic_name,
            description=body.answer.question,
            examples=[body.answer.question],
            verbatim_answer={body.answer.language: body.answer.answer},
        )
    )
    return target


def _with_rule(config: Any, body: ReviewActionRequest) -> str:
    from callobuzz_rag.conversation.schema import GuidanceRule

    assert body.rule is not None
    target = _slug(body.rule.instruction[:30], {r.id for r in config.rules})
    config.rules.append(
        GuidanceRule(
            id=target, type=body.rule.type, instruction=body.rule.instruction, source="owner"
        )
    )
    return target


async def apply_review_action(
    session: AsyncSession, request: Any, corpus: Any, body: ReviewActionRequest
) -> ReviewActionOut:
    """Record what the owner did about an item; `added_answer` / `added_rule` also write the
    assistant's draft (never the published version: the owner reviews and publishes)."""
    from callobuzz_rag.accounts.audit import actor_from_request

    corpus_id = corpus.id
    known = await _known_keys(session, corpus_id)
    if body.item_key not in known:
        raise APIError(404, "Review item not found", "review_item_not_found")
    target: str | None = None
    revision: int | None = None
    if body.action in ("added_answer", "added_rule"):
        if body.base_revision is None:
            raise APIError(
                422,
                "base_revision is required to change the draft",
                "review_base_revision_required",
            )
        if body.action == "added_answer" and body.answer is None:
            raise APIError(422, "answer is required for added_answer", "review_payload_required")
        if body.action == "added_rule" and body.rule is None:
            raise APIError(422, "rule is required for added_rule", "review_payload_required")
        runtime = await get_runtime_config(session, corpus_id, use_draft=True)
        if runtime is None:
            raise APIError(404, "This knowledge base has no assistant", "assistant_not_found")
        config = runtime.config.model_copy(deep=True)
        try:
            target = (
                _with_answer(config, body)
                if body.action == "added_answer"
                else _with_rule(config, body)
            )
            # Appending to a list is not validated by the model: check the whole result, so a
            # change past a limit (40 topics, 100 rules) is a coded 422, not a later failure.
            config = AssistantConfig.model_validate(config.model_dump(mode="json"))
            saved = await _store(session, request).save_draft(corpus, config, body.base_revision)
        except ValidationError as exc:
            where = ".".join(str(p) for p in exc.errors()[0]["loc"]) if exc.errors() else "config"
            raise APIError(
                422,
                f"The assistant cannot hold this change: {where}",
                "assistant_reference_invalid",
                path=where,
            ) from exc
        revision = saved.draft_revision
    session.add(
        AssistantReviewAction(
            corpus_id=corpus_id,
            item_key=body.item_key,
            kind=known[body.item_key],
            action=body.action,
            target=target,
            created_by=actor_from_request(request)[2][:320] if hasattr(request, "state") else None,
        )
    )
    await session.commit()
    return ReviewActionOut(
        item_key=body.item_key, action=body.action, target=target, draft_revision=revision
    )
