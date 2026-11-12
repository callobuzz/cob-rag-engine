"""Tests for the transcription provider module (Epic 7-02)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.core.exceptions import TranscriptionError
from callobuzz_rag.transcription.base import (
    AUDIO_MIME_TYPES,
    TranscriptionProvider,
    TranscriptionResult,
    TranscriptionSegment,
)
from callobuzz_rag.transcription.factory import create_transcription_provider
from callobuzz_rag.transcription.whisper_api import (
    MAX_FILE_SIZE,
    WhisperAPIProvider,
)

# ---------------------------------------------------------------------------
# TranscriptionSegment
# ---------------------------------------------------------------------------


class TestTranscriptionSegment:
    def test_frozen_dataclass(self) -> None:
        seg = TranscriptionSegment(start=0.0, end=1.5, text="hello")
        assert seg.start == 0.0
        assert seg.end == 1.5
        assert seg.text == "hello"
        with pytest.raises(AttributeError):
            seg.text = "nope"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TranscriptionResult
# ---------------------------------------------------------------------------


class TestTranscriptionResult:
    def test_defaults_and_fields(self) -> None:
        result = TranscriptionResult(text="full text")
        assert result.text == "full text"
        assert result.segments == []
        assert result.language_detected is None
        assert result.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# TranscriptionProvider ABC
# ---------------------------------------------------------------------------


class _StubProvider(TranscriptionProvider):
    async def transcribe(
        self,
        file_bytes: bytes,
        file_name: str,
        language: str | None = None,
    ) -> TranscriptionResult:
        return TranscriptionResult(text="stub")

    async def health_check(self) -> bool:
        return True


class TestTranscriptionProviderABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError):
            TranscriptionProvider()  # type: ignore[abstract]

    def test_stub_instantiation(self) -> None:
        provider = _StubProvider()
        assert isinstance(provider, TranscriptionProvider)

    def test_supported_formats_default(self) -> None:
        provider = _StubProvider()
        assert provider.supported_formats == AUDIO_MIME_TYPES

    @pytest.mark.asyncio
    async def test_stub_transcribe(self) -> None:
        provider = _StubProvider()
        result = await provider.transcribe(b"data", "test.mp3")
        assert result.text == "stub"


# ---------------------------------------------------------------------------
# TranscriptionError
# ---------------------------------------------------------------------------


class TestTranscriptionError:
    def test_inherits_from_rag_kernel_error(self) -> None:
        from callobuzz_rag.core.exceptions import RAGKernelError

        err = TranscriptionError("boom")
        assert isinstance(err, RAGKernelError)
        assert str(err) == "boom"


# ---------------------------------------------------------------------------
# WhisperAPIProvider
# ---------------------------------------------------------------------------


class TestWhisperAPIProvider:
    def test_instantiation(self) -> None:
        provider = WhisperAPIProvider(api_key="sk-test", model="whisper-1")
        assert provider._api_key == "sk-test"
        assert provider._model == "whisper-1"
        assert isinstance(provider, TranscriptionProvider)

    @pytest.mark.asyncio
    async def test_transcribe_success(self) -> None:
        provider = WhisperAPIProvider(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Hello world",
            "language": "en",
            "duration": 5.2,
            "segments": [
                {"start": 0.0, "end": 2.5, "text": "Hello"},
                {"start": 2.5, "end": 5.2, "text": " world"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "callobuzz_rag.transcription.whisper_api.httpx.AsyncClient", return_value=mock_client
        ):
            result = await provider.transcribe(b"audio-bytes", "test.mp3", language="en")

        assert result.text == "Hello world"
        assert result.language_detected == "en"
        assert result.duration_seconds == 5.2
        assert len(result.segments) == 2
        assert result.segments[0].text == "Hello"
        assert result.segments[1].end == 5.2

    @pytest.mark.asyncio
    async def test_transcribe_api_error(self) -> None:
        provider = WhisperAPIProvider(api_key="sk-test")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "callobuzz_rag.transcription.whisper_api.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(TranscriptionError, match="Whisper API call failed"),
        ):
            await provider.transcribe(b"audio", "test.mp3")

    @pytest.mark.asyncio
    async def test_transcribe_file_too_large(self) -> None:
        provider = WhisperAPIProvider(api_key="sk-test")
        oversized = b"x" * (MAX_FILE_SIZE + 1)
        with pytest.raises(TranscriptionError, match="exceeds Whisper API limit"):
            await provider.transcribe(oversized, "big.mp3")

    @pytest.mark.asyncio
    async def test_health_check(self) -> None:
        provider = WhisperAPIProvider(api_key="sk-test")
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "callobuzz_rag.transcription.whisper_api.httpx.AsyncClient", return_value=mock_client
        ):
            result = await provider.health_check()

        assert result is True


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestTranscriptionFactory:
    def _make_settings(self, **overrides) -> MagicMock:  # type: ignore[no-untyped-def]
        settings = MagicMock()
        settings.transcription_provider = overrides.get("transcription_provider", "off")
        settings.whisper_api_key = MagicMock()
        settings.whisper_api_key.get_secret_value.return_value = overrides.get(
            "whisper_api_key", ""
        )
        settings.whisper_model = overrides.get("whisper_model", "whisper-1")
        settings.whisper_local_url = overrides.get("whisper_local_url", "http://whisper:9000")
        settings.whisper_local_model = overrides.get("whisper_local_model", "base")
        return settings

    def test_off_returns_none(self) -> None:
        settings = self._make_settings(transcription_provider="off")
        assert create_transcription_provider(settings) is None

    def test_whisper_api_no_key_returns_none(self) -> None:
        settings = self._make_settings(transcription_provider="whisper_api", whisper_api_key="")
        assert create_transcription_provider(settings) is None

    def test_whisper_api_with_key(self) -> None:
        settings = self._make_settings(
            transcription_provider="whisper_api", whisper_api_key="sk-real"
        )
        provider = create_transcription_provider(settings)
        assert isinstance(provider, WhisperAPIProvider)
        assert provider._api_key == "sk-real"

    def test_unknown_provider_returns_none(self) -> None:
        settings = self._make_settings(transcription_provider="banana")
        assert create_transcription_provider(settings) is None


# ---------------------------------------------------------------------------
# WhisperLocalProvider
# ---------------------------------------------------------------------------


class TestWhisperLocalProvider:
    def test_instantiation(self) -> None:
        from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider

        provider = WhisperLocalProvider(
            base_url="http://localhost:9000",
            model="base",
        )
        assert provider._base_url == "http://localhost:9000"
        assert provider._model == "base"
        assert isinstance(provider, TranscriptionProvider)

    @pytest.mark.asyncio
    async def test_transcribe_success(self) -> None:
        from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider

        provider = WhisperLocalProvider(base_url="http://localhost:9000", model="base")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Hello from local whisper",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "Hello from local whisper"},
            ],
            "language": "en",
            "duration": 2.0,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "callobuzz_rag.transcription.whisper_local.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.transcribe(b"fake-audio", "test.mp3")

        assert result.text == "Hello from local whisper"
        assert len(result.segments) == 1
        assert result.language_detected == "en"
        assert result.duration_seconds == 2.0

    @pytest.mark.asyncio
    async def test_transcribe_api_error(self) -> None:
        from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider

        provider = WhisperLocalProvider(base_url="http://localhost:9000", model="base")
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "callobuzz_rag.transcription.whisper_local.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(TranscriptionError, match="Whisper Local call failed"),
        ):
            await provider.transcribe(b"fake-audio", "test.mp3")

    @pytest.mark.asyncio
    async def test_health_check_false_when_unreachable(self) -> None:
        from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider

        provider = WhisperLocalProvider(base_url="http://localhost:9999", model="base")
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "callobuzz_rag.transcription.whisper_local.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await provider.health_check()
        assert result is False

    def test_factory_creates_local_provider(self) -> None:
        from callobuzz_rag.transcription.whisper_local import WhisperLocalProvider

        settings = MagicMock()
        settings.transcription_provider = "whisper_local"
        settings.whisper_local_url = "http://localhost:9000"
        settings.whisper_local_model = "base"
        provider = create_transcription_provider(settings)
        assert isinstance(provider, WhisperLocalProvider)
