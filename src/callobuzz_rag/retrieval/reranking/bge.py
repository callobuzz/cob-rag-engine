"""BGE cross-encoder reranking provider.

Uses BAAI/bge-reranker-v2-m3 via sentence-transformers CrossEncoder.
Model is loaded once and kept in memory (~1.5GB), on the GPU when torch sees
one (in half precision there, see `rerank_fp16`). Inference runs via
asyncio.to_thread() to avoid blocking the event loop.
"""

import asyncio
import logging
import math
from typing import Any

from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult

logger = logging.getLogger(__name__)


class BGERerankProvider(RerankProvider):
    """Cross-encoder reranker using BAAI/bge-reranker-v2-m3.

    The model is loaded lazily on first rerank() call to avoid
    blocking application startup. Subsequent calls reuse the
    loaded model.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        max_length: int = 1024,
        batch_size: int = 0,
        fp16: bool = True,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._batch_size = batch_size
        self._fp16 = fp16
        self._model: Any = None
        self._resolved_batch_size: int | None = None

    def _ensure_model(self) -> None:
        """Load the cross-encoder model if not already loaded."""
        if self._model is not None:
            return

        # Deferred import — sentence-transformers is heavy
        from sentence_transformers import CrossEncoder

        logger.info(
            "Loading cross-encoder model: %s (this may take a moment)",
            self._model_name,
        )
        try:
            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )
        except Exception as e:
            raise RerankError(f"Failed to load rerank model '{self._model_name}': {e}") from e
        # Tensor cores do fp16 several times faster than fp32; on CPU half precision
        # is slower, so it is a GPU-only switch.
        if self._fp16 and str(getattr(self._model, "device", "cpu")).startswith("cuda"):
            self._model.model.half()
        logger.info("Cross-encoder model loaded successfully")

    async def warmup(self) -> float:
        """Load the model now, off the event loop, and report how long it took.

        The lazy load below is deliberate — it keeps startup fast — but it moves
        the cost onto whoever queries first. This runs **once per container
        start**, never per query: the instance lives on `app.state.reranker` and
        `_ensure_model` short-circuits forever after.

        What that first user waits for, measured 2026-08-14 on a real
        20-candidate query:
        importing torch is 3.7s of it, reading and placing the weights is
        1.2s on CPU / 2.3s on GPU — the GPU is SLOWER to load, 2.27 GB has to
        cross PCIe — and revalidating the cached model against huggingface.co
        adds a further 3.2s. This method logs higher still (17.8s observed)
        because the forward pass below initialises the CUDA context on an event
        loop busy with the rest of startup.

        The earlier form of this docstring said 16.2s/18.4s was the weights being
        "read and moved to the device". Most of it is not.

        Call this as a background task, never awaited inline: blocking startup on
        it would make the container fail its own healthcheck window.
        """
        loop = asyncio.get_running_loop()
        started = loop.time()
        await asyncio.to_thread(self._ensure_model)
        # One real pair, because loading the weights is not the whole cost —
        # the first forward pass allocates workspace and, on CUDA, initialises
        # the context. Without it the first user still pays part of the bill.
        await asyncio.to_thread(
            lambda: self._model.predict(
                [("warmup query", "warmup document")],
                batch_size=1,
                show_progress_bar=False,
            )
        )
        return loop.time() - started

    def _batch_size_for_device(self) -> int:
        """Resolve the batch size, auto-detecting the device when unset.

        Batching exists to parallelise, and on CPU there is nothing to
        parallelise -- one sequence already saturates it. What batching DOES do
        is pad every pair up to the longest in its batch, so a single oversized
        chunk multiplies the cost of every other pair beside it. That is the
        whole problem: measured here, 20 candidates with one long outlier took
        45.4s at batch_size 32 and 11.6s at 1, while a uniform batch was
        unaffected by batch size at all.

        On CUDA there is real parallelism to win, but the padding tax comes back
        with large batches. Measured 2026-09-29 on an RTX 5060 Ti in half
        precision, 50 real SciFact candidates (pairs of ~390 tokens, max ~650),
        eight different queries each: batch 32 -> 580-880 ms, 16 -> 460-640 ms,
        8 -> 415-515 ms. So 8 on CUDA.
        """
        if self._batch_size > 0:
            return self._batch_size
        if self._resolved_batch_size is not None:
            return self._resolved_batch_size

        device = str(getattr(self._model, "device", "cpu"))
        self._resolved_batch_size = 8 if device.startswith("cuda") else 1
        logger.info(
            "Reranker batch size auto-set to %d (device=%s)",
            self._resolved_batch_size,
            device,
        )
        return self._resolved_batch_size

    def _predict_sync(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        """Synchronous prediction — runs in a thread."""
        self._ensure_model()

        if not documents:
            return []

        pairs = [(query, doc) for doc in documents]

        assert self._model is not None
        try:
            scores = self._model.predict(pairs, batch_size=self._batch_size_for_device())
        except Exception as exc:
            raise RerankError(f"Cross-encoder prediction failed: {exc}") from exc

        if any(not math.isfinite(s) for s in scores):
            raise RerankError("Model returned non-finite scores")

        results = [
            RerankResult(index=i, score=float(score), text=doc)
            for i, (score, doc) in enumerate(zip(scores, documents, strict=True))
        ]

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:top_k]

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        """Rerank documents using the BGE cross-encoder.

        Runs inference in a thread pool to avoid blocking the event loop.
        """
        if not documents:
            return []

        effective_top_k = min(top_k, len(documents))

        try:
            return await asyncio.to_thread(self._predict_sync, query, documents, effective_top_k)
        except RerankError:
            raise
        except Exception as exc:
            raise RerankError(f"Reranking failed unexpectedly: {exc}") from exc
