"""Abstract base class for transcription providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class TranscriptionSegment:
    start: float  # seconds
    end: float  # seconds
    text: str


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: list[TranscriptionSegment] = field(default_factory=list)
    language_detected: str | None = None
    duration_seconds: float = 0.0


AUDIO_MIME_TYPES = frozenset(
    {
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/mp4",
        "audio/x-m4a",
        "audio/ogg",
        "audio/flac",
        "audio/webm",
    }
)

AUDIO_EXTENSIONS: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}


class TranscriptionProvider(ABC):
    """Abstract base class that all transcription providers must implement."""

    @abstractmethod
    async def transcribe(
        self,
        file_bytes: bytes,
        file_name: str,
        language: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe audio bytes into text with optional segments."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider backend is reachable."""
        ...

    @property
    def supported_formats(self) -> frozenset[str]:
        """MIME types this provider can handle."""
        return AUDIO_MIME_TYPES
