"""Tests for retrieval config schema extensions."""

import pytest
from pydantic import ValidationError


def test_retrieval_config_input_valid():
    """RetrievalConfigInput accepts valid config."""
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    config = RetrievalConfigInput(
        preset="standard",
        top_k=10,
        mmr_enabled=True,
    )
    assert config.preset == "standard"
    assert config.top_k == 10


def test_retrieval_config_input_preset_only():
    """RetrievalConfigInput accepts preset-only config."""
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    config = RetrievalConfigInput(preset="economy")
    assert config.preset == "economy"
    assert config.top_k is None


def test_retrieval_config_input_no_preset():
    """RetrievalConfigInput works without preset (individual overrides only)."""
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    config = RetrievalConfigInput(top_k=5, cache_enabled=False)
    assert config.preset is None
    assert config.top_k == 5
    assert config.cache_enabled is False


def test_retrieval_config_input_invalid_preset():
    """RetrievalConfigInput rejects unknown preset names."""
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    with pytest.raises(ValidationError, match="preset"):
        RetrievalConfigInput(preset="legendary")


def test_retrieval_config_input_top_k_bounds():
    """top_k must be within bounds."""
    from callobuzz_rag.api.schemas.corpus import RetrievalConfigInput

    with pytest.raises(ValidationError):
        RetrievalConfigInput(top_k=500)

    with pytest.raises(ValidationError):
        RetrievalConfigInput(top_k=0)


def test_create_corpus_request_with_retrieval_config():
    """CreateCorpusRequest accepts retrieval_config."""
    from callobuzz_rag.api.schemas.corpus import CreateCorpusRequest

    req = CreateCorpusRequest(
        name="Test Corpus",
        retrieval_config={"preset": "economy"},
    )
    assert req.retrieval_config is not None
    assert req.retrieval_config.preset == "economy"


def test_update_corpus_request_with_retrieval_config():
    """UpdateCorpusRequest accepts retrieval_config."""
    from callobuzz_rag.api.schemas.corpus import UpdateCorpusRequest

    req = UpdateCorpusRequest(
        retrieval_config={"top_k": 15, "cache_enabled": False},
    )
    assert req.retrieval_config is not None
    assert req.retrieval_config.top_k == 15


def test_corpus_detail_response_includes_retrieval_config():
    """CorpusDetailResponse has retrieval_config field."""
    from callobuzz_rag.api.schemas.corpus import CorpusDetailResponse

    fields = CorpusDetailResponse.model_fields
    assert "retrieval_config" in fields
