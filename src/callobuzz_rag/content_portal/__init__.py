"""Content Portal — AI-powered pre-ingestion content structuring.

This module is feature-flagged behind RAG_ENABLE_CONTENT_PORTAL.
When disabled, no portal routes, models, or services are loaded.

Architecture: one-way dependency — portal imports engine core,
engine core never imports portal.
"""

import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)


def get_portal_router() -> APIRouter | None:
    """Return the portal router if the feature flag is enabled.

    Uses deferred import to avoid loading portal modules when disabled.
    Called by create_app() during application startup.

    Returns:
        APIRouter with all portal routes, or None if disabled.
    """
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()

    if not settings.enable_content_portal:
        logger.debug("Content Portal disabled (RAG_ENABLE_CONTENT_PORTAL=false)")
        return None

    logger.info("Content Portal enabled — registering portal routes")

    # Deferred import: only load portal routes when feature is on
    from callobuzz_rag.content_portal.routes import portal_router

    return portal_router
