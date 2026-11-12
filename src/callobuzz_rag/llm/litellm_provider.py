"""LiteLLM implementation of the LLM provider.

Routes to 100+ backends via a unified model-string convention.
All calls are async; litellm handles retries internally.
"""

import copy
import functools
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.exceptions import LLMError
from callobuzz_rag.core.provider_order import is_openrouter, provider_body
from callobuzz_rag.llm.base import LLMProvider
from callobuzz_rag.llm.cost import CostLedger, call_cost_from_response, call_cost_from_stream
from callobuzz_rag.llm.models import LLMMessage, LLMResponse, LLMUsage
from callobuzz_rag.llm.stream_stats import StreamStats, current_stream_stats
from callobuzz_rag.llm.thinking import (
    ReasoningTextFilter,
    ThinkingAttempt,
    clean_thinking,
    family_of,
    strip_reasoning_text,
    thinking_attempts,
)

if TYPE_CHECKING:
    # Type-only: the runtime import stays inside from_resolved_config so this
    # module does not import the resolver at import time.
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

logger = logging.getLogger(__name__)

# Per (model, provider): the thinking attempts (ThinkingAttempt.key) a provider refused once.
# A refused attempt is not sent again this process; the next one in the list is.
_REASONING_REJECTED: dict[tuple[str, str | None], set[str]] = {}
_warned_fast_unset = False


def _answer_thinking(settings: Any) -> str | None:
    """RAG_LLM_THINKING, else the older RAG_LLM_REASONING_EFFORT read as a level; None = the
    provider's default. A settings stand-in (a mock) yields None."""
    return clean_thinking(getattr(settings, "llm_thinking", None)) or clean_thinking(
        getattr(settings, "llm_reasoning_effort", None)
    )


def _fast_thinking(settings: Any) -> str:
    return clean_thinking(getattr(settings, "llm_fast_thinking", None)) or "off"


class LiteLLMProvider(LLMProvider):
    """LLM provider backed by LiteLLM's acompletion API."""

    def __init__(self, settings: Settings) -> None:
        self._model = settings.llm_model
        self._api_key = settings.llm_api_key.get_secret_value()
        self._api_base = settings.llm_api_base
        self._temperature = settings.llm_temperature
        self._max_tokens = settings.llm_max_tokens
        self._provider = settings.llm_provider or None
        # Used by Epic 2-13 (Query Understanding) for cheaper classification tasks
        self._fast_model = settings.llm_fast_model or self._model
        self._fast_model_unset = not settings.llm_fast_model
        self._fast_provider_order = list(getattr(settings, "llm_fast_provider_order", []) or [])
        self._thinking = _answer_thinking(settings)
        self._fast_thinking = _fast_thinking(settings)
        self._fast_temperature = settings.llm_fast_temperature
        self._fast_max_tokens = settings.llm_fast_max_tokens
        self._timeout = settings.llm_timeout_seconds
        self._fast_timeout = settings.llm_fast_timeout_seconds
        self.cost_ledger = CostLedger()
        self.payer = "operator"

    @classmethod
    def from_resolved_config(cls, config: "ResolvedLLMConfig") -> "LiteLLMProvider":
        """Create a LiteLLMProvider from a resolved config.

        Used by the query pipeline to construct per-request providers
        with corpus-specific or request-level LLM configuration.
        """
        from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

        if not isinstance(config, ResolvedLLMConfig):
            raise TypeError(f"Expected ResolvedLLMConfig, got {type(config).__name__}")

        instance = cls.__new__(cls)
        instance._model = config.model
        instance._api_key = config.api_key
        instance._api_base = config.base_url
        instance._temperature = config.temperature
        instance._max_tokens = config.max_tokens
        instance._provider = config.provider
        instance._fast_model = config.fast_model or config.model
        instance._fast_model_unset = not config.fast_model
        instance._fast_provider_order = list(config.fast_provider_order)
        instance._thinking = clean_thinking(config.thinking)
        instance._fast_thinking = clean_thinking(config.fast_thinking) or "off"
        instance._fast_temperature = config.fast_temperature
        instance._fast_max_tokens = config.fast_max_tokens
        instance._timeout = config.timeout
        instance._fast_timeout = config.fast_timeout
        instance.cost_ledger = CostLedger()
        instance.payer = config.payer
        return instance

    def for_model(self, model: str, thinking: str | None = None) -> "LiteLLMProvider":
        """The same provider answering with `model` at `thinking`: a copy that shares this
        one's cost ledger (a turn is billed for every call, whichever model made it), key,
        endpoint and fast model. Language routing uses it so one turn can answer on the model
        chosen for the visitor's language; `thinking` is the level that model is called at
        (`llm/thinking.py` maps it to the model's own parameters, or to its minimum when the
        family cannot be switched off)."""
        clone = copy.copy(self)
        clone._model = model
        clone._thinking = clean_thinking(thinking)
        return clone

    @property
    def fast_model(self) -> str:
        """The model to use for cheap auxiliary calls (classification, variants).

        Falls back to the main model when no cheap model is configured.
        """
        return self._fast_model

    def fast_call_options(self) -> dict[str, Any]:
        """Query understanding at temperature 0, capped when the model allows it.

        The cap is applied ONLY to a model LiteLLM knows and reports as
        non-reasoning. A reasoning model spends its budget thinking before it
        answers, so a tight cap truncates it: the JSON comes back empty,
        classification silently defaults to "simple", the variants list is
        empty, and recall drops with nothing logged above a warning. The engine's
        own `.env.example` names `openai/gpt-oss-20b` as the fast model, which is
        exactly such a model — so the cap cannot be unconditional. An unknown
        model is treated as possibly reasoning and left uncapped.

        Thinking is the fast model's own level (RAG_LLM_FAST_THINKING, default "off", or the
        family's minimum where it cannot be off): rewriting a search string needs no
        deliberation, and a thinking model otherwise spends seconds on it. When no fast model
        is configured the main model does the job at that level, and a warning says so once
        when it is a thinking model, because pointing RAG_LLM_FAST_MODEL at a small model is
        the better fix.
        """
        options: dict[str, Any] = {
            "model": self._fast_model,
            "temperature": self._fast_temperature,
            # Query understanding is optional: a provider that hangs costs this
            # long, then the query goes ahead without it, instead of blocking
            # the request (and a stream's first byte) for the full LLM timeout.
            "timeout": self._fast_timeout,
        }
        if _is_known_non_reasoning(self._fast_model, self._provider):
            options["max_tokens"] = self._fast_max_tokens
        options["thinking"] = self._fast_thinking
        if self._fast_model_unset:
            first = self._attempts(self._fast_model, self._fast_thinking)[0]
            if first.sends:
                _warn_fast_model_unset(self._fast_model, first.effective)
        return options

    def is_known_non_reasoning(self, model: str | None = None) -> bool:
        """See `LLMProvider.is_known_non_reasoning` (LiteLLM's model map)."""
        return _is_known_non_reasoning(model or self._model, self._provider)

    def helper_thinking(self, model: str | None = None) -> str | None:
        """The thinking level for a helper call (rewrite, classify, re-rank): the fast
        model's level (RAG_LLM_FAST_THINKING / llm_config.fast_thinking), never the knowledge
        base's answer level, whichever model does the helper work."""
        return self._fast_thinking

    def thinking_effective(self, model: str | None = None, level: str | None = None) -> str:
        """The thinking a call on `model` (default: the answer model) at `level` (default: its
        configured level) runs at now: off, minimal, low, medium, high, on (switched on at the
        model's own depth) or provider-default. Refusals seen so far are taken into account."""
        target = model or self._model
        return self._attempts(target, level or self._level(target, None))[0].effective

    def _level(self, model: str, explicit: str | None) -> str | None:
        """The thinking level of a call: the caller's, else the answer level on the answer
        model. A helper call on another model gets only what its caller passes."""
        level = clean_thinking(explicit)
        if level is None and model == self._model:
            level = self._thinking
        return level

    def _attempts(self, model: str, level: str | None) -> list[ThinkingAttempt]:
        """The thinking attempts still allowed for `model` at `level`, best first; the last
        sends nothing."""
        known = family_of(model) is not None
        attempts = thinking_attempts(
            model,
            openrouter=is_openrouter(self._provider, model),
            level=level,
            reasoning_known=known or _supports_reasoning(model, self._provider),
        )
        refused = _REASONING_REJECTED.get((model, self._provider), set())
        return [a for a in attempts if not a.key or a.key not in refused]

    @property
    def provider_name(self) -> str | None:
        """The LiteLLM provider this instance calls (`ollama_chat` for the free local model)."""
        return self._provider

    @property
    def has_credentials(self) -> bool:
        """Whether this provider has an API key to call with.

        The engine ships with no built-in key: callers supply one per request.
        Auxiliary features check this and skip rather than issue a call that is
        certain to 401, which would otherwise cost a round-trip on every query.
        """
        return bool(self._api_key)

    # ------------------------------------------------------------------
    # ABC implementation
    # ------------------------------------------------------------------

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
        """Run a standard chat completion. `thinking` (or the older `reasoning_effort`, read as
        a level) overrides the thinking level for this call."""
        raw = await self._call(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_kwargs={"timeout": timeout} if timeout is not None else None,
            thinking=thinking or reasoning_effort,
        )
        return self._parse_response(raw)

    async def complete_stream(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream content deltas from a chat completion. Reasoning never reaches the deltas:
        a separate reasoning field is only timed (`StreamStats`) and counted, and reasoning
        written inline (<think>...</think>) is filtered out."""
        import litellm  # deferred import

        base = self._base_kwargs(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        attempts = self._attempts(base["model"], self._level(base["model"], thinking))
        try:
            # The final chunk then carries the provider's usage (and, on OpenRouter, the
            # cost it charged), which is what the request's cost is recorded from.
            extra = {"stream": True, "stream_options": {"include_usage": True}}
            stream, kwargs, used = await self._send(litellm, base, attempts, extra)
            stats = current_stream_stats()
            usage: Any = None
            parts: list[str] = []
            visible = ReasoningTextFilter()
            try:
                async for chunk in stream:
                    if stats is not None:
                        _note_stream_chunk(stats, chunk)
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage = chunk_usage
                    choices = chunk.choices
                    delta = (choices[0].delta.content or "") if choices else ""
                    if delta:
                        shown = visible.feed(delta)
                        if shown:
                            parts.append(shown)
                            yield shown
                tail = visible.flush()
                if tail:
                    parts.append(tail)
                    yield tail
            finally:
                self._record_stream(kwargs, usage, "".join(parts), used.effective)
        except Exception as exc:
            raise LLMError(f"LLM streaming failed: {exc}") from exc

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
        """Run a completion with JSON response_format enforced."""
        extra: dict[str, Any] = {"response_format": {"type": "json_object"}}
        if timeout is not None:
            extra["timeout"] = timeout
        raw = await self._call(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_kwargs=extra,
            thinking=thinking or reasoning_effort,
        )
        return self._parse_response(raw)

    async def health_check(self) -> bool:
        """Verify the LLM backend is reachable with a minimal call."""
        try:
            resp = await self.complete(
                [LLMMessage(role="user", content="ping")],
                max_tokens=5,
            )
            return bool(resp.content)
        except LLMError:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_kwargs(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """The keyword args for litellm.acompletion, without any thinking parameter."""
        kwargs: dict[str, Any] = {
            "model": model or self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": (temperature if temperature is not None else self._temperature),
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            # LiteLLM waits 6000s by default; a hung provider must fail instead.
            "timeout": self._timeout,
        }
        if self._api_key:
            kwargs["api_key"] = self._api_key
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._provider:
            # Explicit provider beats LiteLLM's `provider/model` prefix inference,
            # so a caller may send a bare model name alongside its provider id and
            # cannot land on the wrong backend with the right key.
            kwargs["custom_llm_provider"] = self._provider
        # The fast model's OpenRouter host pin, on calls to that model only. With no fast model
        # set the main model does the helper work, on whatever host its answers use.
        if (
            self._fast_provider_order
            and not self._fast_model_unset
            and kwargs["model"] == self._fast_model
            and is_openrouter(self._provider, kwargs["model"])
        ):
            kwargs["extra_body"] = provider_body(self._fast_provider_order)
        return kwargs

    @staticmethod
    def _with_thinking(base: dict[str, Any], attempt: ThinkingAttempt) -> dict[str, Any]:
        """`base` plus one thinking attempt: its kwargs as they are, its body merged into
        `extra_body` beside anything already there (the host pin)."""
        kwargs = {**base, **attempt.kwargs}
        if attempt.body:
            kwargs["extra_body"] = {**base.get("extra_body", {}), **attempt.body}
        return kwargs

    def _build_kwargs(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        reasoning_effort: str | None = None,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        """The keyword args for litellm.acompletion with the first thinking attempt applied.

        The level is the call's own (`thinking`, or the older `reasoning_effort`), else the
        knowledge base's answer level on the answer model; a helper call on another model
        gets only what its caller passes. `llm/thinking.py` turns it into the family's
        parameters; a model that does not think gets none.
        """
        base = self._base_kwargs(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        level = self._level(base["model"], thinking or reasoning_effort)
        return self._with_thinking(base, self._attempts(base["model"], level)[0])

    def _refused(self, model: str, attempt: ThinkingAttempt, error: Exception) -> None:
        """The provider refused this thinking attempt: remember it, so the next is sent."""
        _REASONING_REJECTED.setdefault((model, self._provider), set()).add(attempt.key)
        logger.warning(
            "%s refused thinking parameters %s (%s); sending the next option and not this one "
            "again",
            model,
            attempt.key,
            str(error)[:300],
        )

    async def _send(
        self,
        litellm: Any,
        base: dict[str, Any],
        attempts: list[ThinkingAttempt],
        extra: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any], ThinkingAttempt]:
        """Call litellm with the first attempt; on a refusal of the thinking parameters (and
        only that: a rate limit, timeout or bad key surfaces), with the next one. Returns the
        reply, the kwargs it was made with and the attempt that was accepted."""
        # L5a demo: the hard daily ceiling on model calls (free when demo mode is off)
        from callobuzz_rag.demo import budget as demo_budget

        await demo_budget.check_model_call(self._provider, str(base.get("model") or ""))
        # /L5a demo
        for index, attempt in enumerate(attempts):
            kwargs = self._with_thinking(base, attempt)
            try:
                raw = await litellm.acompletion(**kwargs, **(extra or {}))
            except Exception as exc:
                last = index == len(attempts) - 1
                if last or not attempt.sends or not _is_reasoning_param_error(exc):
                    raise
                self._refused(base["model"], attempt, exc)
                continue
            return raw, kwargs, attempt
        raise LLMError("no thinking attempt left")  # unreachable: the last attempt raises

    async def _call(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        extra_kwargs: dict[str, Any] | None = None,
        thinking: str | None = None,
    ) -> Any:
        """Execute litellm.acompletion with error mapping."""
        import litellm  # deferred import

        base = self._base_kwargs(
            messages, model=model, temperature=temperature, max_tokens=max_tokens
        )
        if extra_kwargs:
            base.update(extra_kwargs)
        attempts = self._attempts(base["model"], self._level(base["model"], thinking))

        logger.debug("LLM call: model=%s tokens=%s", base["model"], base["max_tokens"])
        try:
            raw, _, used = await self._send(litellm, base, attempts)
        except Exception as exc:
            raise LLMError(f"LLM completion failed: {exc}") from exc
        self._record_call(raw, base["model"], used.effective)
        return raw

    def _record_call(self, raw: Any, model: str, thinking: str | None = None) -> None:
        """Put one finished call in the request's cost ledger. Never raises: a price that
        cannot be read is an unknown cost, not a failed answer."""
        try:
            self.cost_ledger.add(
                call_cost_from_response(raw, model, self._provider, thinking=thinking),
                answer=model == self._model,
            )
        except Exception:
            logger.warning("Could not record the cost of a %s call", model, exc_info=True)

    def _record_stream(
        self, kwargs: dict[str, Any], usage: Any, answer: str, thinking: str | None = None
    ) -> None:
        """Record a streamed call: the usage chunk when the provider sent one, else estimated
        token counts priced from a price list only."""
        try:
            from callobuzz_rag.analytics.usage import estimate_tokens

            prompt_estimate = sum(
                estimate_tokens(str(m.get("content", ""))) for m in kwargs["messages"]
            )
            self.cost_ledger.add(
                call_cost_from_stream(
                    usage,
                    kwargs["model"],
                    self._provider,
                    prompt_estimate=prompt_estimate,
                    completion_estimate=estimate_tokens(answer),
                    thinking=thinking,
                ),
                answer=kwargs["model"] == self._model,
            )
        except Exception:
            logger.warning("Could not record the cost of a streamed call", exc_info=True)

    @staticmethod
    def _parse_response(raw: Any) -> LLMResponse:
        """Convert a litellm ModelResponse to our LLMResponse."""
        choice = raw.choices[0]
        usage_data = raw.usage if raw.usage else None
        reasoning_tokens = getattr(
            getattr(usage_data, "completion_tokens_details", None), "reasoning_tokens", 0
        )
        usage = (
            LLMUsage(
                prompt_tokens=getattr(usage_data, "prompt_tokens", 0),
                completion_tokens=getattr(usage_data, "completion_tokens", 0),
                total_tokens=getattr(usage_data, "total_tokens", 0),
                # LiteLLM: usage.completion_tokens_details.reasoning_tokens
                reasoning_tokens=reasoning_tokens if isinstance(reasoning_tokens, int) else 0,
            )
            if usage_data
            else LLMUsage()
        )
        return LLMResponse(
            # Reasoning a host writes inline never reaches the answer.
            content=strip_reasoning_text(choice.message.content or ""),
            model=raw.model or "",
            usage=usage,
            finish_reason=choice.finish_reason or "stop",
        )


def _note_stream_chunk(stats: StreamStats, chunk: Any) -> None:
    """Record on `stats` what one streamed chunk says about thinking and its token count."""
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        tokens = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0)
        if isinstance(tokens, int) and tokens > 0:
            stats.reasoning_tokens = tokens
    choices = getattr(chunk, "choices", None)
    if not choices:
        return
    reason = getattr(choices[0], "finish_reason", None)
    if isinstance(reason, str) and reason:
        stats.finish_reason = reason
    delta = choices[0].delta
    now = time.monotonic()
    if stats.first_reasoning_at is None and (
        getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
    ):
        stats.first_reasoning_at = now
    if stats.first_content_at is None and getattr(delta, "content", None):
        stats.first_content_at = now


@functools.lru_cache(maxsize=64)
def _is_known_non_reasoning(model: str, provider: str | None) -> bool:
    """True only when LiteLLM's model map knows `model` and says it does not reason.

    Cached: the answer is static per model string, and this runs on every
    retrieval. Any failure — an unmapped model, a LiteLLM API change — answers
    False, which leaves the call uncapped: slower, never truncated.
    """
    import litellm  # deferred import, as everywhere in this module

    try:
        litellm.get_model_info(model=model, custom_llm_provider=provider)
        return not litellm.supports_reasoning(model=model, custom_llm_provider=provider)
    except Exception:
        return False


@functools.lru_cache(maxsize=64)
def _supports_reasoning(model: str, provider: str | None) -> bool:
    """True only when LiteLLM's model map says `model` reasons. Unknown models are False,
    so thinking effort is never sent to a model nobody can vouch for."""
    import litellm  # deferred import, as everywhere in this module

    try:
        return bool(litellm.supports_reasoning(model=model, custom_llm_provider=provider))
    except Exception:
        return False


def _is_reasoning_param_error(exc: Exception) -> bool:
    """True when the provider refused the thinking parameter itself: LiteLLM's
    UnsupportedParamsError, or a 400 whose text names reasoning or thinking."""
    import litellm  # deferred import, as everywhere in this module

    if isinstance(exc, litellm.UnsupportedParamsError):
        return True
    if isinstance(exc, litellm.BadRequestError):
        text = str(exc).lower()
        return "reasoning" in text or "thinking" in text
    return False


def _warn_fast_model_unset(model: str, effort: str) -> None:
    global _warned_fast_unset
    if _warned_fast_unset:
        return
    _warned_fast_unset = True
    logger.warning(
        "RAG_LLM_FAST_MODEL is not set and %s is a reasoning model: question rewriting and "
        "classification use it at '%s' thinking. Set RAG_LLM_FAST_MODEL to a small, "
        "quick model from the same provider to make them faster still.",
        model,
        effort,
    )
