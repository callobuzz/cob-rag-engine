"""Pipeline profiles: how each plan's (or business's) questions are answered (PIP1).

Revision ID: phase16_001
Revises: phase15_002
Create Date: 2026-10-05

`pipeline_profiles` holds named bundles (answer model, reranker, retrieval knobs, answer
length, queue priority ...) as one validated JSONB `config`; `builtin_key` marks the three the
engine seeds (free / standard / premium, set by this migration only). Exactly one profile is
the default (partial unique index). `plans.pipeline_profile_id` picks a profile for a plan
(NULL = the default one, so every existing plan keeps answering exactly as before) and
`tenant_pipeline_settings` lets the deployer give one business another profile. Both foreign
keys to a profile are RESTRICT: a profile in use cannot be deleted. The settings row cascades
with its business.

Data: seeds the three profiles (the configs below are copies of `tiers/seeds.py: SEEDS`, kept
equal by a unit test), gives the Internal plan the Premium profile and, only when no plan is
called "Free" already, adds an INACTIVE, non-default "Free" plan on the Free profile with 200
website pages (inactive until the local model is ready, so no business is put on a profile
that cannot answer; the deployer activates it). A deployer's own plan is never touched.

Downgrade removes the seeded Free plan when no business uses it, then the column and tables.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase16_001"
down_revision: str | None = "phase15_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_NOW = sa.text("now()")

_SEEDS = (
    (
        "free",
        "Free",
        "Economy",
        "Answers with the free local model: slower, but it costs nothing to run. "
        "Used by the Free plan and as the fallback when money or fair use runs out.",
        False,
        {
            "answer_route": "local",
            "reranker": "off",
            "multi_query_count": 0,
            "hyde_enabled": False,
            "query_classification_enabled": False,
            "max_answer_tokens": 600,
            "followups": False,
            "priority": "low",
            "max_concurrency": 2,
            "overrides_kb_settings": True,
        },
    ),
    (
        "standard",
        "Standard",
        "Standard",
        "Follows each knowledge base's own settings and the engine's defaults. "
        "The default for every plan that does not pick another profile.",
        True,
        {},
    ),
    (
        "premium",
        "Premium",
        "Premium",
        "Reranks with the AI model and answers ahead of the other profiles when the "
        "engine is busy.",
        False,
        {"reranker": "llm", "priority": "high"},
    ),
)


def upgrade() -> None:
    op.create_table(
        "pipeline_profiles",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("builtin_key", sa.String(16), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "config", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_pipeline_profiles_name"),
        sa.CheckConstraint(
            "builtin_key IS NULL OR builtin_key in ('free','standard','premium')",
            name="ck_pipeline_profiles_builtin_key",
        ),
    )
    op.create_index(
        "uq_pipeline_profiles_builtin_key",
        "pipeline_profiles",
        ["builtin_key"],
        unique=True,
        postgresql_where=sa.text("builtin_key IS NOT NULL"),
    )
    op.create_index(
        "uq_pipeline_profiles_default",
        "pipeline_profiles",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column("plans", sa.Column("pipeline_profile_id", _UUID, nullable=True))
    op.create_foreign_key(
        "fk_plans_pipeline_profile_id",
        "plans",
        "pipeline_profiles",
        ["pipeline_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_plans_pipeline_profile_id", "plans", ["pipeline_profile_id"])

    op.create_table(
        "tenant_pipeline_settings",
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("profile_id", _UUID, nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_NOW, nullable=False),
        sa.Column("updated_by", sa.String(320), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["pipeline_profiles.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_tenant_pipeline_settings_profile_id", "tenant_pipeline_settings", ["profile_id"]
    )

    conn = op.get_bind()
    for key, name, label, description, is_default, config in _SEEDS:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline_profiles "
                "(name, label, description, builtin_key, is_default, config, updated_by) "
                "VALUES (:name, :label, :description, :key, :is_default, "
                "CAST(:config AS jsonb), 'system')"
            ),
            {
                "name": name,
                "label": label,
                "description": description,
                "key": key,
                "is_default": is_default,
                "config": json.dumps(config),
            },
        )
    conn.execute(
        sa.text(
            "UPDATE plans SET pipeline_profile_id = "
            "(SELECT id FROM pipeline_profiles WHERE builtin_key = 'premium') "
            "WHERE is_internal"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO plans (name, description, is_default, is_active, monthly_questions, "
            "website_pages_max, pipeline_profile_id) "
            "SELECT 'Free', 'Answers with the free local model. A starting point: edit the "
            "limits to suit your product.', false, false, NULL, 200, p.id "
            "FROM pipeline_profiles p "
            "WHERE p.builtin_key = 'free' "
            "AND NOT EXISTS (SELECT 1 FROM plans WHERE name = 'Free')"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM plans WHERE name = 'Free' AND pipeline_profile_id = "
            "(SELECT id FROM pipeline_profiles WHERE builtin_key = 'free') "
            "AND NOT EXISTS (SELECT 1 FROM tenants WHERE tenants.plan_id = plans.id)"
        )
    )
    op.drop_table("tenant_pipeline_settings")
    op.drop_index("ix_plans_pipeline_profile_id", table_name="plans")
    op.drop_constraint("fk_plans_pipeline_profile_id", "plans", type_="foreignkey")
    op.drop_column("plans", "pipeline_profile_id")
    op.drop_table("pipeline_profiles")
