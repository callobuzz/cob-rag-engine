"""While a business's website is still being learned (BIZ4, D15).

The first minutes after sign-up the knowledge base is empty. A visitor's question then gets a
polite "I'm still learning about <business>" and the team's contact options, never a hard error
and never a wrong answer. `kb_is_learning` is the flag the surfaces put on the turn; the
director turns it into the reply (`_learning_decision`).
"""

import logging
import uuid
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import and_, exists, or_, select

from callobuzz_rag.db.models.document import Document
from callobuzz_rag.db.models.document_version import DocumentVersion
from callobuzz_rag.db.models.website import WebsiteSource
from callobuzz_rag.pipeline.records import SYSTEM_RECORD_PREFIX

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql import Select

logger = logging.getLogger(__name__)

LEARNING_TEXT: Final[dict[str, str]] = {
    "en": "I'm still learning about {business_name}, and I'll be ready in a few minutes.",
    "hi": "मैं अभी {business_name} के बारे में सीख रही हूँ, कुछ ही मिनटों में तैयार हो जाऊँगी।",
    "hi-Latn": (
        "Main abhi {business_name} ke baare mein seekh rahi hoon, "
        "kuch hi minute mein taiyaar ho jaungi."
    ),
}
PLAIN_NAME: Final = "this business"
RUNNING: Final = ("discovering", "learning")


def learning_text(texts: Mapping[str, str], lang: str, business_name: str) -> str:
    """The reply in `lang` (English when there is none), naming the business."""
    template = texts.get(lang) or texts.get(lang.split("-")[0]) or texts["en"]
    return template.format(business_name=business_name.strip() or PLAIN_NAME)


def learning_statement(corpus_id: uuid.UUID) -> "Select[Any]":
    """True while a site of the knowledge base has its first pass running and the knowledge
    base has no active document that is not the engine's own (the business card)."""
    first_pass_running = exists().where(
        WebsiteSource.corpus_id == corpus_id,
        WebsiteSource.first_pass_done_at.is_(None),
        WebsiteSource.status.in_(RUNNING),
    )
    own_document = exists().where(
        Document.corpus_id == corpus_id,
        or_(Document.external_id.is_(None), ~Document.external_id.like(f"{SYSTEM_RECORD_PREFIX}%")),
        exists().where(
            DocumentVersion.document_id == Document.id, DocumentVersion.status == "active"
        ),
    )
    return select(and_(first_pass_running, ~own_document))


async def kb_is_learning(session: "AsyncSession", corpus_id: uuid.UUID) -> bool:
    """One query per turn; a database problem means "not learning" (never blocks an answer)."""
    try:
        result = await session.execute(learning_statement(corpus_id))
        return result.scalar() is True
    except Exception:
        logger.warning("could not tell whether %s is still learning", corpus_id, exc_info=True)
        return False
