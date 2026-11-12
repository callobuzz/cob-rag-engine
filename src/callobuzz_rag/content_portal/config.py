"""Content Portal configuration helpers.

Resolves portal-specific settings using the 3-tier config pattern:
per-corpus config JSONB → global Settings → hardcoded defaults.
"""

import logging
from typing import Any

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

# Default portal config values (tier 3: hardcoded fallbacks)
PORTAL_DEFAULTS: dict[str, Any] = {
    "portal_workflow": "single_reviewer",
    "portal_auto_stage": False,
    "portal_routing_rules": [],
    "portal_ai_model": "",
    "portal_post_ingest_review": False,
}

# Keys in the corpus JSONB that are portal-specific
PORTAL_CONFIG_KEYS: set[str] = {
    "portal_workflow",
    "portal_auto_stage",
    "portal_routing_rules",
    "portal_ai_model",
    "portal_post_ingest_review",
}


def resolve_portal_config(
    corpus_config: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any]:
    """Resolve portal configuration using 3-tier fallback.

    Priority: corpus JSONB → global settings → hardcoded defaults.

    Args:
        corpus_config: Per-corpus config JSONB (may be None).
        settings: Global application settings.

    Returns:
        Resolved portal configuration dict.
    """
    resolved: dict[str, Any] = {}
    corpus = corpus_config or {}

    # portal_workflow: corpus → global → default
    resolved["portal_workflow"] = corpus.get(
        "portal_workflow",
        settings.portal_default_workflow,
    )

    # portal_auto_stage: corpus → default (no global equivalent)
    resolved["portal_auto_stage"] = corpus.get(
        "portal_auto_stage",
        PORTAL_DEFAULTS["portal_auto_stage"],
    )

    # portal_routing_rules: corpus only (no global equivalent)
    resolved["portal_routing_rules"] = corpus.get(
        "portal_routing_rules",
        list(PORTAL_DEFAULTS["portal_routing_rules"]),
    )

    # portal_ai_model: corpus → global → default
    resolved["portal_ai_model"] = corpus.get(
        "portal_ai_model",
        settings.portal_ai_default_model,
    )

    # portal_post_ingest_review: corpus → global → default
    resolved["portal_post_ingest_review"] = corpus.get(
        "portal_post_ingest_review",
        settings.portal_auto_review_after_ingest,
    )

    return resolved


def get_portal_ai_model(
    corpus_config: dict[str, Any] | None,
    settings: Settings,
) -> str:
    """Resolve which LLM model to use for portal AI tasks.

    Falls back to the corpus llm_config model, then the global llm_model.

    Args:
        corpus_config: Per-corpus config JSONB (may be None).
        settings: Global application settings.

    Returns:
        LiteLLM model string (e.g. "anthropic/claude-sonnet-4-20250514").
    """
    corpus = corpus_config or {}

    # 1. Explicit portal AI model
    portal_model = corpus.get("portal_ai_model", "")
    if portal_model:
        return str(portal_model)

    # 2. Global portal AI default
    if settings.portal_ai_default_model:
        return settings.portal_ai_default_model

    # 3. Corpus LLM config model
    llm_config = corpus.get("llm_config", {})
    if isinstance(llm_config, dict) and llm_config.get("model"):
        return str(llm_config["model"])

    # 4. Global LLM model
    return settings.llm_model


def is_portal_enabled(settings: Settings) -> bool:
    """Check if the Content Portal feature is enabled.

    Args:
        settings: Global application settings.

    Returns:
        True if RAG_ENABLE_CONTENT_PORTAL is true.
    """
    return settings.enable_content_portal
