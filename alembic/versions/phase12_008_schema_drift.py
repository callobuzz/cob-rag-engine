"""Schema drift: column comments, NOT NULL timestamps and the two FK indexes in the models.

Revision ID: phase12_008
Revises: phase12_004
Create Date: 2026-10-03

`alembic check` reported three kinds of difference between the models and a migrated database:

* column comments the models declare but migrations never wrote -> added here;
* `created_at` / `updated_at` nullable in the database, NOT NULL in the models. All have a
  server default of now(), so a NULL could only come from a hand-written insert. The safer side
  wins: NULLs are backfilled (updated_at from created_at where the table has one) and the column
  becomes NOT NULL;
* `ix_corpora_tenant_id` and `ix_gap_report_corpus_id` exist in the database (phase8) but the
  models did not declare them -> the models now do; nothing changes in the database.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "phase12_008"
down_revision: str | None = "phase12_004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, comment)
_COMMENTS: list[tuple[str, str, str]] = [
    ("api_keys", "id_short", "First 8 chars of the owner's id (in the key)"),
    (
        "api_keys",
        "lookup",
        "First 8 chars of the key's random part (not a secret); NULL if migrated",
    ),
    ("api_keys", "key_prefix", "What the dashboard shows of the key, never the key"),
    ("api_keys", "created_by", "Who made it: an e-mail, 'admin key' or a key's name"),
    ("api_keys", "last_used_at", "Updated at most once a minute"),
    ("corpora", "api_key_hash", "bcrypt hash of the corpus-level API key"),
    ("corpora", "qdrant_collection", "Qdrant collection name for this corpus's vectors"),
    (
        "corpora",
        "llm_config",
        "Per-corpus LLM overrides: model, api_key (encrypted), temperature, max_tokens",
    ),
    (
        "corpora",
        "prompt_config",
        "Per-corpus prompt template overrides: system_prompt, constraints, response_language",
    ),
    ("corpora", "tenant_id", "Owning tenant. Every corpus belongs to one (accounts A1)."),
    ("tenants", "api_key_hash", "bcrypt hash of the tenant-level API key (tk_xxx)"),
    ("widget_keys", "public_key", "Publishable pk_ key (not a secret)"),
    (
        "widget_keys",
        "allowed_origins",
        "Exact browser origins allowed to use this key, e.g. https://acme.com",
    ),
    ("widget_keys", "rate_limit_per_minute", "Messages per visitor per minute"),
    (
        "widget_keys",
        "appearance",
        "Widget branding: title, greeting, primary_color, position, placeholder",
    ),
    (
        "widget_keys",
        "daily_message_limit",
        "Messages per key per UTC day, across all visitors (0 = unlimited)",
    ),
]

# (table, column): timestamptz DEFAULT now(), nullable in the database, NOT NULL in the models.
_TIMESTAMPS: list[tuple[str, str]] = [
    ("api_keys", "created_at"),
    ("assistant_configs", "created_at"),
    ("assistant_review_actions", "created_at"),
    ("assistant_setup_sessions", "created_at"),
    ("assistant_setup_sessions", "updated_at"),
    ("email_codes", "created_at"),
    ("engine_settings", "updated_at"),
    ("eval_criteria", "created_at"),
    ("eval_criteria", "updated_at"),
    ("eval_run_samples", "created_at"),
    ("eval_settings", "updated_at"),
    ("golden_sets", "created_at"),
    ("golden_sets", "updated_at"),
    ("invitations", "created_at"),
    ("leads", "created_at"),
    ("leads", "updated_at"),
    ("plans", "created_at"),
    ("plans", "updated_at"),
    ("sessions", "created_at"),
    ("tenant_members", "created_at"),
    ("tenant_question_counts", "updated_at"),
    ("users", "created_at"),
    ("users", "updated_at"),
]


def _column_type(conn: sa.Connection, table: str, column: str) -> sa.types.TypeEngine:
    insp = sa.inspect(conn)
    return next(c["type"] for c in insp.get_columns(table) if c["name"] == column)


def upgrade() -> None:
    conn = op.get_bind()
    tables_with_created = {t for t, c in _TIMESTAMPS if c == "created_at"}
    for table, column in _TIMESTAMPS:
        if column == "updated_at" and table in tables_with_created:
            op.execute(f'UPDATE "{table}" SET updated_at = created_at WHERE updated_at IS NULL')
        op.execute(f'UPDATE "{table}" SET "{column}" = now() WHERE "{column}" IS NULL')
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
            existing_server_default=sa.text("now()"),
        )
    for table, column, comment in _COMMENTS:
        op.alter_column(
            table, column, existing_type=_column_type(conn, table, column), comment=comment
        )


def downgrade() -> None:
    conn = op.get_bind()
    for table, column, _comment in _COMMENTS:
        op.alter_column(
            table, column, existing_type=_column_type(conn, table, column), comment=None
        )
    for table, column in _TIMESTAMPS:
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
            existing_server_default=sa.text("now()"),
        )
