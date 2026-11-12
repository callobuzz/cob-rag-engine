"""The COB review list: COB's weak answers, grouped, for the operator to fix in the docs.

Three signals from the COB knowledge base's own traffic, over the last N days:

- `no_answer`: COB found nothing good enough and said so (`assistant_turns.no_answer`);
- `claims_removed`: COB's fact check cut statements out of an answer (`assistant_turns`);
- `thumbs_down`: a person rated an answer down, with an optional comment
  (`messages.agent_log["cob"]["rating"]`).

Only masked text is shown: questions were masked when stored and are masked again here. The list
is a read-only view for the platform operator; fixing means editing a help page in the repository.
"""

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.conversation.pii import mask_pii
from callobuzz_rag.conversation.review import group_key
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message

REASON_ORDER: Final = ("no_answer", "claims_removed", "thumbs_down")
MAX_CONVERSATIONS: Final = 2000
MAX_MESSAGES: Final = 10000
MAX_TURNS: Final = 2000
MAX_COMMENTS: Final = 5


@dataclass
class Signal:
    """One weak answer (keyed by its message, or by its turn when it has none)."""

    key: str
    question: str
    at: datetime
    role: str | None = None
    language: str | None = None
    top_slug: str | None = None
    reasons: set[str] = field(default_factory=set)
    comment: str | None = None


@dataclass(frozen=True)
class ReviewGroup:
    group: str
    question: str
    count: int
    last_at: datetime
    role: str
    language: str
    top_slug: str | None
    reasons: list[str]
    comments: list[str]


def _cob_log(message: Message | None) -> dict[str, Any]:
    log = (message.agent_log or {}) if message is not None else {}
    cob = log.get("cob")
    return cob if isinstance(cob, dict) else {}


def _top_slug(cob: dict[str, Any]) -> str | None:
    sources = cob.get("sources")
    if isinstance(sources, list) and sources and isinstance(sources[0], dict):
        slug = sources[0].get("slug")
        return str(slug) if slug else None
    return None


async def gather_signals(
    session: AsyncSession, corpus_id: uuid.UUID, since: datetime
) -> list[Signal]:
    """Every weak answer of COB's knowledge base since `since`."""
    conversations = (
        (
            await session.execute(
                select(Conversation)
                .where(
                    Conversation.corpus_id == corpus_id,
                    Conversation.kind == "cob",
                    Conversation.updated_at >= since,
                )
                .order_by(Conversation.updated_at.desc())
                .limit(MAX_CONVERSATIONS)
            )
        )
        .scalars()
        .all()
    )
    messages: list[Message] = []
    if conversations:
        messages = list(
            (
                await session.execute(
                    select(Message)
                    .where(
                        Message.conversation_id.in_([c.id for c in conversations]),
                        Message.created_at >= since,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(MAX_MESSAGES)
                )
            )
            .scalars()
            .all()
        )
    by_id = {m.id: m for m in messages}
    users_by_conversation: dict[uuid.UUID, list[Message]] = defaultdict(list)
    for m in messages:
        if m.role == "user":
            users_by_conversation[m.conversation_id].append(m)

    def question_of(reply: Message) -> str | None:
        earlier = [
            u
            for u in users_by_conversation[reply.conversation_id]
            if u.created_at <= reply.created_at
        ]
        return max(earlier, key=lambda u: u.created_at).content if earlier else None

    signals: dict[str, Signal] = {}
    turns = (
        (
            await session.execute(
                select(AssistantTurn)
                .where(
                    AssistantTurn.corpus_id == corpus_id,
                    AssistantTurn.is_preview.is_(False),
                    AssistantTurn.created_at >= since,
                    or_(AssistantTurn.no_answer.is_(True), AssistantTurn.claims_removed > 0),
                )
                .order_by(AssistantTurn.created_at.desc())
                .limit(MAX_TURNS)
            )
        )
        .scalars()
        .all()
    )
    for turn in turns:
        reply = by_id.get(turn.message_id) if turn.message_id else None
        cob = _cob_log(reply)
        key = str(turn.message_id or turn.id)
        signal = signals.setdefault(
            key,
            Signal(
                key,
                turn.question_masked,
                turn.created_at,
                role=cob.get("role"),
                language=turn.language or cob.get("language"),
                top_slug=_top_slug(cob),
            ),
        )
        if turn.no_answer:
            signal.reasons.add("no_answer")
        if turn.claims_removed > 0:
            signal.reasons.add("claims_removed")
    for reply in messages:
        if reply.role != "assistant":
            continue
        cob = _cob_log(reply)
        if cob.get("rating") != -1:
            continue
        question = question_of(reply)
        if question is None:
            continue
        signal = signals.setdefault(
            str(reply.id),
            Signal(
                str(reply.id),
                question,
                reply.created_at,
                role=cob.get("role"),
                language=cob.get("language"),
                top_slug=_top_slug(cob),
            ),
        )
        signal.reasons.add("thumbs_down")
        comment = cob.get("comment")
        signal.comment = str(comment) if comment else None
        signal.role = signal.role or cob.get("role")
        signal.top_slug = signal.top_slug or _top_slug(cob)
        signal.language = signal.language or cob.get("language")
    return list(signals.values())


def _most_common(values: list[str | None], default: str) -> str:
    counted = Counter(v for v in values if v)
    return counted.most_common(1)[0][0] if counted else default


def group_signals(signals: list[Signal], *, only_down: bool, limit: int) -> list[ReviewGroup]:
    """Group by normalised question (so rewording keeps a topic together), newest first."""
    chosen = [s for s in signals if not only_down or "thumbs_down" in s.reasons]
    groups: dict[str, list[Signal]] = defaultdict(list)
    for s in chosen:
        groups[group_key(None, mask_pii(s.question).text)].append(s)
    out: list[ReviewGroup] = []
    for key, members in groups.items():
        members.sort(key=lambda s: s.at, reverse=True)
        newest = members[0]
        reasons = {r for s in members for r in s.reasons}
        comments = [mask_pii(s.comment).text for s in members if s.comment]
        out.append(
            ReviewGroup(
                group=key,
                question=mask_pii(newest.question).text,
                count=len(members),
                last_at=newest.at,
                role=_most_common([s.role for s in members], "unknown"),
                language=_most_common([s.language for s in members], "en"),
                top_slug=_most_common([s.top_slug for s in members], "") or None,
                reasons=[r for r in REASON_ORDER if r in reasons],
                comments=comments[:MAX_COMMENTS],
            )
        )
    out.sort(key=lambda g: g.last_at, reverse=True)
    return out[:limit]
