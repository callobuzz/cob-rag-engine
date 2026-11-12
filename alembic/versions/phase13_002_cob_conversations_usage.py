"""COB chat: private conversations of kind 'cob' and a per-workspace usage key.

Revision ID: phase13_002
Revises: phase13_001
Create Date: 2026-10-04

Two changes, both needed before COB can answer anyone:

* `conversations` gains `kind` ('assistant' = a knowledge base's own chats, 'cob' = a person
  talking to COB), `user_id` (the author; COB chats are private to them) and `scope_tenant_id`
  (the workspace the chat was started in, NULL for the operator view), plus an index for the
  per-person history list. Existing rows keep `kind = 'assistant'`.
* `usage_daily` keeps one row per (day, corpus, source, payer, model). COB answers every
  workspace from one system knowledge base, so two workspaces on the same day would share a
  row and the monthly count and the usage report would mix them. The unique key therefore
  gains `tenant_id`. Ordinary traffic is unaffected: a corpus has exactly one tenant, so the
  wider key never splits an existing row.

Downgrade: the old key cannot hold two rows that differ only by tenant, so those rows are merged
first (sums are kept; the `tenant_id` of the earliest row stays), then the narrow key returns
and the conversation columns go.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase13_002"
down_revision: str | None = "phase13_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KEY = "uq_usage_daily_day_corpus_source_payer_model"
NEW_KEY = "uq_usage_daily_day_corpus_tenant_source_payer_model"


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("kind", sa.String(12), server_default=sa.text("'assistant'"), nullable=False),
    )
    op.add_column("conversations", sa.Column("user_id", sa.UUID(), nullable=True))
    op.add_column("conversations", sa.Column("scope_tenant_id", sa.UUID(), nullable=True))
    op.create_check_constraint(
        "ck_conversations_kind", "conversations", "kind in ('assistant','cob')"
    )
    op.create_foreign_key(
        "fk_conversations_user_id",
        "conversations",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_conversations_scope_tenant_id",
        "conversations",
        "tenants",
        ["scope_tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_conversations_user_kind_updated", "conversations", ["user_id", "kind", "updated_at"]
    )
    op.drop_constraint(OLD_KEY, "usage_daily", type_="unique")
    op.create_unique_constraint(
        NEW_KEY, "usage_daily", ["day", "corpus_id", "tenant_id", "source", "payer", "model"]
    )


def downgrade() -> None:
    op.drop_constraint(NEW_KEY, "usage_daily", type_="unique")
    op.execute(
        """
        UPDATE usage_daily u SET requests = m.requests, prompt_tokens = m.prompt_tokens,
            completion_tokens = m.completion_tokens, reasoning_tokens = m.reasoning_tokens,
            cost_micro = m.cost_micro, unpriced_requests = m.unpriced_requests
        FROM (
            SELECT (array_agg(id ORDER BY rank))[1] AS keep_id,
                   SUM(requests) AS requests, SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(reasoning_tokens) AS reasoning_tokens,
                   SUM(cost_micro) AS cost_micro,
                   SUM(unpriced_requests) AS unpriced_requests
            FROM (
                SELECT *, ROW_NUMBER() OVER (ORDER BY updated_at, id::text) AS rank
                FROM usage_daily
            ) r GROUP BY day, corpus_id, source, payer, model
        ) m WHERE u.id = m.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM usage_daily u USING (
            SELECT id, ROW_NUMBER() OVER (
                PARTITION BY day, corpus_id, source, payer, model ORDER BY updated_at, id::text
            ) AS rn
            FROM usage_daily
        ) d WHERE u.id = d.id AND d.rn > 1
        """
    )
    op.create_unique_constraint(
        OLD_KEY, "usage_daily", ["day", "corpus_id", "source", "payer", "model"]
    )
    op.drop_index("ix_conversations_user_kind_updated", table_name="conversations")
    op.drop_constraint("fk_conversations_scope_tenant_id", "conversations", type_="foreignkey")
    op.drop_constraint("fk_conversations_user_id", "conversations", type_="foreignkey")
    op.drop_constraint("ck_conversations_kind", "conversations", type_="check")
    op.drop_column("conversations", "scope_tenant_id")
    op.drop_column("conversations", "user_id")
    op.drop_column("conversations", "kind")
