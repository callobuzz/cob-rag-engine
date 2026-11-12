"""`build_request_options(model, thinking, provider)`: what to send for a thinking level.

The harness asks for a thinking level (`off | minimal | low | medium | high | provider-default`)
per variant. Who turns that into request parameters depends on what the engine can do:

1. **engine**: the engine's `ResolvedLLMConfig` has a `thinking` field (RAG_LLM_THINKING): the
   level goes into the provider's config and the engine's own `LiteLLMProvider` sends it, with
   its own fallbacks. The harness adds nothing; `capture` records what the engine sent.
2. **harness**: an engine without that field. The options below are merged into the engine's
   LiteLLM call by `capture.instrument` (and recorded). Their source is the engine's
   `callobuzz_rag.llm.thinking.thinking_attempts` when that module exists, else the minimal
   mapping in this file: OpenRouter's unified `reasoning` object
   (openrouter.ai/docs/use-cases/reasoning-tokens), or LiteLLM's `reasoning_effort` elsewhere.

Each option is one attempt; the list is best first and ends with an attempt that sends nothing.
When a provider refuses an attempt (HTTP 400 naming reasoning/thinking) the runner moves on to
the next and remembers that for the rest of the model's items.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import Any, Literal

THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "provider-default")
Source = Literal["engine", "harness"]


@dataclass(frozen=True)
class ThinkingOption:
    """One way to ask for a level. `kwargs` go to litellm.acompletion as they are; `body` is
    merged into its `extra_body` (the request JSON)."""

    key: str
    effective: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestOptions:
    source: Source
    level: str
    attempts: list[ThinkingOption]


def check_level(level: str) -> str:
    if level not in THINKING_LEVELS:
        raise ValueError(f"thinking must be one of {', '.join(THINKING_LEVELS)}, not {level!r}")
    return level


def engine_handles_thinking() -> bool:
    """True when the engine's resolved LLM config carries a thinking level of its own."""
    from callobuzz_rag.core.config_resolver import ResolvedLLMConfig

    return "thinking" in {f.name for f in dataclasses.fields(ResolvedLLMConfig)}


def is_openrouter(provider: str | None, model: str) -> bool:
    return provider == "openrouter" or (not provider and model.startswith("openrouter/"))


def _minimal(model: str, level: str, openrouter: bool) -> list[ThinkingOption]:
    """The fallback mapping: OpenRouter's `reasoning` object, else `reasoning_effort`."""
    nothing = ThinkingOption("", "provider-default")
    if level == "provider-default":
        return [nothing]
    if openrouter:
        reasoning: dict[str, Any] = {"enabled": False} if level == "off" else {"effort": level}
        key = "or:" + ",".join(f"{k}={v}" for k, v in sorted(reasoning.items()))
        return [ThinkingOption(key, level, body={"reasoning": reasoning}), nothing]
    effort = "none" if level == "off" else level
    return [ThinkingOption(f"effort={effort}", level, kwargs={"reasoning_effort": effort}), nothing]


def _from_engine_module(model: str, level: str, openrouter: bool) -> list[ThinkingOption] | None:
    try:
        from callobuzz_rag.llm import thinking as engine_thinking  # type: ignore[attr-defined]
    except ImportError:
        return None
    attempts = engine_thinking.thinking_attempts(
        model, openrouter=openrouter, level=level, reasoning_known=True
    )
    return [ThinkingOption(a.key, a.effective, dict(a.kwargs), dict(a.body)) for a in attempts]


def build_request_options(model: str, thinking: str, provider: str | None) -> RequestOptions:
    """The thinking attempts for `model` at level `thinking`, and who sends them."""
    level = check_level(thinking)
    if engine_handles_thinking():
        return RequestOptions("engine", level, [ThinkingOption("", level)])
    openrouter = is_openrouter(provider, model)
    attempts = _from_engine_module(model, level, openrouter) or _minimal(model, level, openrouter)
    return RequestOptions("harness", level, attempts)


def merge_body(kwargs: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """`kwargs` with `body` merged into `extra_body` (one level deep, `body` wins)."""
    if not body:
        return kwargs
    extra = dict(kwargs.get("extra_body") or {})
    for k, v in body.items():
        if isinstance(v, dict) and isinstance(extra.get(k), dict):
            extra[k] = {**extra[k], **v}
        else:
            extra[k] = v
    return {**kwargs, "extra_body": extra}
