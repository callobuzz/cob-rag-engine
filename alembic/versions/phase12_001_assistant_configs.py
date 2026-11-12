"""Assistant configurations (conversation layer C2), on for every knowledge base.

Creates `assistant_configs` (one published row and at most one draft per corpus; archived
history; partial unique indexes `uq_assistant_configs_published` / `uq_assistant_configs_draft`).

Data step (SV 2026-10-02: nothing is public yet, so the assistant is switched on everywhere):
every existing corpus gets a published version 1 built from a FROZEN copy of the
"Customer support" (`generic_support`) template defaults below, `enabled=true`. Frozen so this
migration never changes when the template file does. A corpus whose `prompt_config` has
`system_prompt` and/or `constraints` keeps them, unshortened, as `advanced.extra_instructions`
(joined by a blank line); its `response_language` becomes the fixed reply language when it is
one of the assistant's language keys, else a line "Always reply in <value>." is appended.
When no `RAG_ENCRYPTION_KEY` is configured for the process running the migration,
`leads.enabled` is set to false (leads are stored encrypted, and publishing with leads on
needs the key), consistent with new knowledge bases.

Downgrade drops the table (the corpora's prompt_config was never changed).

Revision ID: phase12_001
Revises: phase11_014
Create Date: 2026-10-02
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "phase12_001"
down_revision: str | None = "phase11_014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LANG_KEYS = ("en", "hi", "hi-Latn", "mr", "ta", "te", "bn", "gu", "kn", "ml", "pa")

# Frozen: the generic_support template's `config` as of conversation C2. Fields not listed
# take the schema's defaults when the store loads the row (load_config).
GENERIC_SUPPORT_V1: dict[str, Any] = {
    "schema_version": 1,
    "enabled": True,
    "identity": {
        "assistant_name": "Asha",
        "business_name": "",
        "business_type": "generic_support",
        "business_profile": "",
        "audience": "Customers and people interested in our products and services",
        "ai_disclosure": True,
    },
    "voice": {
        "tone_preset": "friendly",
        "formality": "neutral",
        "hindi_pronoun": "aap",
        "response_length": "medium",
        "format": {
            "lists": "prefer",
            "tables": "when_comparing",
            "headings": "never",
            "custom": "",
        },
        "emoji": "none",
    },
    "language": {
        "primary": "en",
        "supported": ["en", "hi", "hi-Latn"],
        "auto_detect": True,
        "reply_policy": "mirror",
        "script_policy": "mirror",
        "documents_language": "en",
        "translate_queries": True,
    },
    "scope": {
        "scope_mode": "docs_plus_domain",
        "topics": [
            {
                "id": "products",
                "name": "Products and services",
                "description": "What we offer and how it works",
                "examples": ["What services do you offer?", "Aap kya kya karte ho?"],
            },
            {
                "id": "pricing",
                "name": "Prices and payment",
                "description": "Prices, fees, payment methods, UPI",
                "examples": ["How much does it cost?", "Kitne ka hai?"],
            },
            {
                "id": "orders",
                "name": "Orders and bookings",
                "description": "Placing, changing or tracking an order or booking",
                "examples": ["How do I book?", "Where is my order?"],
            },
            {
                "id": "policies",
                "name": "Refunds and policies",
                "description": "Refunds, cancellations, warranties and terms",
                "examples": ["What is your refund policy?"],
            },
            {
                "id": "contact",
                "name": "Hours and contact",
                "description": "Opening hours, address, phone and WhatsApp",
                "examples": ["What are your timings?", "Aapka address kya hai?"],
            },
        ],
        "blocked_topics": [
            {
                "id": "competitors",
                "name": "Other companies",
                "description": "Comparisons with or opinions about other businesses",
                "action": "decline",
            }
        ],
        "sensitive_profile": "none",
        "claim_check": "enforce",
        "max_off_topic_strikes": 3,
    },
    "rules": [
        {
            "id": "one-question",
            "type": "clarification",
            "instruction": "If a question could mean two different things, ask one short question "
            "to check before answering.",
            "priority": 60,
            "source": "template",
        },
        {
            "id": "steps-as-list",
            "type": "communication",
            "instruction": "When explaining how to do something, give numbered steps.",
            "priority": 50,
            "source": "template",
        },
        {
            "id": "price-source",
            "type": "compliance",
            "when": {"topic": "pricing"},
            "instruction": "Quote prices exactly as written in the documents, with the ₹ sign, and "
            "say if taxes or charges are extra when the documents say so.",
            "priority": 80,
            "source": "template",
        },
        {
            "id": "refund-handoff",
            "type": "escalation",
            "when": {"topic": "policies"},
            "instruction": "For a refund or cancellation request about a specific order, explain "
            "the policy and offer to connect the visitor to the team.",
            "priority": 70,
            "source": "template",
        },
        {
            "id": "order-details",
            "type": "context",
            "when": {"topic": "orders"},
            "instruction": "You cannot see individual orders. Explain how the visitor can check "
            "theirs and offer to connect them to the team.",
            "priority": 70,
            "source": "template",
        },
        {
            "id": "buying-next-step",
            "type": "sales",
            "when": {"label": "buying_intent"},
            "instruction": "After answering, suggest one clear next step (book, order, or leave a "
            "number for a call back).",
            "priority": 50,
            "source": "template",
        },
        {
            "id": "closed-hours",
            "type": "context",
            "when": {"office_hours": "closed"},
            "instruction": "If the visitor wants to talk to someone, mention that the team is "
            "closed now and when it opens.",
            "priority": 40,
            "source": "template",
        },
        {
            "id": "no-jargon",
            "type": "communication",
            "instruction": "Use plain words; explain any technical term in a few words.",
            "priority": 30,
            "source": "template",
        },
    ],
    "conversation": {
        "starters": [
            {"label": "Services", "message": "What services do you offer?"},
            {"label": "Prices", "message": "What are your prices?"},
            {"label": "Timings", "message": "What are your opening hours?"},
            {"label": "Talk to us", "message": "I'd like to talk to someone"},
        ],
        "ai_followups": True,
        "cta": {"max_per_answer": 1, "buying_intent_cta": "lead_form"},
    },
    "leads": {
        "enabled": True,
        "mode": "form",
        "triggers": {
            "buying_intent": True,
            "no_answer": True,
            "topics": ["pricing"],
            "human_request": False,
            "out_of_hours": True,
        },
        "name": "optional",
        "phone": "required",
        "email": "optional",
        "custom_fields": [
            {
                "id": "requirement",
                "label": {
                    "en": "What do you need?",
                    "hi": "आपको क्या चाहिए?",
                    "hi-Latn": "Aapko kya chahiye?",
                },
                "type": "text",
                "required": False,
                "max_length": 200,
            }
        ],
    },
    "handoff": {"enabled": True},
    "advanced": {},
}


def _encryption_configured() -> bool:
    """True when RAG_ENCRYPTION_KEY holds at least one key (the app's own settings)."""
    try:
        from callobuzz_rag.core.config import Settings
        from callobuzz_rag.core.secrets import parse_key_list

        return bool(parse_key_list(Settings().encryption_key))
    except Exception:
        return False


def _legacy_config(prompt_config: Any) -> dict[str, Any]:
    """The frozen defaults plus whatever the corpus's old prompt_config carried."""
    config: dict[str, Any] = json.loads(json.dumps(GENERIC_SUPPORT_V1))
    if isinstance(prompt_config, str):
        try:
            prompt_config = json.loads(prompt_config)
        except ValueError:
            prompt_config = None
    if not isinstance(prompt_config, dict):
        return config
    extra = [
        str(prompt_config[key]).strip()
        for key in ("system_prompt", "constraints")
        if prompt_config.get(key) and str(prompt_config[key]).strip()
    ]
    language = str(prompt_config.get("response_language") or "").strip()
    if language in LANG_KEYS:
        policy = config["language"]
        policy["primary"] = language
        policy["reply_policy"] = "fixed"
        if language not in policy["supported"]:
            policy["supported"] = [*policy["supported"], language]
    elif language:
        extra.append(f"Always reply in {language}.")
    if extra:
        config["advanced"] = {"extra_instructions": "\n\n".join(extra)}
    return config


def upgrade() -> None:
    op.create_table(
        "assistant_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "corpus_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("corpora.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_label", sa.String(320), nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("restored_from", sa.Integer(), nullable=True),
        sa.UniqueConstraint("corpus_id", "version", name="uq_assistant_configs_corpus_version"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_assistant_configs_status"
        ),
        sa.CheckConstraint(
            "(status = 'draft') = (version IS NULL)", name="ck_assistant_configs_draft_version"
        ),
    )
    op.create_index("ix_assistant_configs_corpus_id", "assistant_configs", ["corpus_id"])
    op.create_index(
        "uq_assistant_configs_published",
        "assistant_configs",
        ["corpus_id"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "uq_assistant_configs_draft",
        "assistant_configs",
        ["corpus_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    bind = op.get_bind()
    leads_allowed = _encryption_configured()
    for corpus_id, prompt_config in bind.execute(
        sa.text("SELECT id, prompt_config FROM corpora")
    ).all():
        config = _legacy_config(prompt_config)
        if not leads_allowed:
            config["leads"]["enabled"] = False
        bind.execute(
            sa.text(
                "INSERT INTO assistant_configs (corpus_id, version, status, config, "
                "schema_version, created_by, published_at, published_by_label, note) "
                "VALUES (:c, 1, 'published', CAST(:cfg AS jsonb), 1, 'migration phase12_001', "
                "now(), 'migration phase12_001', "
                "'Customer support template (switched on for every knowledge base)')"
            ),
            {"c": corpus_id, "cfg": json.dumps(config, ensure_ascii=False)},
        )


def downgrade() -> None:
    op.drop_index("uq_assistant_configs_draft", table_name="assistant_configs")
    op.drop_index("uq_assistant_configs_published", table_name="assistant_configs")
    op.drop_index("ix_assistant_configs_corpus_id", table_name="assistant_configs")
    op.drop_table("assistant_configs")
