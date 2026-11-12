"""Run RAGAS on the engine's own LLM and embedding providers.

Left to itself, `ragas.evaluate()` builds an OpenAI chat model and OpenAI
embeddings from `OPENAI_API_KEY`. The engine ships no such key: its model is
whatever LiteLLM string the operator (or the corpus) configured, and its
embeddings come from the configured provider (local / TEI / LiteLLM / Jina).
These adapters hand RAGAS those instead.

Everything here imports ragas lazily: it is heavy and only the worker needs it.
"""

import asyncio
import concurrent.futures
import logging
import math
import threading
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig
    from callobuzz_rag.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)

T = TypeVar("T")


def build_ragas_llm(config: "ResolvedLLMConfig", provider: Any = None) -> Any:
    """A `BaseRagasLLM` that answers through the engine's LiteLLM provider.

    Pass `provider` to reuse a `LiteLLMProvider` the caller already holds, so every judge call
    lands on that provider's cost ledger (golden runs); by default one is built from `config`.
    """
    from langchain_core.outputs import Generation, LLMResult
    from ragas.llms.base import BaseRagasLLM

    from callobuzz_rag.llm.litellm_provider import LiteLLMProvider
    from callobuzz_rag.llm.models import LLMMessage

    if provider is None:
        provider = LiteLLMProvider.from_resolved_config(config)

    class _EngineRagasLLM(BaseRagasLLM):  # type: ignore[misc]
        """RAGAS prompts in, LiteLLM completions out. `stop` is not forwarded."""

        async def _one(self, prompt: Any, temperature: float | None) -> Any:
            response = await provider.complete(
                [LLMMessage(role="user", content=prompt.to_string())],
                temperature=temperature,
            )
            return Generation(
                text=response.content,
                generation_info={"finish_reason": response.finish_reason},
            )

        async def agenerate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float | None = None,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            generations = [await self._one(prompt, temperature) for _ in range(n)]
            return LLMResult(generations=[generations])

        def generate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float | None = 1e-8,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            # RAGAS calls this from inside its own running loop; a fresh thread
            # gets a loop of its own. LiteLLM holds no loop-bound state.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(
                    asyncio.run, self.agenerate_text(prompt, n, temperature, stop)
                ).result()

        def is_finished(self, response: Any) -> bool:
            # A truncated answer ("length") is unparseable JSON to RAGAS: retry it.
            return all(
                (g.generation_info or {}).get("finish_reason") != "length"
                for gens in response.generations
                for g in gens
            )

    return _EngineRagasLLM()


class _LoopThread:
    """One event loop on a daemon thread, owning an async provider.

    The engine's embedding providers are async and some hold loop-bound HTTP
    clients, while RAGAS's answer-relevancy metric calls embeddings
    synchronously from inside its own running loop. Every call is sent to this
    one loop, so the provider always sees the loop it was created on.
    """

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self.loop.run_forever, name="ragas-embeddings", daemon=True
        )
        self._thread.start()

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    async def arun(self, coro: Coroutine[Any, Any, T]) -> T:
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self.loop))

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=10)
        self.loop.close()


class RagasEmbeddings:
    """Builds the RAGAS embeddings adapter and owns its provider's lifetime.

    Use as a context manager; `.ragas` is what `evaluate(embeddings=...)` takes.
    Both sides of the relevancy comparison (the question and the questions
    generated from the answer) are queries, so both embed with the query task.
    """

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._runner: _LoopThread | None = None
        self._provider: EmbeddingProvider | None = None
        self.ragas: Any = None

    def __enter__(self) -> "RagasEmbeddings":
        from ragas.embeddings.base import BaseRagasEmbeddings
        from ragas.run_config import RunConfig

        from callobuzz_rag.embeddings.factory import create_embedding_provider

        runner = _LoopThread()
        self._runner = runner

        async def _make() -> "EmbeddingProvider":
            return create_embedding_provider(self._settings)

        provider = runner.run(_make())
        self._provider = provider

        async def _embed(texts: list[str]) -> list[list[float]]:
            results = await provider.embed_texts(texts, task="retrieval.query")
            return [r.vector for r in results]

        class _EngineRagasEmbeddings(BaseRagasEmbeddings):  # type: ignore[misc]
            def __init__(self) -> None:
                super().__init__()
                self.set_run_config(RunConfig())

            def embed_query(self, text: str) -> list[float]:
                return runner.run(_embed([text]))[0]

            def embed_documents(self, texts: list[str]) -> list[list[float]]:
                return runner.run(_embed(texts))

            async def aembed_query(self, text: str) -> list[float]:
                return (await runner.arun(_embed([text])))[0]

            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                return await runner.arun(_embed(texts))

        self.ragas = _EngineRagasEmbeddings()
        return self

    def __exit__(self, *exc: object) -> None:
        if self._runner is None:
            return
        if self._provider is not None:
            try:
                self._runner.run(self._provider.close())
            except Exception:
                logger.debug("Closing the evaluation embedding provider failed", exc_info=True)
        self._runner.close()
        self._runner = None


def aggregate_scores(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, float]:
    """Mean of each metric over the samples that scored, keyed by engine name.

    RAGAS records NaN for a sample whose LLM output it could not parse. Those
    are left out of the mean; a metric with no score at all is omitted rather
    than stored as NaN, which Postgres JSONB cannot hold.
    """
    totals: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row.items():
            if key not in names or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if math.isnan(float(value)):
                continue
            totals.setdefault(names[key], []).append(float(value))
    return {name: sum(values) / len(values) for name, values in totals.items()}
