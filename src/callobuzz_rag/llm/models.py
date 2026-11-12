"""Response and message models for the LLM provider layer."""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token usage from a single LLM call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    """Tokens a reasoning model spent thinking, already counted inside `completion_tokens`.
    0 when the provider does not report them."""


@dataclass(frozen=True, slots=True)
class LLMMessage:
    """A single message in a conversation."""

    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Result from a non-streaming LLM completion."""

    content: str
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    finish_reason: str = "stop"
