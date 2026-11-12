"""Usage: the real cost per request, who paid, and which model.

`usage_daily` gains `cost_micro` (USD millionths, integer: the provider's reported cost, or a
price-list cost), `reasoning_tokens`, `unpriced_requests` (requests whose price could not be
found; they add no cost rather than a made-up zero), `payer` (`operator` = the engine's own
LLM key, `byok` = a key the knowledge base stores or the request sent) and `model` (the answer
model). The unique key becomes (day, corpus, source, payer, model), so one day keeps a row per
payer and model and the upsert stays atomic.

Existing rows keep their old estimate: `cost_micro` is back-filled from `cost_usd`, and their
payer is `operator` and model empty (shown as "unknown model"). `cost_usd` stays, unwritten.
The old estimator returned 0.0 for any model missing from its price table, so an old row with
tokens but zero cost is back-filled as unpriced (`unpriced_requests = GREATEST(requests, 1)`):
it reads "price unknown", never a known zero.

Revision ID: phase11_014
Revises: phase11_013
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase11_014"
down_revision: str | None = "phase11_013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_daily",
        sa.Column("payer", sa.String(16), nullable=False, server_default=sa.text("'operator'")),
    )
    op.add_column(
        "usage_daily",
        sa.Column("model", sa.String(128), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "usage_daily",
        sa.Column("reasoning_tokens", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "usage_daily",
        sa.Column("cost_micro", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "usage_daily",
        sa.Column("unpriced_requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.execute("UPDATE usage_daily SET cost_micro = ROUND(cost_usd * 1000000)::bigint")
    # The old estimator answered 0.0 for a model missing from its price table, so a row with
    # tokens and no cost was unpriced, not free. Flag it so screens say "price unknown"
    # instead of a made-up zero.
    op.execute(
        "UPDATE usage_daily SET unpriced_requests = GREATEST(requests, 1) "
        "WHERE cost_usd = 0 AND (prompt_tokens + completion_tokens) > 0"
    )
    op.drop_constraint("uq_usage_daily_day_corpus_source", "usage_daily", type_="unique")
    op.create_unique_constraint(
        "uq_usage_daily_day_corpus_source_payer_model",
        "usage_daily",
        ["day", "corpus_id", "source", "payer", "model"],
    )
    op.create_index("ix_usage_daily_tenant_day", "usage_daily", ["tenant_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_usage_daily_tenant_day", table_name="usage_daily")
    op.drop_constraint(
        "uq_usage_daily_day_corpus_source_payer_model", "usage_daily", type_="unique"
    )
    # Rows that differ only by payer or model merge into one before the narrower key returns.
    op.execute(
        """
        UPDATE usage_daily u SET requests = m.requests, prompt_tokens = m.prompt_tokens,
            completion_tokens = m.completion_tokens, cost_usd = m.cost_micro / 1000000.0
        FROM (
            SELECT (array_agg(id ORDER BY id::text))[1] AS keep_id,
                   SUM(requests) AS requests,
                   SUM(prompt_tokens) AS prompt_tokens,
                   SUM(completion_tokens) AS completion_tokens,
                   SUM(cost_micro) AS cost_micro
            FROM usage_daily GROUP BY day, corpus_id, source
        ) m WHERE u.id = m.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM usage_daily u USING (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY day, corpus_id, source ORDER BY id::text) AS rn
            FROM usage_daily
        ) d WHERE u.id = d.id AND d.rn > 1
        """
    )
    op.create_unique_constraint(
        "uq_usage_daily_day_corpus_source", "usage_daily", ["day", "corpus_id", "source"]
    )
    for col in ("unpriced_requests", "cost_micro", "reasoning_tokens", "model", "payer"):
        op.drop_column("usage_daily", col)
