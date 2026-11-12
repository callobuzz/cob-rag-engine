"""SSE event formatting for streaming responses."""

import json
from collections.abc import Awaitable
from typing import Any

import anyio


async def shielded[T](awaitable: Awaitable[T]) -> T:
    """Finish a database write even if the client disconnects meanwhile.

    Starlette cancels a streaming response when the client goes away, and a
    client typically closes the connection as soon as it reads `done`. A write
    cut off mid-query leaves its connection broken, and the pool then hands it
    to the next request, which fails with "connection is closed". Streams do
    their writes BEFORE the final event, and through this.
    """
    with anyio.CancelScope(shield=True):
        result = await awaitable
    return result


def sse_event(event_type: str, data: dict[str, Any]) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def sse_token(content: str) -> str:
    return sse_event("token", {"content": content})


def sse_thinking(step: str) -> str:
    return sse_event("thinking", {"step": step})


def sse_tool_call(tool: str, params: dict[str, Any]) -> str:
    return sse_event("tool_call", {"tool": tool, "params": params})


def sse_tool_result(tool: str, summary: str, duration_ms: float) -> str:
    return sse_event("tool_result", {"tool": tool, "summary": summary, "duration_ms": duration_ms})


def sse_done(message_id: str, total_tokens: int, **extra: Any) -> str:
    return sse_event("done", {"message_id": message_id, "total_tokens": total_tokens, **extra})


def sse_error(detail: str) -> str:
    return sse_event("error", {"detail": detail})
