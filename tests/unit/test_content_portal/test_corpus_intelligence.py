"""Tests for Corpus Intelligence Service (CP-07)."""

import json
from dataclasses import dataclass, field
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from callobuzz_rag.content_portal.services.corpus_intelligence import (
    ContradictionReport,
    CorpusIntelligenceError,
    CorpusIntelligenceService,
    DuplicateReport,
    GapAnalysisReport,
    IntelligenceReport,
    _parse_contradiction_response,
    _parse_gap_response,
)
from callobuzz_rag.llm.models import LLMResponse, LLMUsage

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


@dataclass
class MockCitedChunk:
    """Mimics CitedChunk from context_assembly."""

    chunk_id: str
    text: str
    score: float
    document_id: str | None = None
    file_name: str | None = None

    @property
    def llm_text(self) -> str:
        return self.text


@dataclass
class MockAssembledContext:
    """Mimics AssembledContext from context_assembly."""

    cited_chunks: list[MockCitedChunk] = field(default_factory=list)
    messages: list[object] = field(default_factory=list)
    total_context_tokens: int = 0
    chunks_included: int = 0
    chunks_dropped: int = 0


@dataclass
class MockRetrievalResult:
    """Mimics _RetrievalPipelineResult from orchestrator."""

    assembled: MockAssembledContext = field(default_factory=MockAssembledContext)
    processed_query: str = ""
    plan: object = None
    top_results: list[object] = field(default_factory=list)


def _make_service(
    retrieval_result: MockRetrievalResult | None = None,
    retrieval_error: Exception | None = None,
    llm_response: LLMResponse | None = None,
    llm_error: Exception | None = None,
) -> CorpusIntelligenceService:
    """Create a CorpusIntelligenceService with mocked dependencies."""
    orchestrator = AsyncMock()
    if retrieval_error:
        orchestrator.run_retrieval_pipeline = AsyncMock(side_effect=retrieval_error)
    elif retrieval_result is not None:
        orchestrator.run_retrieval_pipeline = AsyncMock(return_value=retrieval_result)
    else:
        orchestrator.run_retrieval_pipeline = AsyncMock(return_value=MockRetrievalResult())

    llm = AsyncMock()
    if llm_error:
        llm.complete_json = AsyncMock(side_effect=llm_error)
    elif llm_response is not None:
        llm.complete_json = AsyncMock(return_value=llm_response)
    else:
        llm.complete_json = AsyncMock(return_value=LLMResponse(content="{}", model="test"))

    return CorpusIntelligenceService(orchestrator=orchestrator, llm=llm)


# ---------------------------------------------------------------------------
# TestDetectDuplicates
# ---------------------------------------------------------------------------


class TestDetectDuplicates:
    @pytest.mark.asyncio
    async def test_no_results_returns_empty_report(self) -> None:
        svc = _make_service()
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        assert isinstance(report, DuplicateReport)
        assert report.matches == []
        assert not report.has_duplicates
        assert not report.has_near_duplicates

    @pytest.mark.asyncio
    async def test_exact_duplicate_detected(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="exact match text",
                        score=0.95,
                    )
                ]
            )
        )
        svc = _make_service(retrieval_result=result)
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        assert len(report.matches) == 1
        assert report.matches[0].is_exact_duplicate
        assert report.has_duplicates
        assert not report.has_near_duplicates

    @pytest.mark.asyncio
    async def test_near_duplicate_detected(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="similar text",
                        score=0.85,
                    )
                ]
            )
        )
        svc = _make_service(retrieval_result=result)
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        assert len(report.matches) == 1
        assert not report.matches[0].is_exact_duplicate
        assert not report.has_duplicates
        assert report.has_near_duplicates

    @pytest.mark.asyncio
    async def test_below_threshold_filtered_out(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="low similarity",
                        score=0.50,
                    )
                ]
            )
        )
        svc = _make_service(retrieval_result=result)
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        assert report.matches == []

    @pytest.mark.asyncio
    async def test_retrieval_failure_returns_empty_report(self) -> None:
        svc = _make_service(retrieval_error=RuntimeError("connection failed"))
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        assert isinstance(report, DuplicateReport)
        assert report.matches == []

    @pytest.mark.asyncio
    async def test_custom_threshold(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="moderate match",
                        score=0.88,
                    )
                ]
            )
        )
        svc = _make_service(retrieval_result=result)
        chunk_id = uuid4()
        # Score 0.88 is below 0.90 threshold
        report = await svc.detect_duplicates("test text", chunk_id, uuid4(), threshold=0.90)
        assert report.matches == []
        # Score 0.88 is above 0.85 threshold
        report = await svc.detect_duplicates("test text", chunk_id, uuid4(), threshold=0.85)
        assert len(report.matches) == 1

    @pytest.mark.asyncio
    async def test_text_preview_truncated(self) -> None:
        svc = _make_service()
        chunk_id = uuid4()
        long_text = "a" * 500
        report = await svc.detect_duplicates(long_text, chunk_id, uuid4())
        assert len(report.staged_text_preview) == 200

    @pytest.mark.asyncio
    async def test_multiple_matches(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(chunk_id="c1", text="text1", score=0.95),
                    MockCitedChunk(chunk_id="c2", text="text2", score=0.85),
                    MockCitedChunk(chunk_id="c3", text="text3", score=0.70),
                ]
            )
        )
        svc = _make_service(retrieval_result=result)
        chunk_id = uuid4()
        report = await svc.detect_duplicates("test text", chunk_id, uuid4())
        # c1 (0.95) and c2 (0.85) are above default 0.80 threshold, c3 is not
        assert len(report.matches) == 2
        assert report.has_duplicates  # c1 is exact (>= 0.92)
        assert report.has_near_duplicates  # c2 is near


# ---------------------------------------------------------------------------
# TestDetectContradictions
# ---------------------------------------------------------------------------


class TestDetectContradictions:
    @pytest.mark.asyncio
    async def test_no_existing_content_returns_empty(self) -> None:
        svc = _make_service()
        chunk_id = uuid4()
        report = await svc.detect_contradictions("new text", chunk_id, uuid4())
        assert isinstance(report, ContradictionReport)
        assert report.contradictions == []
        assert not report.has_contradictions

    @pytest.mark.asyncio
    async def test_contradiction_detected(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="The sky is blue",
                        score=0.85,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps(
                {
                    "contradictions": [
                        {
                            "existing_chunk_id": "existing-1",
                            "explanation": "Color mismatch",
                            "severity": "high",
                            "confidence": 0.9,
                        }
                    ]
                }
            ),
            model="test",
            usage=LLMUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        chunk_id = uuid4()
        report = await svc.detect_contradictions("The sky is green", chunk_id, uuid4())
        assert report.has_contradictions
        assert len(report.contradictions) == 1
        assert report.contradictions[0].severity == "high"
        assert report.contradictions[0].confidence == 0.9
        assert report.token_usage is not None
        assert report.token_usage.total_tokens == 150

    @pytest.mark.asyncio
    async def test_no_contradictions_found(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="The sky is blue",
                        score=0.85,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps({"contradictions": []}),
            model="test",
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20, total_tokens=70),
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        chunk_id = uuid4()
        report = await svc.detect_contradictions("The sky is blue", chunk_id, uuid4())
        assert not report.has_contradictions
        assert report.contradictions == []

    @pytest.mark.asyncio
    async def test_llm_failure_raises_error(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[MockCitedChunk(chunk_id="existing-1", text="some text", score=0.85)]
            )
        )
        svc = _make_service(retrieval_result=result, llm_error=RuntimeError("API timeout"))
        chunk_id = uuid4()
        with pytest.raises(CorpusIntelligenceError, match="LLM call failed"):
            await svc.detect_contradictions("new text", chunk_id, uuid4())

    @pytest.mark.asyncio
    async def test_retrieval_failure_returns_empty(self) -> None:
        svc = _make_service(retrieval_error=RuntimeError("connection failed"))
        chunk_id = uuid4()
        report = await svc.detect_contradictions("new text", chunk_id, uuid4())
        assert isinstance(report, ContradictionReport)
        assert report.contradictions == []

    @pytest.mark.asyncio
    async def test_llm_called_with_correct_messages(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="existing content",
                        score=0.85,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps({"contradictions": []}),
            model="test",
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        chunk_id = uuid4()
        await svc.detect_contradictions("new text", chunk_id, uuid4())
        # Verify LLM was called with system + user messages
        call_args = svc._llm.complete_json.call_args
        messages = call_args[0][0]
        assert len(messages) == 2
        assert messages[0].role == "system"
        assert messages[1].role == "user"
        assert "new text" in messages[1].content
        assert "existing content" in messages[1].content


# ---------------------------------------------------------------------------
# TestAnalyzeGaps
# ---------------------------------------------------------------------------


class TestAnalyzeGaps:
    @pytest.mark.asyncio
    async def test_empty_chunks_returns_empty_report(self) -> None:
        svc = _make_service()
        workspace_id = uuid4()
        report = await svc.analyze_gaps([], workspace_id, uuid4())
        assert isinstance(report, GapAnalysisReport)
        assert report.new_topics == []
        assert not report.has_new_knowledge

    @pytest.mark.asyncio
    async def test_new_topics_detected(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="existing topic",
                        score=0.85,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps(
                {
                    "new_topics": [
                        {
                            "topic": "New Feature",
                            "description": "A brand new feature",
                            "confidence": 0.9,
                            "supporting_text": "Feature X does Y",
                        }
                    ],
                    "covered_topics": ["Existing Feature"],
                }
            ),
            model="test",
            usage=LLMUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        workspace_id = uuid4()
        report = await svc.analyze_gaps(["chunk about new feature"], workspace_id, uuid4())
        assert report.has_new_knowledge
        assert len(report.new_topics) == 1
        assert report.new_topics[0].topic == "New Feature"
        assert report.covered_topics == ["Existing Feature"]

    @pytest.mark.asyncio
    async def test_all_covered_no_new_topics(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="existing topic",
                        score=0.85,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps(
                {
                    "new_topics": [],
                    "covered_topics": ["Topic A", "Topic B"],
                }
            ),
            model="test",
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        workspace_id = uuid4()
        report = await svc.analyze_gaps(["chunk text"], workspace_id, uuid4())
        assert not report.has_new_knowledge
        assert report.covered_topics == ["Topic A", "Topic B"]

    @pytest.mark.asyncio
    async def test_llm_failure_raises_error(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[MockCitedChunk(chunk_id="existing-1", text="some text", score=0.85)]
            )
        )
        svc = _make_service(retrieval_result=result, llm_error=RuntimeError("API timeout"))
        workspace_id = uuid4()
        with pytest.raises(CorpusIntelligenceError, match="Gap analysis LLM call failed"):
            await svc.analyze_gaps(["chunk text"], workspace_id, uuid4())

    @pytest.mark.asyncio
    async def test_long_content_truncated(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[MockCitedChunk(chunk_id="existing-1", text="existing", score=0.85)]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps({"new_topics": [], "covered_topics": []}),
            model="test",
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        workspace_id = uuid4()
        long_texts = ["a" * 8000, "b" * 8000]  # Total > 15000
        await svc.analyze_gaps(long_texts, workspace_id, uuid4())
        # Verify the LLM was called (content was truncated internally)
        call_args = svc._llm.complete_json.call_args
        messages = call_args[0][0]
        user_content = messages[1].content
        assert "[... truncated ...]" in user_content


# ---------------------------------------------------------------------------
# TestFullAnalysis
# ---------------------------------------------------------------------------


class TestFullAnalysis:
    @pytest.mark.asyncio
    async def test_runs_all_analysis_types(self) -> None:
        result = MockRetrievalResult(
            assembled=MockAssembledContext(
                cited_chunks=[
                    MockCitedChunk(
                        chunk_id="existing-1",
                        text="existing text",
                        score=0.95,
                    )
                ]
            )
        )
        llm_resp = LLMResponse(
            content=json.dumps({"contradictions": []}),
            model="test",
            usage=LLMUsage(prompt_tokens=50, completion_tokens=20, total_tokens=70),
        )
        svc = _make_service(retrieval_result=result, llm_response=llm_resp)
        workspace_id = uuid4()
        corpus_id = uuid4()
        chunk_id = uuid4()
        report = await svc.full_analysis([(chunk_id, "chunk text")], workspace_id, corpus_id)
        assert isinstance(report, IntelligenceReport)
        assert len(report.duplicate_reports) == 1
        assert len(report.contradiction_reports) == 1
        assert report.gap_report is not None
        assert report.workspace_id == workspace_id
        assert report.corpus_id == corpus_id

    @pytest.mark.asyncio
    async def test_empty_chunks(self) -> None:
        svc = _make_service()
        workspace_id = uuid4()
        corpus_id = uuid4()
        report = await svc.full_analysis([], workspace_id, corpus_id)
        assert isinstance(report, IntelligenceReport)
        assert report.duplicate_reports == []
        assert report.contradiction_reports == []
        assert report.gap_report is not None
        assert report.total_duplicates == 0


# ---------------------------------------------------------------------------
# TestParseContradictionResponse
# ---------------------------------------------------------------------------


class TestParseContradictionResponse:
    def test_valid_json_parsed(self) -> None:
        existing = [MockCitedChunk(chunk_id="c1", text="old text", score=0.9)]
        content = json.dumps(
            {
                "contradictions": [
                    {
                        "existing_chunk_id": "c1",
                        "explanation": "Mismatch",
                        "severity": "high",
                        "confidence": 0.85,
                    }
                ]
            }
        )
        result = _parse_contradiction_response(content, "new text", existing)
        assert len(result) == 1
        assert result[0].existing_chunk_id == "c1"
        assert result[0].existing_text == "old text"
        assert result[0].severity == "high"
        assert result[0].confidence == 0.85

    def test_invalid_json_returns_empty(self) -> None:
        result = _parse_contradiction_response("not json", "new text", [])
        assert result == []

    def test_invalid_severity_defaults_to_medium(self) -> None:
        existing = [MockCitedChunk(chunk_id="c1", text="old text", score=0.9)]
        content = json.dumps(
            {
                "contradictions": [
                    {
                        "existing_chunk_id": "c1",
                        "explanation": "Mismatch",
                        "severity": "critical",  # invalid
                        "confidence": 0.5,
                    }
                ]
            }
        )
        result = _parse_contradiction_response(content, "new text", existing)
        assert len(result) == 1
        assert result[0].severity == "medium"

    def test_confidence_clamped(self) -> None:
        existing = [MockCitedChunk(chunk_id="c1", text="old text", score=0.9)]
        content = json.dumps(
            {
                "contradictions": [
                    {
                        "existing_chunk_id": "c1",
                        "explanation": "Mismatch",
                        "severity": "low",
                        "confidence": 1.5,  # above 1.0
                    }
                ]
            }
        )
        result = _parse_contradiction_response(content, "new text", existing)
        assert result[0].confidence == 1.0


# ---------------------------------------------------------------------------
# TestParseGapResponse
# ---------------------------------------------------------------------------


class TestParseGapResponse:
    def test_valid_json_parsed(self) -> None:
        workspace_id = uuid4()
        usage = LLMUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        content = json.dumps(
            {
                "new_topics": [
                    {
                        "topic": "Topic A",
                        "description": "New stuff",
                        "confidence": 0.8,
                        "supporting_text": "evidence",
                    }
                ],
                "covered_topics": ["Topic B"],
            }
        )
        result = _parse_gap_response(content, workspace_id, usage)
        assert len(result.new_topics) == 1
        assert result.new_topics[0].topic == "Topic A"
        assert result.covered_topics == ["Topic B"]
        assert result.has_new_knowledge
        assert result.token_usage == usage

    def test_invalid_json_returns_empty(self) -> None:
        workspace_id = uuid4()
        result = _parse_gap_response("not json", workspace_id, None)
        assert result.new_topics == []
        assert result.covered_topics == []
        assert not result.has_new_knowledge

    def test_non_list_covered_topics_defaults(self) -> None:
        workspace_id = uuid4()
        content = json.dumps(
            {
                "new_topics": [],
                "covered_topics": "not a list",
            }
        )
        result = _parse_gap_response(content, workspace_id, None)
        assert result.covered_topics == []
