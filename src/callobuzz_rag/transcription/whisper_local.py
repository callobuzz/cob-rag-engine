"""Whisper Local transcription provider — calls a self-hosted Whisper ASR service."""

import logging

import httpx

from callobuzz_rag.core.exceptions import TranscriptionError
from callobuzz_rag.transcription.base import (
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = logging.getLogger(__name__)


class WhisperLocalProvider(TranscriptionProvider):
    """Transcription via a local Whisper ASR webservice (Docker sidecar).

    Compatible with: onerahmet/openai-whisper-asr-webservice
    API: POST /asr?output=json&task=transcribe
    """

    def __init__(
        self,
        base_url: str = "http://whisper:9000",
        model: str = "base",
        timeout: float = 300.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout

    async def transcribe(
        self,
        file_bytes: bytes,
        file_name: str,
        language: str | None = None,
    ) -> TranscriptionResult:
        params: dict[str, str] = {
            "output": "json",
            "task": "transcribe",
            "word_timestamps": "true",
            "model": self._model,
        }
        if language:
            params["language"] = language

        files = {"audio_file": (file_name, file_bytes)}

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base_url}/asr",
                    params=params,
                    files=files,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TranscriptionError(
                f"Whisper Local returned HTTP {exc.response.status_code}: {exc}"
            ) from exc
        except Exception as exc:
            raise TranscriptionError(f"Whisper Local call failed: {exc}") from exc

        try:
            body = response.json()
        except Exception as exc:
            raise TranscriptionError(f"Whisper Local returned invalid JSON: {exc}") from exc

        segments = [
            TranscriptionSegment(
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", ""),
            )
            for seg in body.get("segments", [])
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
                resp = await client.get(f"{self._base_url}/")
                return resp.status_code == 200
        except Exception:
            return False
