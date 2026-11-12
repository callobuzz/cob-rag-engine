"""COB retrieves with an audience filter: both search legs must receive the whole list."""

import uuid

import pytest
from pydantic import ValidationError
from qdrant_client import models

from callobuzz_rag.api.schemas.query import QueryOptions
from callobuzz_rag.vectordb.qdrant import QdrantVectorProvider
from tests.unit.test_orchestrator_extra_queries import _orch

ALLOWED = ["customer", "public"]


async def test_list_filter_reaches_dense_and_sparse_legs() -> None:
    orch = _orch(hybrid=True)
    await orch.run_retrieval_pipeline(
        text="widget key",
        corpus_id=uuid.uuid4(),
        enable_classification=False,
        metadata_filters={"audience": ALLOWED},
    )
    dense = [call.args[3] for call in orch._vector_store.search.await_args_list]  # type: ignore[attr-defined]
    sparse = [call.args[3] for call in orch._vector_store.search_sparse.await_args_list]  # type: ignore[attr-defined]
    assert dense and sparse
    for filters in (*dense, *sparse):
        assert filters["meta_audience"] == ALLOWED
        assert "corpus_id" in filters


async def test_a_plain_string_filter_still_works() -> None:
    orch = _orch(hybrid=False)
    await orch.run_retrieval_pipeline(
        text="widget key",
        corpus_id=uuid.uuid4(),
        enable_classification=False,
        metadata_filters={"branch": "Kothrud"},
    )
    filters = orch._vector_store.search.await_args.args[3]  # type: ignore[attr-defined]
    assert filters["meta_branch"] == "Kothrud"


def test_qdrant_list_filter_is_match_any() -> None:
    f = QdrantVectorProvider._build_filter(None, {"meta_audience": ALLOWED})  # type: ignore[arg-type]
    condition = f.must[0]  # type: ignore[index]
    assert isinstance(condition.match, models.MatchAny) and condition.match.any == ALLOWED  # type: ignore[union-attr]


def test_public_query_options_stay_string_only() -> None:
    with pytest.raises(ValidationError):
        QueryOptions(metadata_filters={"audience": ["admin"]})  # type: ignore[dict-item]
