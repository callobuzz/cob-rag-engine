"""Unit tests for extractive enrichment."""

import pytest

from callobuzz_rag.chunking.models import ChunkResult
from callobuzz_rag.enrichment.extractive import ExtractiveEnrichmentProvider


@pytest.fixture
def enricher():
    return ExtractiveEnrichmentProvider(max_keywords=5)


@pytest.fixture
def sample_chunk():
    return ChunkResult(
        text=(
            "Machine learning is a subset of artificial intelligence. "
            "It enables computers to learn from data. "
            "Deep learning is a further subset that uses neural networks."
        ),
        chunk_index=0,
        structure_path=["AI", "Machine Learning"],
        length_tokens=30,
    )


async def test_extract_summary(enricher, sample_chunk):
    result = await enricher.enrich(sample_chunk)
    assert result.summary is not None
    assert "Machine learning" in result.summary


async def test_summary_first_two_sentences(enricher, sample_chunk):
    result = await enricher.enrich(sample_chunk)
    assert result.summary is not None
    # Should contain first two sentences but not third
    assert "artificial intelligence" in result.summary
    assert "learn from data" in result.summary


async def test_extract_keywords(enricher, sample_chunk):
    result = await enricher.enrich(sample_chunk)
    assert len(result.keywords) > 0
    # Keywords should be multi-word phrases from RAKE
    assert all(isinstance(k, str) for k in result.keywords)


async def test_detect_language_english(enricher, sample_chunk):
    result = await enricher.enrich(sample_chunk)
    assert result.language == "en"


async def test_confidence_score_range(enricher, sample_chunk):
    result = await enricher.enrich(sample_chunk)
    assert 0.0 <= result.confidence_score <= 1.0


async def test_empty_text(enricher):
    chunk = ChunkResult(text="", chunk_index=0, structure_path=[], length_tokens=0)
    result = await enricher.enrich(chunk)
    assert result.summary is None
    assert result.keywords == []
    assert result.language is None


async def test_short_text(enricher):
    chunk = ChunkResult(text="Hi.", chunk_index=0, structure_path=[], length_tokens=1)
    result = await enricher.enrich(chunk)
    assert result.keywords == []
    assert result.language is None


async def test_table_chunk_confidence(enricher):
    chunk = ChunkResult(
        text="| A | B |\n|---|---|\n| 1 | 2 |",
        chunk_index=0,
        structure_path=[],
        length_tokens=200,
        is_table=True,
    )
    result = await enricher.enrich(chunk)
    assert result.confidence_score > 0.0


async def test_code_chunk_confidence(enricher):
    chunk = ChunkResult(
        text="def hello():\n    print('world')\n    return True",
        chunk_index=0,
        structure_path=[],
        length_tokens=200,
        is_code=True,
    )
    result = await enricher.enrich(chunk)
    assert result.confidence_score > 0.0


async def test_phase1_fields_empty(enricher, sample_chunk):
    """Phase 1: hypothetical_questions and tags should be empty."""
    result = await enricher.enrich(sample_chunk)
    assert result.hypothetical_questions == []
    assert result.tags == []


async def test_enrich_batch(enricher, sample_chunk):
    """Batch enrichment processes all chunks."""
    chunks = [sample_chunk, sample_chunk]
    results = await enricher.enrich_batch(chunks)
    assert len(results) == 2
    assert all(r.summary is not None for r in results)
