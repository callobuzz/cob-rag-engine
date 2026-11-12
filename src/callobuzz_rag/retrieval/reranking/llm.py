"""LLM-as-reranker provider.

The local cross-encoder (`bge.py`) is free and self-hosted but CPU-bound: cost
is roughly linear in candidate count and quadratic in the longest candidate, so
a 20-candidate rerank costs seconds rather than milliseconds. This provider
trades that for money — it asks the caller's own LLM to rank the candidates,
using the credentials that already arrive per request on the `X-LLM-*` headers.

It is a *switchable alternative*, never a replacement. The consumer decides,
per corpus, whether a faster paid rerank is worth it. With no credentials
available the caller is expected to fall back to the local provider
(`FallbackRerankProvider`), because a reranker that cannot run must not silently
turn into no reranking at all.

Scores: the model is asked for a relevance score per candidate, not merely an
ordering, because everything downstream treats `RerankResult.score` as a real
number — it is written onto `SearchResult.score` when pipeline ordering is on,
and it is surfaced as `score_breakdown.rerank_score`. A model that answers with
bare indices still works: positional scores are derived so the ordering
survives, and the derivation is recorded in the log rather than hidden.
"""

import json
import logging
from typing import Any

from callobuzz_rag.core.exceptions import RerankError
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.models import LLMMessage
from callobuzz_rag.retrieval.reranking.base import RerankProvider, RerankResult

logger = logging.getLogger(__name__)

# Long candidates cost input tokens on every query and add nothing to a
# relevance judgement — the decision is made in the first sentence or two. The
# local cross-encoder truncates at `rerank_max_length` for the same reason.
MAX_DOC_CHARS = 1200

RERANK_SYSTEM_PROMPT = """You rank search results by how well they answer a query.

You will be given a query and a numbered list of candidate passages.

Return ONLY a JSON object of this exact shape:

{"ranking": [{"index": <int>, "score": <float 0.0-1.0>}, ...]}

Rules:
- `index` is the candidate's number as given to you.
- `score` is relevance to the query: 1.0 answers it directly, 0.0 is unrelated.
- Order the array from most to least relevant.
- Include every candidate exactly once.
- Judge relevance to the query only. Never invent, summarise or answer."""


class LLMRerankProvider(RerankProvider):
    """Rerank candidates by asking an LLM to score them.

    Args:
        llm: The provider to call. Supplied per request, so it carries the
            consumer's own credentials.
        model: Model override. `None` uses the provider's `fast_model`, since
            ranking is a judgement task and not the answer model's job.
    """

    def __init__(self, llm: LLMProvider, model: str | None = None) -> None:
        self._llm = llm
        self._model = model

    @property
    def usable(self) -> bool:
        """Whether a call could succeed at all.

        Checked before use so the absence of a credential routes to the local
        provider instead of producing an exception the pipeline would swallow
        into "reranking skipped".
        """
        return self._llm.has_credentials

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        """Rank `documents` against `query` and return the top `top_k`."""
        if not documents:
            return []

        if not self.usable:
            raise RerankError("LLM reranker has no credentials available")

        messages = [
            LLMMessage(role="system", content=RERANK_SYSTEM_PROMPT),
            LLMMessage(role="user", content=self._build_user_prompt(query, documents)),
        ]

        model = self._model or self._llm.fast_model
        options: dict[str, Any] = {"model": model, "temperature": 0.0}
        # A re-rank is a helper call: it gets the fast model's thinking, not the knowledge
        # base's answer thinking (a base set to "high" must not re-rank slowly).
        level = self._llm.helper_thinking(model)
        if level is not None:
            options["thinking"] = level
        try:
            response = await self._llm.complete_json(messages, **options)
        except Exception as e:
            raise RerankError(f"LLM rerank call failed: {e}") from e

        ranking = self._parse_ranking(response.content, len(documents))
        if not ranking:
            raise RerankError("LLM rerank returned no usable ranking")

        return [
            RerankResult(index=idx, score=score, text=documents[idx])
            for idx, score in ranking[:top_k]
        ]

    def _build_user_prompt(self, query: str, documents: list[str]) -> str:
        lines = [f"Query: {query}", "", "Candidates:"]
        for i, doc in enumerate(documents):
            text = doc[:MAX_DOC_CHARS].replace("\n", " ").strip()
            lines.append(f"[{i}] {text}")
        return "\n".join(lines)

    def _parse_ranking(self, content: str, n_docs: int) -> list[tuple[int, float]]:
        """Turn the model's reply into `(index, score)` pairs.

        Everything here is defensive on purpose: the reply is generated text,
        so every field is treated as untrusted. An entry that cannot be read is
        dropped rather than failing the whole rerank, and an index outside the
        candidate list is dropped rather than raising an IndexError deep in the
        pipeline — that is the failure mode that would take a whole query down.
        """
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("LLM rerank reply was not JSON: %s", e)
            return []

        raw = parsed.get("ranking") if isinstance(parsed, dict) else parsed
        if not isinstance(raw, list):
            logger.warning("LLM rerank reply had no ranking array")
            return []

        out: list[tuple[int, float]] = []
        seen: set[int] = set()
        derived = False

        for position, entry in enumerate(raw):
            index, score = self._read_entry(entry, position, len(raw))
            if index is None:
                continue
            if not 0 <= index < n_docs:
                # A hallucinated index. Dropping it keeps the rest of a
                # perfectly good ranking usable.
                logger.warning("LLM rerank returned out-of-range index %d", index)
                continue
            if index in seen:
                continue
            if score is None:
                score = self._positional_score(position, len(raw))
                derived = True
            seen.add(index)
            out.append((index, score))

        if derived:
            logger.info(
                "LLM rerank returned an ordering without scores; scores derived from position"
            )
        return out

    def _read_entry(self, entry: Any, position: int, total: int) -> tuple[int | None, float | None]:
        """Read one ranking entry, accepting either `{index, score}` or a bare int."""
        if isinstance(entry, bool):
            # bool is an int subclass; a `true` in the array is a bad reply,
            # not candidate 1.
            return None, None
        if isinstance(entry, int):
            return entry, None
        if isinstance(entry, dict):
            index = entry.get("index")
            if not isinstance(index, int) or isinstance(index, bool):
                return None, None
            score = entry.get("score")
            if isinstance(score, bool) or not isinstance(score, int | float):
                return index, None
            # Clamp rather than reject: a model answering 1.2 meant "very
            # relevant", and discarding the entry would lose that judgement.
            return index, max(0.0, min(1.0, float(score)))
        return None, None

    @staticmethod
    def _positional_score(position: int, total: int) -> float:
        """Descending scores in (0, 1] for a ranking that carried none."""
        if total <= 0:
            return 0.0
        return round((total - position) / total, 6)


class FallbackRerankProvider(RerankProvider):
    """Try `primary`, fall back to `secondary` on any failure.

    This is what makes the paid toggle safe to leave on. Without it, a provider
    outage or an expired key degrades to *no reranking* — the orchestrator
    catches rerank errors and keeps the fusion order — which is a silent
    quality regression of exactly the kind that has bitten this project before.
    With it, the worst case is that a query is slow rather than badly ranked.
    """

    def __init__(self, primary: RerankProvider, secondary: RerankProvider) -> None:
        self._primary = primary
        self._secondary = secondary

    async def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int = 10,
    ) -> list[RerankResult]:
        try:
            return await self._primary.rerank(query, documents, top_k=top_k)
        except Exception as e:
            logger.warning("Primary reranker failed (%s); falling back to local", e)
            return await self._secondary.rerank(query, documents, top_k=top_k)
