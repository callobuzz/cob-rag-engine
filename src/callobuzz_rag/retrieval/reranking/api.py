"""Reranking through a hosted reranking service (Cohere, Jina, Voyage ... via LiteLLM) (PIP1).

The third reranker next to the local cross-encoder (`bge.py`, free, slow on a CPU) and the LLM
reranker (`llm.py`, the caller's own chat model). A hosted reranking model is much faster than
the cross-encoder on a CPU and cheaper than asking a chat model; the deployer pays, with
`RAG_RERANK_API_KEY`. It always runs behind `FallbackRerankProvider(api, local)` so an outage
or an expired key costs latency, never ranking quality.
"""

import logging
from typing import Any

from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult

logger = logging.getLogger(__name__)

# A passage is judged on its first few hundred words; sending more only costs money.
MAX_DOC_CHARS = 4000
TIMEOUT_SECONDS = 15.0


class APIRerankProvider(RerankProvider):
    """Rerank candidates with a hosted rerank model.

    Args:
        model: The LiteLLM rerank id, e.g. ``cohere/rerank-v3.5`` or ``jina_ai/jina-reranker-v2``.
        api_key: The service's key (`RAG_RERANK_API_KEY`). Never logged.
    """

    def __init__(self, model: str, api_key: str) -> None:
        self._model = model
        self._api_key = api_key

    @property
    def usable(self) -> bool:
        """Whether a call could succeed at all (a key and a model are set)."""
        return bool(self._api_key and self._model)

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        if not documents:
            return []
        if not self.usable:
            raise RerankError("The reranking service has no key or model configured")

        import litellm  # deferred: heavy import

        try:
            response = await litellm.arerank(
                model=self._model,
                query=query,
                documents=[doc[:MAX_DOC_CHARS] for doc in documents],
                top_n=min(top_k, len(documents)),
                api_key=self._api_key,
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:
            raise RerankError(f"The reranking service call failed: {type(exc).__name__}") from exc

        out: list[RerankResult] = []
        seen: set[int] = set()
        for item in _results(response):
            index, score = item
            if not 0 <= index < len(documents) or index in seen:
                continue  # a bad index from the service must not take the query down
            seen.add(index)
            out.append(RerankResult(index=index, score=score, text=documents[index]))
        if not out:
            raise RerankError("The reranking service returned no usable ranking")
        out.sort(key=lambda r: r.score, reverse=True)
        return out[:top_k]


def _results(response: Any) -> list[tuple[int, float]]:
    """(index, score) pairs from a LiteLLM RerankResponse, which holds dicts or objects."""
    raw = getattr(response, "results", None)
    if raw is None and isinstance(response, dict):
        raw = response.get("results")
    pairs: list[tuple[int, float]] = []
    for entry in raw or []:
        index = entry.get("index") if isinstance(entry, dict) else getattr(entry, "index", None)
        score = (
            entry.get("relevance_score")
            if isinstance(entry, dict)
            else getattr(entry, "relevance_score", None)
        )
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and isinstance(score, int | float)
        ):
            pairs.append((index, float(score)))
    return pairs
