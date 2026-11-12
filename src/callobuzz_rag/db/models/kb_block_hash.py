"""Paragraph hashes per document: how many documents of a knowledge base hold a paragraph.

Page furniture (a footer line, a disclaimer, a "Contact us" call to action) repeats across
the pages of a site. `pipeline/boilerplate.py` counts, for each paragraph of a new
document, how many OTHER documents of the knowledge base already hold it, and drops it
once that reaches the limit. The counts come from these rows, not from a counter, so
deleting a document (the cascade) fixes them.
"""

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from callobuzz_rag.db.base import Base


class KbBlockHash(Base):
    __tablename__ = "kb_block_hashes"

    corpus_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True
    )
    block_hash: Mapped[str] = mapped_column(String(16), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )

    # Deleting or re-ingesting a document finds its rows by document_id.
    __table_args__ = (Index("ix_kb_block_hashes_document_id", "document_id"),)
