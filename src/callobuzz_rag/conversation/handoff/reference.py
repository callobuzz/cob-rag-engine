"""Reference codes: one short code per conversation a visitor can quote to a person.

`SD-4F7K` = two letters from the business's initials + four symbols from an alphabet with no
look-alikes (no 0 O 1 I L U). Unique per knowledge base (partial unique index on
`conversations`). The pure parts are the generator and the normaliser; `reserve_ref` is the one
function that touches the database, in its own short transaction so a streaming turn never
holds a lock on the conversation.
"""

import logging
import random
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

REF_ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_DEFAULT_PREFIX: Final = "RF"
_ATTEMPTS: Final = 8
_NORMAL = re.compile(rf"([A-Z]{{2}})([{REF_ALPHABET}]{{4}})")
_rng = random.SystemRandom()


def _letters(word: str) -> str:
    folded = unicodedata.normalize("NFKD", word).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in folded.upper() if "A" <= ch <= "Z")


def ref_prefix(business_name: str) -> str:
    """Initials of the first two words, else the first two letters, else `RF`."""
    words = [w for w in (_letters(w) for w in business_name.split()) if w]
    if len(words) >= 2:
        return words[0][0] + words[1][0]
    if words and len(words[0]) >= 2:
        return words[0][:2]
    return _DEFAULT_PREFIX


def new_ref(prefix: str, rng: random.Random | None = None) -> str:
    r = rng or _rng
    return f"{prefix}-" + "".join(r.choice(REF_ALPHABET) for _ in range(4))


def normalise_ref(raw: str) -> str | None:
    """`sd4f7k` / `SD 4F7K` / `sd-4f7k.` -> `SD-4F7K`; anything ambiguous or malformed -> None."""
    compact = re.sub(r"[\s\-.]", "", raw or "").upper()
    m = _NORMAL.fullmatch(compact)
    return f"{m.group(1)}-{m.group(2)}" if m else None


async def ref_code_of(session: "AsyncSession", conversation_id: uuid.UUID) -> str | None:
    """The conversation's reference, or None when it has none (yet)."""
    found = (
        await session.execute(
            text("SELECT ref_code FROM conversations WHERE id = :id"), {"id": conversation_id}
        )
    ).scalar_one_or_none()
    return str(found) if found else None


async def assign_ref(
    session: "AsyncSession", conversation_id: uuid.UUID, prefix: str
) -> str | None:
    """Give the conversation its reference inside the caller's transaction (the caller
    commits). Each attempt runs in a SAVEPOINT, so a collision on the partial unique index
    costs one retry, not the caller's work. None after eight collisions."""
    for _ in range(_ATTEMPTS):
        existing = await ref_code_of(session, conversation_id)
        if existing:
            return existing
        try:
            async with session.begin_nested():
                claimed = (
                    await session.execute(
                        text(
                            "UPDATE conversations SET ref_code = :c "
                            "WHERE id = :id AND ref_code IS NULL RETURNING ref_code"
                        ),
                        {"c": new_ref(prefix), "id": conversation_id},
                    )
                ).scalar_one_or_none()
        except IntegrityError:
            continue
        if claimed:
            return str(claimed)
        # a concurrent winner got in between the read and the update: re-read next round
    logger.warning("No free reference code after %d tries", _ATTEMPTS)
    return None


async def reserve_ref(
    session_factory: "Callable[[], AsyncSession]", conversation_id: uuid.UUID, prefix: str
) -> str | None:
    """The conversation's reference, assigned once, in its own short transaction. None
    (logged) on a database error or after eight collisions: the card is then shown without a
    reference rather than not at all."""
    try:
        async with session_factory() as session:
            ref = await assign_ref(session, conversation_id, prefix)
            await session.commit()
            return ref
    except Exception:
        logger.warning("Could not reserve a reference code", exc_info=True)
        return None


async def refs_for(
    session: "AsyncSession", conversation_ids: "Iterable[uuid.UUID | None]"
) -> dict[uuid.UUID, str]:
    """`{conversation_id: reference}` for the conversations that have one (one query)."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.conversation import Conversation

    ids = {c for c in conversation_ids if c is not None}
    if not ids:
        return {}
    rows = await session.execute(
        select(Conversation.id, Conversation.ref_code).where(
            Conversation.id.in_(ids), Conversation.ref_code.is_not(None)
        )
    )
    return {cid: str(code) for cid, code in rows.all() if code}


async def find_matches(
    session: "AsyncSession", ref: str, allowed_corpus_ids: "Iterable[uuid.UUID] | None"
) -> list[dict[str, object]]:
    """The conversations holding `ref` among the knowledge bases the caller may read (None =
    all of them), newest first, each with its handoff and lead ids. A reference is unique per
    knowledge base, so one business with two of them can get two matches. Ids only."""
    from sqlalchemy import select

    from callobuzz_rag.db.models.conversation import Conversation
    from callobuzz_rag.db.models.handoff import Handoff
    from callobuzz_rag.db.models.lead import Lead

    query = select(Conversation.id, Conversation.corpus_id).where(
        Conversation.ref_code == ref, Conversation.kind == "assistant"
    )
    if allowed_corpus_ids is not None:
        query = query.where(Conversation.corpus_id.in_(list(allowed_corpus_ids)))
    found = (await session.execute(query.order_by(Conversation.created_at.desc()))).all()
    if not found:
        return []
    ids = [cid for cid, _ in found]
    handoffs: dict[uuid.UUID, list[uuid.UUID]] = {}
    for hid, cid in (
        await session.execute(
            select(Handoff.id, Handoff.conversation_id)
            .where(Handoff.conversation_id.in_(ids))
            .order_by(Handoff.created_at.desc())
        )
    ).all():
        if cid is not None:
            handoffs.setdefault(cid, []).append(hid)
    leads: dict[uuid.UUID, list[uuid.UUID]] = {}
    for lid, cid in (
        await session.execute(
            select(Lead.id, Lead.conversation_id)
            .where(Lead.conversation_id.in_(ids))
            .order_by(Lead.created_at.desc())
        )
    ).all():
        if cid is not None:
            leads.setdefault(cid, []).append(lid)
    return [
        {
            "corpus_id": corpus_id,
            "conversation_id": cid,
            "handoff_ids": handoffs.get(cid, []),
            "lead_ids": leads.get(cid, []),
        }
        for cid, corpus_id in found
    ]
