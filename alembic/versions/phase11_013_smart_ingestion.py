"""Smart ingestion: paragraph hashes per document, chunk text hashes.

`kb_block_hashes(corpus_id, block_hash, document_id)`: which documents of a knowledge base
hold which paragraph, so a paragraph that repeats across pages (page furniture) can be
recognised and left out of later documents. Rows cascade with the document and the
knowledge base.

`chunks.text_hash` (16 hex characters, nullable) and its index with `corpus_id`: the hash
of a chunk's own text, so search can show a passage stored in several documents once. It
is NULL on existing chunks; `python -m callobuzz_rag.retrieval.reindex --payload-hash`
fills it, in Postgres and in Qdrant, with no re-embedding.

Revision ID: phase11_013
Revises: phase11_012
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase11_013"
down_revision: str | None = "phase11_012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kb_block_hashes",
        sa.Column("corpus_id", sa.UUID(), nullable=False),
        sa.Column("block_hash", sa.String(length=16), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("corpus_id", "block_hash", "document_id"),
    )
    # Deleting a document looks its rows up by document_id.
    op.create_index("ix_kb_block_hashes_document_id", "kb_block_hashes", ["document_id"])
    op.add_column("chunks", sa.Column("text_hash", sa.String(length=16), nullable=True))
    op.create_index("ix_chunks_corpus_text_hash", "chunks", ["corpus_id", "text_hash"])


def downgrade() -> None:
    op.drop_index("ix_chunks_corpus_text_hash", table_name="chunks")
    op.drop_column("chunks", "text_hash")
    op.drop_index("ix_kb_block_hashes_document_id", table_name="kb_block_hashes")
    op.drop_table("kb_block_hashes")
