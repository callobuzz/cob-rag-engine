"""Watch the engine's own LiteLLM call: the exact parameters sent, every streamed chunk, timing.

The answer is produced by the engine's real code (`conversation.answering.grounded` ->
`LiteLLMProvider.complete_stream` -> `litellm.acompletion`). `instrument()` wraps
`litellm.acompletion` for the duration of a run; inside a task that has set a `Capture`
(`capturing()`), each call:

- gets the harness's thinking option and OpenRouter host pin merged in (harness mode only, see
  `thinking.py`), refusing to double-send when the engine already sent its own;
- is recorded with its parameters (the API key dropped, the messages replaced by their hash);
- has its stream observed: the response id, the first content chunk, reasoning deltas, the
  finish reason and the final usage block.

Outside a capture the wrapper is a pass-through. `inner` is the real `litellm.acompletion`, or
the fake model in a dry run, so the dry run exercises every line here.
"""

import contextlib
import hashlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from benchmarks.answer_bench.thinking import ThinkingOption, merge_body

Acompletion = Callable[..., Awaitable[Any]]


@dataclass
class Capture:
    """Filled in while one item's call runs."""

    option: ThinkingOption | None = None  # harness mode: merged into the call
    provider_body: dict[str, Any] = field(default_factory=dict)  # OpenRouter host pin
    calls: int = 0
    request_params: dict[str, Any] = field(default_factory=dict)
    started: float | None = None
    first_content_at: float | None = None
    ended: float | None = None
    response_id: str | None = None
    response_model: str | None = None
    finish_reason: str | None = None
    usage: Any = None
    reasoning_seen: bool = False
    content: list[str] = field(default_factory=list)

    def ttft_ms(self) -> int | None:
        if self.started is None or self.first_content_at is None:
            return None
        # A first token that arrived took time: under half a millisecond only rounds to 0, which
        # the verifier reads as "no TTFT" (a fast fake host in the dry run hits this).
        return max(1, round((self.first_content_at - self.started) * 1000))

    def total_ms(self) -> int:
        if self.started is None:
            return 0
        end = self.ended if self.ended is not None else time.perf_counter()
        return max(0, round((end - self.started) * 1000))


_current: ContextVar[Capture | None] = ContextVar("answer_bench_capture", default=None)


@contextlib.contextmanager
def capturing(capture: Capture) -> Iterator[Capture]:
    token = _current.set(capture)
    try:
        yield capture
    finally:
        with contextlib.suppress(ValueError):
            _current.reset(token)


def sha(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_params(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request as sent, minus the secret; messages as a hash and sizes (the prompt itself
    is reproducible from the item and `prompt_hash`)."""
    out: dict[str, Any] = {}
    for key, value in sorted(kwargs.items()):
        if key in ("api_key", "aws_secret_access_key", "headers"):
            continue
        if key == "messages":
            out["messages_sha"] = sha(value)
            out["messages_chars"] = sum(len(str(m.get("content", ""))) for m in value)
            out["messages_count"] = len(value)
            continue
        out[key] = value
    return json.loads(json.dumps(out, default=str))


class DoubleThinkingError(RuntimeError):
    """The engine sent thinking parameters itself while the harness also had one to send."""


def _thinking_keys(kwargs: dict[str, Any]) -> list[str]:
    found = [k for k in ("reasoning_effort", "thinking") if k in kwargs]
    extra = kwargs.get("extra_body") or {}
    found += [
        f"extra_body.{k}" for k in ("reasoning", "chat_template_kwargs", "thinking") if k in extra
    ]
    return found


def _prepare(capture: Capture, kwargs: dict[str, Any]) -> dict[str, Any]:
    if capture.option is not None and (capture.option.kwargs or capture.option.body):
        sent = _thinking_keys(kwargs)
        if sent:
            raise DoubleThinkingError(
                f"the engine already sends {', '.join(sent)}; the harness must not add its own "
                "(check thinking.engine_handles_thinking)"
            )
        kwargs = {**kwargs, **capture.option.kwargs}
        kwargs = merge_body(kwargs, capture.option.body)
    if capture.provider_body:
        kwargs = merge_body(kwargs, capture.provider_body)
    return kwargs


async def _observe(capture: Capture, stream: Any) -> AsyncIterator[Any]:
    try:
        async for chunk in stream:
            now = time.perf_counter()
            capture.response_id = capture.response_id or getattr(chunk, "id", None)
            capture.response_model = capture.response_model or getattr(chunk, "model", None)
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                capture.usage = usage
            choices = getattr(chunk, "choices", None) or []
            if choices:
                choice = choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    if getattr(delta, "reasoning_content", None) or getattr(
                        delta, "reasoning", None
                    ):
                        capture.reasoning_seen = True
                    text = getattr(delta, "content", None)
                    if text:
                        capture.content.append(text)
                        if capture.first_content_at is None:
                            capture.first_content_at = now
                reason = getattr(choice, "finish_reason", None)
                if reason:
                    capture.finish_reason = reason
            yield chunk
    finally:
        capture.ended = time.perf_counter()


def _note_response(capture: Capture, raw: Any) -> None:
    """A non-streamed response (the judge): id, usage, finish reason."""
    capture.ended = time.perf_counter()
    capture.response_id = getattr(raw, "id", None)
    capture.response_model = getattr(raw, "model", None)
    capture.usage = getattr(raw, "usage", None)
    choices = getattr(raw, "choices", None) or []
    if choices:
        capture.finish_reason = getattr(choices[0], "finish_reason", None)
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content:
            capture.content.append(content)
            capture.first_content_at = capture.ended


def wrap(inner: Acompletion) -> Acompletion:
    async def acompletion(**kwargs: Any) -> Any:
        capture = _current.get()
        if capture is None:
            return await inner(**kwargs)
        kwargs = _prepare(capture, kwargs)
        capture.calls += 1
        capture.request_params = safe_params(kwargs)
        capture.started = time.perf_counter()
        capture.first_content_at = capture.ended = None
        capture.content = []
        capture.usage = capture.response_id = capture.finish_reason = None
        capture.reasoning_seen = False
        result = await inner(**kwargs)
        if kwargs.get("stream"):
            return _observe(capture, result)
        _note_response(capture, result)
        return result

    acompletion.__wrapped__ = inner  # type: ignore[attr-defined]
    return acompletion


@contextlib.contextmanager
def instrument(inner: Acompletion | None = None) -> Iterator[None]:
    """Install the wrapper on `litellm.acompletion` (around `inner`, default the real one) for
    the duration of the block, and put the original back afterwards."""
    import litellm

    litellm.suppress_debug_info = True  # no "Provider List" banners for unmapped model names
    original = litellm.acompletion
    litellm.acompletion = wrap(inner or original)
    try:
        yield
    finally:
        litellm.acompletion = original
