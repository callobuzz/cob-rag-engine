"""What a streamed completion reports about its own thinking.

`LLMProvider.complete_stream` yields plain text deltas, so a reasoning model's thinking
(its `reasoning_content` deltas and its reasoning-token count) had nowhere to go. A caller
that wants it installs a `StreamStats` with `collect_stream_stats()` around the iteration;
a provider that sees reasoning records it on the installed object. Providers that know
nothing of this simply never touch it, and a caller that installs nothing pays nothing.

A context variable rather than a new parameter: `complete_stream`'s signature is the
provider contract, and every test double and third-party provider implements it.
"""

import contextlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class StreamStats:
    """Filled in by the provider while the stream is consumed."""

    started: float = 0.0
    """`time.monotonic()` when the stream was opened."""
    first_reasoning_at: float | None = None
    """When the first reasoning delta arrived (None = the model sent none)."""
    first_content_at: float | None = None
    """When the first answer token arrived."""
    reasoning_tokens: int = 0
    """From the provider's usage block, when it sends one on the stream; 0 = not reported."""
    finish_reason: str | None = None
    """Why the model stopped ("stop", "length", ...), from the last chunk that said so."""

    @property
    def thinking_ms(self) -> int | None:
        """Time from opening the stream to the first answer token, for a model that thought.

        None unless the model reported reasoning (a delta or a token count), so a plain
        model's wait for its first token is never presented as thinking.
        """
        if self.first_content_at is None:
            return None
        if self.first_reasoning_at is None and self.reasoning_tokens <= 0:
            return None
        return max(0, round((self.first_content_at - self.started) * 1000))


_current: ContextVar[StreamStats | None] = ContextVar("stream_stats", default=None)


@contextmanager
def collect_stream_stats() -> Iterator[StreamStats]:
    """Install a fresh `StreamStats` for streams consumed inside the block."""
    stats = StreamStats(started=time.monotonic())
    token = _current.set(stats)
    try:
        yield stats
    finally:
        # Closed from another task (a cancelled stream's generator is finalised by the
        # event loop): the variable then lives in a context that is already gone.
        with contextlib.suppress(ValueError):
            _current.reset(token)


def current_stream_stats() -> StreamStats | None:
    return _current.get()
