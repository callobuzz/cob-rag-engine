"""The reranker must not pay a padding tax it gets nothing for.

A cross-encoder pads every pair in a batch up to the longest one, so a single
oversized chunk multiplies the cost of every pair beside it. sentence-transformers
sorts by length internally, but its default ``batch_size=32`` swallows our whole
candidate set (7-20 pairs) into one batch, so the sort never gets a chance.

Measured in-container on the real ``bge-reranker-v2-m3``, 20 candidates with one
long outlier: **45.4s at batch_size 32, 11.6s at 1**. On a uniform batch, batch
size changed nothing (9.8-10.0s) -- CPU is saturated by one sequence, so batching
buys no parallelism and only costs padding. Scores are byte-identical either way
(max abs diff 1.7e-08, same ranking), which is why this is waste removal rather
than a quality trade.

These tests pin the *decision* (what batch size we ask for, on which device),
not the model's arithmetic. The timings above were measured against the live
model; re-running a 568M cross-encoder in unit tests would be minutes per run.
"""

from typing import Any

import pytest

from callobuzz_rag.retrieval.reranking.bge import BGERerankProvider


class _FakeModel:
    """Records the batch_size it was called with. Deliberately not a mock of
    the reranker itself -- the thing under test is which knob we turn."""

    def __init__(self, device: str = "cpu") -> None:
        self.device = device
        self.calls: list[dict[str, Any]] = []

    def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> list[float]:
        self.calls.append(kwargs)
        # Descending so the provider's own sort is observable.
        return [float(len(pairs) - i) for i in range(len(pairs))]


def _provider(model: _FakeModel, **kwargs: Any) -> BGERerankProvider:
    provider = BGERerankProvider(**kwargs)
    provider._model = model  # already "loaded", so _ensure_model is a no-op
    return provider


class TestBatchSizeSelection:
    @pytest.mark.asyncio
    async def test_cpu_defaults_to_one_pair_per_batch(self) -> None:
        """The padding tax is the entire cost on CPU. One pair per batch means
        every pair is padded only to itself."""
        model = _FakeModel(device="cpu")

        await _provider(model).rerank("q", ["a", "b", "c"], top_k=3)

        assert model.calls[0]["batch_size"] == 1

    @pytest.mark.asyncio
    async def test_cuda_keeps_a_real_batch(self) -> None:
        """On GPU there IS parallelism to win, but large batches pay padding: 8
        measured fastest on 50 real candidates (see _batch_size_for_device)."""
        model = _FakeModel(device="cuda:0")

        await _provider(model).rerank("q", ["a", "b"], top_k=2)

        assert model.calls[0]["batch_size"] == 8

    @pytest.mark.asyncio
    async def test_an_explicit_setting_overrides_the_device(self) -> None:
        model = _FakeModel(device="cpu")

        await _provider(model, batch_size=8).rerank("q", ["a", "b"], top_k=2)

        assert model.calls[0]["batch_size"] == 8

    @pytest.mark.asyncio
    async def test_the_device_is_probed_once_not_per_query(self) -> None:
        """Retrieval is the hot path; re-deriving this per call would be silly."""
        model = _FakeModel(device="cpu")
        provider = _provider(model)

        await provider.rerank("q", ["a"], top_k=1)
        await provider.rerank("q", ["b"], top_k=1)

        assert provider._resolved_batch_size == 1
        assert [c["batch_size"] for c in model.calls] == [1, 1]


class TestBehaviourIsUnchanged:
    """Batch size must be invisible in the output. If any of these break, the
    'zero accuracy cost' claim for batch sizing is void."""

    @pytest.mark.asyncio
    async def test_results_are_still_ranked_and_trimmed(self) -> None:
        model = _FakeModel()

        results = await _provider(model).rerank("q", ["a", "b", "c", "d"], top_k=2)

        assert len(results) == 2
        assert results[0].score >= results[1].score

    @pytest.mark.asyncio
    async def test_indexes_still_point_at_the_original_documents(self) -> None:
        """The caller maps results back by `index`; a batching change that
        scrambled these would silently mis-attribute every chunk."""
        model = _FakeModel()
        documents = ["alpha", "beta", "gamma"]

        results = await _provider(model).rerank("q", documents, top_k=3)

        for result in results:
            assert documents[result.index] == result.text

    @pytest.mark.asyncio
    async def test_empty_input_never_reaches_the_model(self) -> None:
        model = _FakeModel()

        assert await _provider(model).rerank("q", [], top_k=5) == []
        assert model.calls == []


class TestMaxLengthIsExposedButUnchanged:
    def test_the_default_still_does_not_truncate(self) -> None:
        """Unlike batch size, lowering this WOULD cost accuracy — it discards
        the tail of a chunk. It is exposed for tuning against an eval set, and
        no eval set exists yet, so the default must not move."""
        assert BGERerankProvider()._max_length == 1024

    def test_it_can_be_lowered_deliberately(self) -> None:
        assert BGERerankProvider(max_length=512)._max_length == 512


class TestWarmup:
    """The load happens once; the only question is who waits for it.

    `_ensure_model` is lazy so startup stays fast, and that is right — but the
    load is charged to the first person to ask a question after a deploy.
    `warmup()` moves it to a background task at startup. It is paid once per
    container start either way, never per query.

    Re-measured 2026-08-14 on a real 20-candidate query. The figures
    that used to sit here — "16.2s CPU / 18.4s GPU to read and place, 0.59s /
    0.10s warm" — were misleading twice over: reading and placing the weights is
    ~1.2s of that load (most of the rest is importing torch and revalidating the
    cache against huggingface.co), and the warm numbers were for THREE
    candidates when a real query reranks 20-40. Warm at 20 candidates is 406ms
    on GPU and 9.7s on CPU.
    """

    @pytest.mark.asyncio
    async def test_warmup_runs_a_real_pair_not_just_the_load(self) -> None:
        """Loading the weights is not the whole cost.

        The first forward pass allocates workspace and, on CUDA, initialises the
        context. A warmup that only loaded the model would leave part of the bill
        with the first user — so assert a prediction actually happened.
        """
        provider = BGERerankProvider()
        fake = _FakeModel()
        provider._model = fake  # already "loaded" — keeps sentence-transformers out

        elapsed = await provider.warmup()

        assert len(fake.calls) == 1, "warmup must run one forward pass"
        assert elapsed >= 0.0

    @pytest.mark.asyncio
    async def test_warmup_is_idempotent_and_does_not_reload(self) -> None:
        """Called twice (a restart racing a request), it must not load twice."""
        provider = BGERerankProvider()
        fake = _FakeModel()
        provider._model = fake

        await provider.warmup()
        await provider.warmup()

        assert provider._model is fake
        assert len(fake.calls) == 2, "each call warms; neither replaces the model"

    def test_warmup_follows_reranking_unless_set(self) -> None:
        """Holding ~2.3 GB resident is only worth it when reranking is actually on."""
        from callobuzz_rag.core.config import Settings

        base = {"_env_file": None, "jina_api_key": "t", "llm_api_key": "t"}
        assert Settings(**base).rerank_warmup_enabled is None  # follow reranking_enabled
        assert Settings(**base, rerank_warmup_enabled=False).rerank_warmup_enabled is False
        assert Settings(**base, rerank_warmup_enabled=True).rerank_warmup_enabled is True


@pytest.mark.parametrize(
    ("device", "fp16", "halved"),
    [
        ("cuda:0", True, True),
        ("cuda:0", False, False),
        ("cpu", True, False),  # half precision is slower on CPU, never applied there
    ],
)
def test_half_precision_is_a_gpu_only_switch(device: str, fp16: bool, halved: bool) -> None:
    import sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    model = MagicMock(device=device)
    fake = SimpleNamespace(CrossEncoder=MagicMock(return_value=model))
    with patch.dict(sys.modules, {"sentence_transformers": fake}):
        BGERerankProvider(fp16=fp16)._ensure_model()
    assert model.model.half.called is halved
