"""Tests for content similarity detection."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.pipeline.similarity import find_similar_documents, spread_sample


def _hit(doc_id: str, score: float, **payload: str) -> MagicMock:
    return MagicMock(id=str(uuid.uuid4()), score=score, payload={"document_id": doc_id, **payload})


def _embedder(n: int) -> AsyncMock:
    embedder = AsyncMock()
    embedder.embed_texts.return_value = [MagicMock(vector=[0.1 * (i + 1)] * 4) for i in range(n)]
    return embedder


async def _find(embedder, store, texts, **kwargs):
    return await find_similar_documents(
        chunks_text=texts,
        corpus_id=uuid.uuid4(),
        embedder=embedder,
        vector_store=store,
        collection="test-collection",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_find_similar_returns_candidates():
    """Documents sorted by similarity score."""
    doc_1, doc_2 = str(uuid.uuid4()), str(uuid.uuid4())
    store = AsyncMock()
    store.search.return_value = [
        _hit(doc_1, 0.97),
        _hit(doc_1, 0.93),
        _hit(doc_2, 0.91),
    ]
    candidates = await _find(_embedder(2), store, ["chunk one text", "chunk two text"])

    assert [c["document_id"] for c in candidates] == [doc_1, doc_2]
    assert candidates[0]["similarity"] > candidates[1]["similarity"]
    # A document counts once per sampled chunk, by its best hit.
    assert candidates[0]["match_count"] == 2


@pytest.mark.asyncio
async def test_find_similar_empty_when_no_matches():
    store = AsyncMock()
    store.search.return_value = []
    assert await _find(_embedder(1), store, ["some text"]) == []


@pytest.mark.asyncio
async def test_find_similar_excludes_same_document():
    doc_self, doc_other = str(uuid.uuid4()), str(uuid.uuid4())
    store = AsyncMock()
    store.search.return_value = [_hit(doc_self, 0.99), _hit(doc_other, 0.95)]
    candidates = await _find(_embedder(1), store, ["text"], exclude_document_id=doc_self)
    assert [c["document_id"] for c in candidates] == [doc_other]


@pytest.mark.asyncio
async def test_find_similar_samples_at_most_sample_size_chunks():
    store = AsyncMock()
    store.search.return_value = []
    embedder = _embedder(3)
    await _find(embedder, store, [f"chunk {i}" for i in range(20)], sample_size=3)
    assert len(embedder.embed_texts.call_args[0][0]) == 3


def test_the_sample_is_spread_across_the_document_not_its_first_chunks():
    chunks = [f"chunk {i}" for i in range(20)]
    assert spread_sample(chunks, 5) == ["chunk 0", "chunk 5", "chunk 10", "chunk 14", "chunk 19"]
    assert spread_sample(chunks, 2) == ["chunk 0", "chunk 19"]
    assert spread_sample(chunks, 1) == ["chunk 0"]
    assert spread_sample(chunks[:3], 5) == chunks[:3]
    assert spread_sample([], 5) == []


@pytest.mark.asyncio
async def test_similarity_requires_floor_and_match_count():
    doc_a, doc_b, doc_c = (str(uuid.uuid4()) for _ in range(3))
    # Five sampled chunks. doc_a is found by all five at high scores; doc_b by only two
    # (a shared passage, not the same document); doc_c by four, but below the floor.
    answers = [
        [_hit(doc_a, 0.96), _hit(doc_b, 0.95), _hit(doc_c, 0.88)],
        [_hit(doc_a, 0.94), _hit(doc_b, 0.92), _hit(doc_c, 0.89)],
        [_hit(doc_a, 0.93), _hit(doc_c, 0.87)],
        [_hit(doc_a, 0.92), _hit(doc_c, 0.86)],
        [_hit(doc_a, 0.91)],
    ]
    store = AsyncMock()
    store.search.side_effect = answers
    candidates = await _find(_embedder(5), store, [f"chunk {i}" for i in range(5)])

    assert [c["document_id"] for c in candidates] == [doc_a]
    assert candidates[0]["match_count"] == 5
    assert candidates[0]["similarity"] == pytest.approx(
        (0.96 + 0.94 + 0.93 + 0.92 + 0.91) / 5, abs=1e-4
    )


@pytest.mark.asyncio
async def test_three_of_five_samples_are_enough_two_are_not():
    doc_3, doc_2 = str(uuid.uuid4()), str(uuid.uuid4())
    answers = [
        [_hit(doc_3, 0.95), _hit(doc_2, 0.95)],
        [_hit(doc_3, 0.95), _hit(doc_2, 0.95)],
        [_hit(doc_3, 0.95)],
        [],
        [],
    ]
    store = AsyncMock()
    store.search.side_effect = answers
    candidates = await _find(_embedder(5), store, [f"c{i}" for i in range(5)])
    assert [c["document_id"] for c in candidates] == [doc_3]
    assert candidates[0]["match_count"] == 3


@pytest.mark.asyncio
async def test_a_short_document_needs_all_of_its_samples_to_match():
    doc = str(uuid.uuid4())
    store = AsyncMock()
    store.search.side_effect = [[_hit(doc, 0.97)], [_hit(doc, 0.96)]]
    candidates = await _find(_embedder(2), store, ["one", "two"])
    assert [c["document_id"] for c in candidates] == [doc]


@pytest.mark.asyncio
async def test_the_floor_is_configurable():
    doc = str(uuid.uuid4())
    store = AsyncMock()
    store.search.return_value = [_hit(doc, 0.80)]
    assert await _find(_embedder(1), store, ["one"]) == []
    found = await _find(_embedder(1), store, ["one"], min_score=0.75)
    assert [c["document_id"] for c in found] == [doc]


@pytest.mark.asyncio
async def test_similarity_ignores_boilerplate_hashes():
    doc_furniture, doc_real = str(uuid.uuid4()), str(uuid.uuid4())
    store = AsyncMock()
    store.search.return_value = [
        _hit(doc_furniture, 0.99, text_hash="aaaaaaaaaaaaaaaa"),  # the shared footer chunk
        _hit(doc_real, 0.95, text_hash="bbbbbbbbbbbbbbbb"),
    ]
    candidates = await _find(
        _embedder(1), store, ["footer-ish"], ignore_text_hashes={"aaaaaaaaaaaaaaaa"}
    )
    assert [c["document_id"] for c in candidates] == [doc_real]
