"""Abstract base class for LLM providers."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from callobuzz_rag.llm.models import LLMMessage, LLMResponse


class LLMProvider(ABC):
    """Interface for large-language-model completions.

    All implementations must handle retries, timeouts, and error mapping
    internally — callers should only see LLMError on failure.
    """

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        thinking: str | None = None,
    ) -> LLMResponse:
        """Run a chat completion and return the full response.

        `timeout` (seconds) overrides the provider's default for this call. `thinking`
        ("off" | "minimal" | "low" | "medium" | "high" | "provider-default") overrides the
        thinking level for this call; `reasoning_effort` is its older name ("none" = "off").
        Providers send nothing to models that do not think.
        """

    @abstractmethod
    def complete_stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Stream a chat completion, yielding content deltas."""

    @abstractmethod
    async def complete_json(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        thinking: str | None = None,
    ) -> LLMResponse:
        """Run a completion with JSON-mode enforced.

        The caller is responsible for including a JSON schema instruction
        in the system prompt. The provider sets response_format accordingly.
        """

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the LLM backend is reachable."""

    @property
    def fast_model(self) -> str | None:
        """Cheap model for auxiliary calls, or None to use the main model.

        Query understanding (classification, decomposition, query variants) is
        rewriting a search string, not reasoning — it does not need the answer
        model. Implementations that have no notion of a cheap model return None,
        and callers pass that straight through as "use the default".
        """
        return None

    def fast_call_options(self) -> dict[str, Any]:
        """Keyword arguments for a query-understanding call.

        The default only routes to `fast_model`; LiteLLMProvider adds the
        temperature and token cap. Callers spread this into `complete_json`, so
        every understanding call gets the same treatment from one place.
        """
        return {"model": self.fast_model}

    def helper_thinking(self, model: str | None = None) -> str | None:
        """Thinking level for a helper call (rewrite, classify, re-rank) on `model`: the fast
        model's level, never the knowledge base's answer level. None = send nothing."""
        return None

    def is_known_non_reasoning(self, model: str | None = None) -> bool:
        """True only when `model` (default: the answer model) is known not to reason.

        A reasoning model spends its token budget thinking before it answers, so a tight
        `max_tokens` leaves an empty reply; callers cap a reply only when this is True.
        Unknown models and providers with no model map answer False (uncapped: slower,
        never truncated).
        """
        return False

    @property
    def has_credentials(self) -> bool:
        """Whether a credential is available to make a call.

        The engine holds no LLM key of its own; callers supply one per request.
        Optional features consult this to skip cleanly instead of issuing a call
        that cannot succeed.
        """
        return True
