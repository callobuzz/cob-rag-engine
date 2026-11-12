"""Whisper API transcription provider -- uses OpenAI's hosted Whisper."""

import logging

import httpx

from callobuzz_rag.core.exceptions import TranscriptionError
from callobuzz_rag.transcription.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)

OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
MAX_FILE_SIZE = 25 * 1024 * 1024  # 25 MB


class WhisperAPIProvider(TranscriptionProvider):
    """Transcription via OpenAI Whisper API."""

    def __init__(
        self,
        api_key: str,
        model: str = "whisper-1",
        timeout: float = 120.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    async def transcribe(
        self,
        file_bytes: bytes,
        file_name: str,
        language: str | None = None,
    ) -> TranscriptionResult:
        if len(file_bytes) > MAX_FILE_SIZE:
            raise TranscriptionError(
                f"File size {len(file_bytes)} exceeds Whisper API limit of {MAX_FILE_SIZE} bytes"
            )

        data: dict[str, str] = {
            "model": self._model,
            "response_format": "verbose_json",
        }
        if language:
            data["language"] = language

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    OPENAI_TRANSCRIPTION_URL,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    files={"file": (file_name, file_bytes)},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TranscriptionError(
                f"Whisper API returned HTTP {exc.response.status_code}: {exc}"
            ) from exc
        except Exception as exc:
            raise TranscriptionError(f"Whisper API call failed: {exc}") from exc

        try:
            body = response.json()
        except Exception as exc:
            raise TranscriptionError(f"Whisper API returned invalid JSON: {exc}") from exc
        segments = [
            TranscriptionSegment(
                start=s.get("start", 0.0),
                end=s.get("end", 0.0),
                text=s.get("text", ""),
            )
            for s in body.get("segments", [])
        ]
        return TranscriptionResult(
            text=body.get("text", ""),
            segments=segments,
            language_detected=body.get("language"),
            duration_seconds=body.get("duration", 0.0),
        )

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
                return resp.status_code == 200
        except Exception:
            return False
