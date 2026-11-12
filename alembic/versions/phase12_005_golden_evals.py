"""One quality system: golden sets, behaviour fields and tags on golden pairs, golden runs and
experiments on eval_run, per-question eval_run_samples, judge criteria and settings, review actions
(conversation C7).

Every existing golden pair moves into one default set per corpus ("Golden questions",
`is_default`, at most one per corpus: partial unique index). `expected_answer` becomes nullable
(a behaviour-only question has none); a CHECK keeps at least one of answer/behaviour. `origin`
is back-filled from `source` (`feedback` -> `from_feedback`, everything else `human`).
`eval_run.kind` defaults to `traffic`, so every existing run keeps meaning what it meant.

Downgrade drops the new tables and columns. It first deletes behaviour-only pairs (no expected
answer) because the old schema cannot hold them, then restores NOT NULL; this loss is stated here
on purpose.

Revision ID: phase12_005
Revises: phase12_002
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_005"
down_revision: str | None = "phase12_002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB
NOW = sa.text("now()")
GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "golden_sets",
        sa.Column("id", UUID, primary_key=True, server_default=GEN),
        sa.Column(
            "corpus_id", UUID, sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source", sa.String(12), nullable=False, server_default=sa.text("'owner'")),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.CheckConstraint(
            "source IN ('owner', 'template', 'generated', 'setup', 'import')",
            name="ck_golden_sets_source",
        ),
    )
    op.create_index("ix_golden_sets_corpus_id", "golden_sets", ["corpus_id"])
    op.create_index(
        "uq_golden_sets_default",
        "golden_sets",
        ["corpus_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.add_column(
        "golden_pair",
        sa.Column("set_id", UUID, sa.ForeignKey("golden_sets.id", ondelete="CASCADE")),
    )
    op.alter_column("golden_pair", "expected_answer", nullable=True)
    for column in (
        sa.Column("expected_behaviour", sa.String(12)),
        sa.Column("expected_label", sa.String(32)),
        sa.Column(
            "must_include",
            postgresql.ARRAY(sa.String(200)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("must_cite", sa.String(500)),
        sa.Column("history", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("language", sa.String(16)),
        sa.Column("page_url", sa.String(2048)),
        sa.Column("office_hours", sa.String(6)),
        sa.Column("origin", sa.String(16), nullable=False, server_default=sa.text("'human'")),
        sa.Column("difficulty", sa.String(8)),
        sa.Column("topic", sa.String(60)),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
    ):
        op.add_column("golden_pair", column)
    op.execute(
        sa.text(
            """INSERT INTO golden_sets (corpus_id, name, source, is_default)
               SELECT DISTINCT corpus_id, 'Golden questions', 'owner', true FROM golden_pair"""
        )
    )
    op.execute(
        sa.text(
            """UPDATE golden_pair p SET set_id = s.id FROM golden_sets s
               WHERE s.corpus_id = p.corpus_id AND s.is_default"""
        )
    )
    op.execute(
        sa.text(
            "UPDATE golden_pair SET origin = "
            "CASE WHEN source = 'feedback' THEN 'from_feedback' ELSE 'human' END"
        )
    )
    op.alter_column("golden_pair", "set_id", nullable=False)
    op.create_index("ix_golden_pair_set_id", "golden_pair", ["set_id"])
    op.create_check_constraint(
        "ck_golden_pair_expectation",
        "golden_pair",
        "expected_answer IS NOT NULL OR expected_behaviour IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_golden_pair_behaviour",
        "golden_pair",
        "expected_behaviour IS NULL OR expected_behaviour IN "
        "('answer', 'decline', 'handoff', 'lead', 'smalltalk', 'no_answer')",
    )
    op.create_check_constraint(
        "ck_golden_pair_origin",
        "golden_pair",
        "origin IN ('human', 'ai_generated', 'from_feedback')",
    )
    op.create_check_constraint(
        "ck_golden_pair_difficulty",
        "golden_pair",
        "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
    )
    op.create_check_constraint(
        "ck_golden_pair_office_hours",
        "golden_pair",
        "office_hours IS NULL OR office_hours IN ('open', 'closed')",
    )

    for column in (
        sa.Column("name", sa.String(120)),
        sa.Column("kind", sa.String(10), nullable=False, server_default=sa.text("'traffic'")),
        sa.Column("set_id", UUID, sa.ForeignKey("golden_sets.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(4)),
        sa.Column("target", sa.String(12)),
        sa.Column("config_version", sa.Integer),
        sa.Column("judge_model", sa.String(200)),
        sa.Column("thresholds", JSONB),
        sa.Column("criteria", JSONB),
        sa.Column("totals", JSONB),
        sa.Column("estimated_cost_usd", sa.Float),
        sa.Column("actual_cost_usd", sa.Float),
        sa.Column("actual_cost_known", sa.Boolean),
        sa.Column("created_by", sa.String(320)),
    ):
        op.add_column("eval_run", column)
    op.create_index("ix_eval_run_set_id", "eval_run", ["set_id"])
    op.create_check_constraint("ck_eval_run_kind", "eval_run", "kind IN ('traffic', 'golden')")
    op.create_check_constraint(
        "ck_eval_run_mode", "eval_run", "mode IS NULL OR mode IN ('fast', 'full')"
    )

    op.create_table(
        "eval_run_samples",
        sa.Column("id", UUID, primary_key=True, server_default=GEN),
        sa.Column("run_id", UUID, sa.ForeignKey("eval_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("golden_pair_id", UUID, sa.ForeignKey("golden_pair.id", ondelete="SET NULL")),
        sa.Column("query_log_id", UUID, sa.ForeignKey("query_log.id", ondelete="SET NULL")),
        sa.Column("question", sa.Text, nullable=False),
        sa.Column("expected_answer", sa.Text),
        sa.Column("answer", sa.Text),
        sa.Column("contexts", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("scores", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("reasons", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("behaviour", JSONB),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("passed", sa.Boolean),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("cost_usd", sa.Float),
        sa.Column("rating", sa.String(10)),
        sa.Column("rated_by", sa.String(320)),
        sa.Column("error", sa.Text),
        sa.Column("position", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.CheckConstraint(
            "rating IS NULL OR rating IN ('good', 'acceptable', 'poor')",
            name="ck_eval_samples_rating",
        ),
    )
    op.create_index("ix_eval_run_samples_run_id", "eval_run_samples", ["run_id"])

    op.create_table(
        "eval_criteria",
        sa.Column("id", UUID, primary_key=True, server_default=GEN),
        sa.Column(
            "corpus_id", UUID, sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("definition", sa.String(500), nullable=False),
        sa.Column("threshold", sa.Float, nullable=False, server_default=sa.text("0.7")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.UniqueConstraint("corpus_id", "name", name="uq_eval_criteria_name"),
        sa.CheckConstraint("threshold >= 0 AND threshold <= 1", name="ck_eval_criteria_threshold"),
    )
    op.create_index("ix_eval_criteria_corpus_id", "eval_criteria", ["corpus_id"])

    op.create_table(
        "eval_settings",
        sa.Column(
            "corpus_id", UUID, sa.ForeignKey("corpora.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("judge_provider", sa.String(64)),
        sa.Column("judge_model", sa.String(200)),
        sa.Column("judge_api_key_encrypted", sa.Text),
        sa.Column("judge_base_url", sa.String(500)),
        sa.Column("thresholds", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW),
    )

    op.create_table(
        "assistant_review_actions",
        sa.Column("id", UUID, primary_key=True, server_default=GEN),
        sa.Column(
            "corpus_id", UUID, sa.ForeignKey("corpora.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("target", sa.String(200)),
        sa.Column("created_by", sa.String(320)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW),
        sa.CheckConstraint(
            "action IN ('dismissed', 'added_document', 'added_answer', 'added_rule')",
            name="ck_review_actions_action",
        ),
        sa.CheckConstraint(
            "kind IN ('no_answer', 'low_confidence', 'thumbs_down', 'handoff')",
            name="ck_review_actions_kind",
        ),
    )
    op.create_index(
        "ix_review_actions_corpus_key", "assistant_review_actions", ["corpus_id", "item_key"]
    )


def downgrade() -> None:
    op.drop_table("assistant_review_actions")
    op.drop_table("eval_settings")
    op.drop_table("eval_criteria")
    op.drop_table("eval_run_samples")
    op.drop_constraint("ck_eval_run_mode", "eval_run", type_="check")
    op.drop_constraint("ck_eval_run_kind", "eval_run", type_="check")
    op.drop_index("ix_eval_run_set_id", table_name="eval_run")
    for name in (
        "created_by",
        "actual_cost_known",
        "actual_cost_usd",
        "estimated_cost_usd",
        "totals",
        "criteria",
        "thresholds",
        "judge_model",
        "config_version",
        "target",
        "mode",
        "set_id",
        "kind",
        "name",
    ):
        op.drop_column("eval_run", name)
    for name in (
        "ck_golden_pair_office_hours",
        "ck_golden_pair_difficulty",
        "ck_golden_pair_origin",
        "ck_golden_pair_behaviour",
        "ck_golden_pair_expectation",
    ):
        op.drop_constraint(name, "golden_pair", type_="check")
    op.execute(sa.text("DELETE FROM golden_pair WHERE expected_answer IS NULL"))
    op.alter_column("golden_pair", "expected_answer", nullable=False)
    op.drop_index("ix_golden_pair_set_id", table_name="golden_pair")
    for name in (
        "position",
        "topic",
        "difficulty",
        "origin",
        "office_hours",
        "page_url",
        "language",
        "history",
        "must_cite",
        "must_include",
        "expected_label",
        "expected_behaviour",
        "set_id",
    ):
        op.drop_column("golden_pair", name)
    op.drop_table("golden_sets")
