"""Tests for admin action API schemas."""

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.admin import (
    RetrievalConfigResponse,
    TriggerEvalRequest,
    TriggerEvalResponse,
    TriggerOptimizeRequest,
    TriggerOptimizeResponse,
    UpdateRetrievalConfigRequest,
)


class TestTriggerEvalRequest:
    def test_defaults(self) -> None:
        req = TriggerEvalRequest()
        assert req.sample_size is None
        assert req.corpus_id is None

    def test_with_values(self) -> None:
        corpus_id = "6f1c1d52-7c1e-4a36-9d1a-1f7f0c3b8e21"
        req = TriggerEvalRequest(sample_size=50, corpus_id=corpus_id)
        assert req.sample_size == 50
        assert str(req.corpus_id) == corpus_id

    def test_corpus_id_must_be_a_uuid(self) -> None:
        """A non-UUID corpus_id used to be accepted and then crash the worker."""
        with pytest.raises(ValidationError):
            TriggerEvalRequest(corpus_id="my-corpus")

    def test_sample_size_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TriggerEvalRequest(sample_size=0)
        with pytest.raises(ValidationError):
            TriggerEvalRequest(sample_size=-1)


class TestTriggerOptimizeRequest:
    def test_defaults(self) -> None:
        req = TriggerOptimizeRequest()
        assert req.n_trials is None
        assert req.corpus_id is None

    def test_n_trials_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            TriggerOptimizeRequest(n_trials=0)
        with pytest.raises(ValidationError):
            TriggerOptimizeRequest(n_trials=-1)


class TestUpdateRetrievalConfigRequest:
    def test_all_optional(self) -> None:
        req = UpdateRetrievalConfigRequest()
        assert req.top_k_dense is None
        assert req.mmr_lambda is None

    def test_partial_update(self) -> None:
        req = UpdateRetrievalConfigRequest(top_k_dense=30, rrf_k=80)
        assert req.top_k_dense == 30
        assert req.rrf_k == 80
        assert req.mmr_lambda is None

    def test_mmr_lambda_range(self) -> None:
        req = UpdateRetrievalConfigRequest(mmr_lambda=0.0)
        assert req.mmr_lambda == 0.0
        req = UpdateRetrievalConfigRequest(mmr_lambda=1.0)
        assert req.mmr_lambda == 1.0
        with pytest.raises(ValidationError):
            UpdateRetrievalConfigRequest(mmr_lambda=-0.1)
        with pytest.raises(ValidationError):
            UpdateRetrievalConfigRequest(mmr_lambda=1.1)

    def test_rerank_top_k_output_positive(self) -> None:
        req = UpdateRetrievalConfigRequest(rerank_top_k_output=1)
        assert req.rerank_top_k_output == 1
        with pytest.raises(ValidationError):
            UpdateRetrievalConfigRequest(rerank_top_k_output=0)


class TestRetrievalConfigResponse:
    def test_from_dict(self) -> None:
        resp = RetrievalConfigResponse(
            top_k_dense=20,
            top_k_sparse=20,
            rrf_k=60,
            mmr_lambda=0.7,
            temporal_decay_half_life_days=30.0,
            rerank_top_n_input=50,
            rerank_top_k_output=10,
            source="defaults",
        )
        assert resp.top_k_dense == 20
        assert resp.source == "defaults"


class TestTriggerEvalResponse:
    def test_fields(self) -> None:
        resp = TriggerEvalResponse(eval_run_id="abc-123")
        assert resp.eval_run_id == "abc-123"
        assert resp.status == "queued"


class TestTriggerOptimizeResponse:
    def test_fields(self) -> None:
        resp = TriggerOptimizeResponse(optimization_run_id="opt-456")
        assert resp.optimization_run_id == "opt-456"
        assert resp.status == "queued"
