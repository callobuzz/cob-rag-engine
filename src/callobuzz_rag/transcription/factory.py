"""Factory function for transcription providers."""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.transcription.base import TranscriptionProvider

logger = logging.getLogger(__name__)


def create_transcription_provider(
    settings: "Settings",
) -> "TranscriptionProvider | None":
    """Instantiate the configured transcription provider, or None if disabled."""
    provider = settings.transcription_provider

    if provider == "off":
        return None

    if provider == "whisper_api":
        from callobuzz_rag.transcription.whisper_api import WhisperAPIProvider

        api_key = settings.whisper_api_key.get_secret_value()
        if not api_key:
            logger.warning("Whisper API key not set -- transcription disabled")
            return None
        return WhisperAPIProvider(
            api_key=api_key,
            model=settings.whisper_model,
        )

    if provider == "whisper_local":
        try:
            from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider
        except ImportError:
            logger.warning("whisper_local provider not available (see epic 7-03)")
            return None
        return WhisperLocalProvider(
            base_url=settings.whisper_local_url,
            model=settings.whisper_local_model,
        )

    logger.warning(
        "Unknown transcription provider %r -- transcription disabled",
        provider,
    )
    return None
