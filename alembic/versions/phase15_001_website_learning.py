"""Website learning: the business's site, read by the engine (BIZ1).

Revision ID: phase15_001
Revises: phase14_005
Create Date: 2026-10-05

Three plan columns (the deployer's switches: learning on/off, a page cap per workspace, and the
dashboard features BIZ3 reads), then `website_settings` (one row per knowledge base),
`website_sources` (one site of it), `website_pages` (every URL found and what became of it) and
`website_runs` (one pass over a site; the partial unique index allows only one open discovery
pass per site). `knowledge_articles` is created here too, for BIZ3, so the parallel lane needs no
migration of its own. Everything with knowledge-base affinity cascades with the knowledge base,
workspace-level rows with the workspace.

Downgrade drops the tables and the plan columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase15_001"
down_revision: str | None = "phase14_005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")
_GEN = sa.text("gen_random_uuid()")


def _ts(name: str, *, nullable: bool = True, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=_NOW if default else None,
        nullable=nullable,
    )


def _int(name: str, **kw: object) -> sa.Column:
    return sa.Column(name, sa.Integer(), server_default=sa.text("0"), nullable=False, **kw)


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "website_learning_enabled",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column("plans", sa.Column("website_pages_max", sa.Integer(), nullable=True))
    op.add_column(
        "plans",
        sa.Column(
            "dashboard_features",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_plans_website_pages_max",
        "plans",
        "website_pages_max IS NULL OR website_pages_max >= 1",
    )

    op.create_table(
        "website_settings",
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "exclude_patterns",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("refresh_mode", sa.String(8), server_default=sa.text("'daily'"), nullable=False),
        sa.Column("adaptive", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("learn_key_hash", sa.String(64), nullable=True),
        sa.Column("learn_key_hint", sa.String(8), nullable=True),
        _ts("learn_key_created_at"),
        _ts("last_reread_at"),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("corpus_id"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "refresh_mode in ('off','monthly','weekly','daily','6h','hourly')",
            name="ck_website_settings_refresh",
        ),
    )
    op.create_index("ix_website_settings_tenant_id", "website_settings", ["tenant_id"])
    op.create_index(
        "uq_website_settings_learn_key", "website_settings", ["learn_key_hash"], unique=True
    )

    op.create_table(
        "website_sources",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("origin", sa.String(255), nullable=False),
        sa.Column("scope", sa.String(255), nullable=False),
        sa.Column("status", sa.String(12), server_default=sa.text("'idle'"), nullable=False),
        sa.Column("pause_reason", sa.String(32), nullable=True),
        _int("pages_found"),
        _int("pages_learned"),
        _int("pages_skipped"),
        _ts("first_pass_done_at"),
        _ts("last_run_started_at"),
        _ts("last_run_finished_at"),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        sa.Column(
            "sitemap_urls",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("sitemap_etag", sa.String(255), nullable=True),
        sa.Column("sitemap_last_modified", sa.String(64), nullable=True),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("corpus_id", "scope", name="uq_website_sources_corpus_scope"),
        sa.CheckConstraint(
            "status in ('idle','discovering','learning','ready','paused','blocked','failed')",
            name="ck_website_sources_status",
        ),
    )
    op.create_index("ix_website_sources_corpus_id", "website_sources", ["corpus_id"])
    op.create_index("ix_website_sources_tenant_id", "website_sources", ["tenant_id"])

    op.create_table(
        "website_pages",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("source_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("document_id", _UUID, nullable=True),
        sa.Column("status", sa.String(12), server_default=sa.text("'discovered'"), nullable=False),
        sa.Column("skip_reason", sa.String(32), nullable=True),
        sa.Column("duplicate_of", _UUID, nullable=True),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column("depth", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        _int("rank_score"),
        sa.Column("in_sitemap", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("sitemap_lastmod"),
        sa.Column("sitemap_priority", sa.SmallInteger(), nullable=True),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("etag", sa.String(255), nullable=True),
        sa.Column("last_modified", sa.String(64), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("simhash", sa.BigInteger(), nullable=True),
        sa.Column("words", sa.Integer(), nullable=True),
        sa.Column(
            "excluded_by_owner", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        _ts("last_seen_at"),
        _ts("last_checked_at"),
        _ts("last_changed_at"),
        _int("check_count"),
        _int("change_count"),
        _int("missing_count"),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        _ts("next_check_at"),
        sa.Column("sensor_fingerprint", sa.String(16), nullable=True),
        _ts("sensor_seen_at"),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["website_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source_id", "url_hash", name="uq_website_pages_source_url"),
        sa.CheckConstraint(
            "status in ('discovered','queued','learned','skipped','excluded','failed','removed')",
            name="ck_website_pages_status",
        ),
        sa.CheckConstraint(
            "sitemap_priority IS NULL OR sitemap_priority BETWEEN 0 AND 100",
            name="ck_website_pages_priority",
        ),
    )
    op.create_index("ix_website_pages_corpus_status", "website_pages", ["corpus_id", "status"])
    op.create_index(
        "ix_website_pages_next_check",
        "website_pages",
        ["next_check_at"],
        postgresql_where=sa.text("status = 'learned' AND next_check_at IS NOT NULL"),
    )
    op.create_index("ix_website_pages_document", "website_pages", ["document_id"])

    op.create_table(
        "website_runs",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("source_id", _UUID, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        _ts("started_at", nullable=False, default=True),
        _ts("heartbeat_at", nullable=False, default=True),
        _ts("finished_at"),
        sa.Column("status", sa.String(10), server_default=sa.text("'running'"), nullable=False),
        sa.Column(
            "counts", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("error_code", sa.String(40), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["website_sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "kind in ('first','full','check','push','sensor','page')", name="ck_website_runs_kind"
        ),
        sa.CheckConstraint(
            "status in ('running','done','failed','stopped')", name="ck_website_runs_status"
        ),
    )
    op.create_index("ix_website_runs_source_id", "website_runs", ["source_id"])
    op.create_index(
        "uq_website_runs_open_discovery",
        "website_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("finished_at IS NULL AND kind IN ('first','full')"),
    )

    op.create_table(
        "knowledge_articles",
        sa.Column("id", _UUID, server_default=_GEN, nullable=False),
        sa.Column("corpus_id", _UUID, nullable=False),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("external_id", sa.String(500), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        _ts("created_at", nullable=False, default=True),
        _ts("updated_at", nullable=False, default=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["corpus_id"], ["corpora.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("corpus_id", "external_id", name="uq_knowledge_articles_external"),
        sa.CheckConstraint("kind in ('article','faq')", name="ck_knowledge_articles_kind"),
    )
    op.create_index("ix_knowledge_articles_corpus_id", "knowledge_articles", ["corpus_id"])
    op.create_index("ix_knowledge_articles_tenant_id", "knowledge_articles", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("knowledge_articles")
    op.drop_table("website_runs")
    op.drop_table("website_pages")
    op.drop_table("website_sources")
    op.drop_table("website_settings")
    op.drop_constraint("ck_plans_website_pages_max", "plans", type_="check")
    op.drop_column("plans", "dashboard_features")
    op.drop_column("plans", "website_pages_max")
    op.drop_column("plans", "website_learning_enabled")
