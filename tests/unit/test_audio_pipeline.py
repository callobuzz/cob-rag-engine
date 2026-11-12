"""Unit tests for audio transcription pipeline integration (Epic 7-04)."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.chunking.chunker import ChunkerConfig, StructureAwareChunker
from callobuzz_rag.embeddings.base import EmbeddingResult
from callobuzz_rag.enrichment.models import EnrichmentResult
from callobuzz_rag.parsing.models import (
    ElementType,
    ParsedDocument,
    ParsedElement,
)
from callobuzz_rag.pipeline.models import JobStatus
from callobuzz_rag.pipeline.orchestrator import IngestionOrchestrator
from callobuzz_rag.transcription.base import (
    AUDIO_MIME_TYPES,
    TranscriptionResult,
    TranscriptionSegment,
)

CORPUS_ID = uuid.uuid4()
AUDIO_BYTES = b"\xff\xfb\x90\x00" * 100  # fake MP3 header bytes
AUDIO_FILE = "meeting.mp3"
AUDIO_MIME = "audio/mpeg"

HTML_BYTES = b"<html><body><p>Hello</p></body></html>"
HTML_FILE = "doc.html"
HTML_MIME = "text/html"


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_session():
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.add = MagicMock()
    session.add_all = MagicMock()
    return session


@pytest.fixture
def mock_storage():
    storage = AsyncMock()
    storage.upload = AsyncMock()
    return storage


@pytest.fixture
def mock_parser():
    parser = AsyncMock()
    parser.parse = AsyncMock(
        return_value=ParsedDocument(
            elements=[
                ParsedElement(type=ElementType.PARAGRAPH, text="Hello world."),
            ],
            source_filename="doc.html",
            parser_name="docling",
            parser_version="2.75.0",
        )
    )
    return parser


@pytest.fixture
def chunker():
    return StructureAwareChunker(ChunkerConfig(min_tokens=1, overlap_tokens=0))


@pytest.fixture
def mock_enricher():
    enricher = AsyncMock()
    enricher.enrich_batch = AsyncMock(
        side_effect=lambda chunks: [
            EnrichmentResult(
                summary="Summary",
                keywords=["test"],
                language="en",
                confidence_score=0.85,
            )
            for _ in chunks
        ]
    )
    return enricher


@pytest.fixture
def mock_embedder():
    embedder = AsyncMock()
    embedder.embed_texts = AsyncMock(
        side_effect=lambda texts, **kwargs: [
            EmbeddingResult(
                vector=[0.1] * 1024,
                model="jina-embeddings-v3",
                model_version="jina-embeddings-v3",
                dimensions=1024,
            )
            for _ in texts
        ]
    )
    return embedder


@pytest.fixture
def mock_vector_store():
    vs = AsyncMock()
    vs.upsert = AsyncMock()
    vs.search = AsyncMock(return_value=[])
    return vs


@pytest.fixture
def mock_transcriber():
    transcriber = AsyncMock()
    transcriber.transcribe = AsyncMock(
        return_value=TranscriptionResult(
            text="Hello from audio. This is a test recording.",
            segments=[
                TranscriptionSegment(start=0.0, end=2.5, text="Hello from audio."),
                TranscriptionSegment(start=2.5, end=5.0, text="This is a test recording."),
            ],
            language_detected="en",
            duration_seconds=5.0,
        )
    )
    return transcriber


def _make_orchestrator(
    mock_session,
    mock_storage,
    mock_parser,
    chunker,
    mock_enricher,
    mock_embedder,
    mock_vector_store,
    transcriber=None,
):
    return IngestionOrchestrator(
        session=mock_session,
        storage=mock_storage,
        parser=mock_parser,
        chunker=chunker,
        enricher=mock_enricher,
        embedder=mock_embedder,
        vector_store=mock_vector_store,
        transcriber=transcriber,
    )


# ── Tests ─────────────────────────────────────────────────────────────


class TestAudioMimeTypes:
    """Audio MIME type detection."""

    def test_common_audio_types_present(self):
        assert "audio/mpeg" in AUDIO_MIME_TYPES
        assert "audio/wav" in AUDIO_MIME_TYPES
        assert "audio/mp4" in AUDIO_MIME_TYPES
        assert "audio/ogg" in AUDIO_MIME_TYPES
        assert "audio/flac" in AUDIO_MIME_TYPES
        assert "audio/webm" in AUDIO_MIME_TYPES

    def test_non_audio_not_in_set(self):
        assert "text/html" not in AUDIO_MIME_TYPES
        assert "application/pdf" not in AUDIO_MIME_TYPES


class TestOrchestratorAcceptsTranscriber:
    """Orchestrator constructor accepts optional transcriber."""

    def test_without_transcriber(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
        )
        assert orch._transcriber is None

    def test_with_transcriber(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
        mock_transcriber,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
            transcriber=mock_transcriber,
        )
        assert orch._transcriber is mock_transcriber


class TestAudioRouting:
    """Audio files route to transcriber; non-audio to parser."""

    @pytest.mark.asyncio
    async def test_audio_file_calls_transcriber(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
        mock_transcriber,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
            transcriber=mock_transcriber,
        )
        result = await orch.ingest(
            file_bytes=AUDIO_BYTES,
            file_name=AUDIO_FILE,
            mime_type=AUDIO_MIME,
            corpus_id=CORPUS_ID,
        )
        # Transcriber should be called, parser should NOT
        mock_transcriber.transcribe.assert_awaited_once_with(AUDIO_BYTES, AUDIO_FILE)
        mock_parser.parse.assert_not_awaited()
        assert result.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_non_audio_file_calls_parser(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
        mock_transcriber,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
            transcriber=mock_transcriber,
        )
        result = await orch.ingest(
            file_bytes=HTML_BYTES,
            file_name=HTML_FILE,
            mime_type=HTML_MIME,
            corpus_id=CORPUS_ID,
        )
        mock_parser.parse.assert_awaited_once()
        mock_transcriber.transcribe.assert_not_awaited()
        assert result.status == JobStatus.COMPLETED


class TestNoTranscriberRaisesError:
    """Audio file without transcriber configured raises TranscriptionError."""

    @pytest.mark.asyncio
    async def test_audio_no_transcriber_raises(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
            transcriber=None,
        )
        result = await orch.ingest(
            file_bytes=AUDIO_BYTES,
            file_name=AUDIO_FILE,
            mime_type=AUDIO_MIME,
            corpus_id=CORPUS_ID,
        )
        # Pipeline should fail gracefully with error in result
        assert result.status == JobStatus.FAILED
        assert "no provider configured" in (result.error or "")


class TestTranscriptToParsed:
    """_transcript_to_parsed produces correct ParsedDocument."""

    def test_with_segments(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
        )
        transcript = TranscriptionResult(
            text="Hello. World.",
            segments=[
                TranscriptionSegment(start=0.0, end=1.0, text="Hello."),
                TranscriptionSegment(start=1.0, end=2.0, text="World."),
            ],
            language_detected="en",
            duration_seconds=2.0,
        )
        parsed = orch._transcript_to_parsed(transcript, "test.mp3", "audio/mpeg")

        assert isinstance(parsed, ParsedDocument)
        assert len(parsed.elements) == 2
        assert "[0.0s - 1.0s] Hello." in parsed.elements[0].text
        assert "[1.0s - 2.0s] World." in parsed.elements[1].text
        assert parsed.elements[0].type == ElementType.PARAGRAPH
        assert parsed.source_filename == "test.mp3"
        assert parsed.parser_name == "transcription"

    def test_without_segments_uses_full_text(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
        )
        transcript = TranscriptionResult(
            text="Full transcript text without segments.",
            segments=[],
            language_detected="en",
            duration_seconds=3.0,
        )
        parsed = orch._transcript_to_parsed(transcript, "call.wav", "audio/wav")

        assert len(parsed.elements) == 1
        assert parsed.elements[0].text == "Full transcript text without segments."

    def test_metadata_on_elements(
        self,
        mock_session,
        mock_storage,
        mock_parser,
        chunker,
        mock_enricher,
        mock_embedder,
        mock_vector_store,
    ):
        orch = _make_orchestrator(
            mock_session,
            mock_storage,
            mock_parser,
            chunker,
            mock_enricher,
            mock_embedder,
            mock_vector_store,
        )
        transcript = TranscriptionResult(
            text="Audio text.",
            segments=[TranscriptionSegment(start=0.0, end=1.0, text="Audio text.")],
            language_detected="fr",
            duration_seconds=1.0,
        )
        parsed = orch._transcript_to_parsed(transcript, "fr.mp3", "audio/mpeg")
        meta = parsed.elements[0].metadata
        assert meta["source_type"] == "audio_transcription"
        assert meta["language_detected"] == "fr"
        assert meta["duration_seconds"] == 1.0
        assert meta["segment_count"] == 1


class TestWorkerCreatesTranscriptionProvider:
    """Worker _create_providers includes transcriber key."""

    def test_create_providers_includes_transcriber(self):
        mock_trans = MagicMock(return_value=MagicMock())
        with (
            patch(
                "callobuzz_rag.worker.tasks._create_embedding_provider",
                return_value=MagicMock(),
            ),
            patch(
                "callobuzz_rag.worker.tasks._create_transcription_provider",
                mock_trans,
            ),
            patch(
                "callobuzz_rag.storage.factory.object_storage",
                return_value=MagicMock(),
            ),
            patch(
                "callobuzz_rag.parsing.docling_parser.DoclingParserProvider.__init__",
                return_value=None,
            ),
            patch(
                "callobuzz_rag.chunking.chunker.chunker_from_settings",
                return_value=MagicMock(),
            ),
            patch(
                "callobuzz_rag.enrichment.extractive.ExtractiveEnrichmentProvider.__init__",
                return_value=None,
            ),
            patch(
                "callobuzz_rag.vectordb.qdrant.QdrantVectorProvider.__init__",
                return_value=None,
            ),
        ):
            from callobuzz_rag.worker.tasks import _create_providers

            settings = MagicMock()
            providers = _create_providers(settings)
            assert "transcriber" in providers
            mock_trans.assert_called_once_with(settings)
