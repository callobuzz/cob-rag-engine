"""Factory function for web search providers."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.websearch.base import WebSearchProvider

logger = logging.getLogger(__name__)


def create_web_search_provider(settings: "Settings") -> "WebSearchProvider | None":
    provider = settings.web_search_provider
    if provider == "off":
        return None

    api_key = settings.web_search_api_key.get_secret_value()
    if not api_key:
        logger.warning("Web search API key not set — web search disabled")
        return None

    if provider == "serper":
        from callobuzz_rag.websearch.serper import SerperProvider

        return SerperProvider(api_key=api_key)

    if provider == "brave":
        from callobuzz_rag.websearch.brave import BraveProvider

        return BraveProvider(api_key=api_key)

    logger.warning("Unknown web search provider %r — disabled", provider)
    return None
