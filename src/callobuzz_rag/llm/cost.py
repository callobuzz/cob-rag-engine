"""What an LLM request really cost: per-call capture, summed per request.

The engine never guesses a price it cannot back. A call's cost is, in order:

1. the provider's own figure for that response (OpenRouter returns `usage.cost`, the amount it
   charged; LiteLLM carries it on the response), then
2. LiteLLM's price map (`litellm.cost_per_token`), then
3. the engine's small price table (`analytics.cost_tracker`),
4. otherwise **unknown** (`None`). Unknown is never reported as zero: a total that contains
   an unknown call says so (`RequestCost.cost_known`), and screens print "price unknown".

One `CostLedger` lives on each per-request `LiteLLMProvider`, and everything that request
asks of the model (the answer, query rewrites, classification, a rerank by LLM, validation)
goes through that provider, so the ledger is the whole request.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MICRO = 1_000_000


def usd_to_micro(usd: float) -> int:
    """USD as an integer count of millionths of a dollar."""
    return round(usd * MICRO)


@dataclass(frozen=True, slots=True)
class CallCost:
    """One model call. `cost_usd` is None when no price could be found. `thinking` is the
    thinking the call ran at (`LiteLLMProvider.thinking_effective`), None when not known."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None
    thinking: str | None = None


@dataclass(frozen=True, slots=True)
class RequestCost:
    """Everything one answered request spent. `cost_micro` sums the calls whose price is
    known; `cost_known` is False when any call was not priced (the sum is then a floor)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_micro: int = 0
    cost_known: bool = True
    calls: int = 0
    thinking_effective: str | None = None
    """The thinking the answer model ran at in this request (its last call): off, minimal,
    low, medium, high, on or provider-default. None when no answer-model call was made."""

    @property
    def cost_usd(self) -> float | None:
        return self.cost_micro / MICRO if self.cost_known else None

    def as_dict(self) -> dict[str, Any]:
        """The fields added to a response's `token_usage` / a stream's `done.usage`.
        Reasoning tokens are already inside the completion tokens and the cost: thinking is
        paid for even though its text is never shown."""
        out: dict[str, Any] = {
            "reasoning_tokens": self.reasoning_tokens,
            "cost_usd": self.cost_usd,
            "cost_known": self.cost_known,
        }
        if self.thinking_effective is not None:
            out["thinking_effective"] = self.thinking_effective
        return out


@dataclass(slots=True)
class CostLedger:
    """Accumulates the calls of one request."""

    calls: list[CallCost] = field(default_factory=list)
    answer_thinking: str | None = None

    def add(self, call: CallCost, *, answer: bool = False) -> None:
        """Record a call; `answer` marks a call on the answer model, whose thinking is the
        request's `thinking_effective`."""
        self.calls.append(call)
        if answer and call.thinking is not None:
            self.answer_thinking = call.thinking

    def summary(self) -> RequestCost:
        known = [c for c in self.calls if c.cost_usd is not None]
        return RequestCost(
            prompt_tokens=sum(c.prompt_tokens for c in self.calls),
            completion_tokens=sum(c.completion_tokens for c in self.calls),
            reasoning_tokens=sum(c.reasoning_tokens for c in self.calls),
            cost_micro=sum(usd_to_micro(c.cost_usd or 0.0) for c in known),
            cost_known=len(known) == len(self.calls),
            calls=len(self.calls),
            thinking_effective=self.answer_thinking,
        )


def request_cost_of(llm: Any) -> RequestCost | None:
    """What this request's LLM provider has spent so far: every call it made, helpers
    included. None when `llm` keeps no ledger (a stand-in, another provider) or has made no
    call yet (a cache hit)."""
    ledger = getattr(llm, "cost_ledger", None)
    if not isinstance(ledger, CostLedger) or not ledger.calls:
        return None
    return ledger.summary()


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            out = dump()
            return out if isinstance(out, dict) else {}
        except Exception:
            return {}
    return {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if value >= 0 else None


def usage_counts(usage: Any) -> tuple[int, int, int]:
    """(prompt, completion, reasoning) tokens of a provider usage object or dict."""

    def count(v: Any) -> int:
        return int(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0

    details = _get(usage, "completion_tokens_details")
    return (
        count(_get(usage, "prompt_tokens")),
        count(_get(usage, "completion_tokens")),
        count(_get(details, "reasoning_tokens")),
    )


def provider_reported_cost(usage: Any) -> float | None:
    """The amount the provider says it charged for this response, from its usage object.

    OpenRouter puts it in `usage.cost`. For a request billed to the caller's own upstream
    key (`is_byok`), OpenRouter's own charge is only its fee and the model's price is
    `cost_details.upstream_inference_cost`: both are the caller's spend, so they are added.
    """
    data = _as_dict(usage)
    cost = _number(_get(usage, "cost"))
    if cost is None:
        cost = _number(data.get("cost"))
    if cost is None:
        return None
    if data.get("is_byok") or _get(usage, "is_byok") is True:
        details = _get(usage, "cost_details") or data.get("cost_details")
        upstream = _number(_get(details, "upstream_inference_cost"))
        if upstream is not None:
            cost += upstream
    return cost


def _header_cost(raw: Any) -> float | None:
    hidden = getattr(raw, "_hidden_params", None)
    if not isinstance(hidden, dict):
        return None
    headers = hidden.get("additional_headers")
    if isinstance(headers, dict):
        value = _number(headers.get("llm_provider-x-litellm-response-cost"))
        if value is not None:
            return value
    return _number(hidden.get("response_cost"))


def bare_model(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith("openrouter/") else model


def priced_cost(model: str, provider: str | None, prompt: int, completion: int) -> float | None:
    """Cost from a price list (LiteLLM's map, then the engine table), None when neither has
    the model. Never 0.0 for an unpriced model (except when no tokens were used)."""
    if prompt == 0 and completion == 0:
        return 0.0
    # PIP2 local
    if provider == "ollama_chat":
        return 0.0  # the free local model: priced, and the price is nothing
    # /PIP2 local
    try:
        import litellm  # deferred import

        names = [model]
        if provider and not model.startswith(f"{provider}/"):
            names.append(f"{provider}/{model}")
        for name in names:
            try:
                p, c = litellm.cost_per_token(
                    model=name,
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    custom_llm_provider=provider if name == model else None,
                )
            except Exception:
                continue
            total = _number(p + c)
            if total is not None:
                return total
    except Exception:
        logger.debug("litellm price lookup failed for %s", model, exc_info=True)
    from callobuzz_rag.analytics.cost_tracker import CostTracker

    tracker = CostTracker()
    for name in (bare_model(model), model):
        if tracker.has_price(name):
            return tracker.estimate_cost(name, prompt, completion)
    return None


def call_cost_from_response(
    raw: Any, model: str, provider: str | None, *, thinking: str | None = None
) -> CallCost:
    """The CallCost of a finished (non-streamed) litellm response."""
    usage = _get(raw, "usage")
    prompt, completion, reasoning = usage_counts(usage)
    cost = provider_reported_cost(usage)
    if cost is None:
        cost = _header_cost(raw)
    if cost is None:
        cost = priced_cost(model, provider, prompt, completion)
    return CallCost(model, prompt, completion, reasoning, cost, thinking)


def call_cost_from_stream(
    usage: Any,
    model: str,
    provider: str | None,
    *,
    prompt_estimate: int,
    completion_estimate: int,
    thinking: str | None = None,
) -> CallCost:
    """The CallCost of a streamed call from its final usage chunk. Without one, token counts
    are estimates and the cost comes from a price list only (never from the provider)."""
    if usage is not None:
        prompt, completion, reasoning = usage_counts(usage)
        if prompt or completion:
            cost = provider_reported_cost(usage)
            if cost is None:
                cost = priced_cost(model, provider, prompt, completion)
            return CallCost(model, prompt, completion, reasoning, cost, thinking)
    cost = priced_cost(model, provider, prompt_estimate, completion_estimate)
    return CallCost(model, prompt_estimate, completion_estimate, 0, cost, thinking)
