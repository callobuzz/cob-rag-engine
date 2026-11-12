"""Drive an ASGI app like uvicorn does, including a client that hangs up early.

httpx's ASGITransport never reports a disconnect, so it cannot show what
happens when a client closes the connection the moment it has what it wants
(an SSE client reading `done`). This does: once a body chunk containing
`hang_up_after` has been sent, `receive()` answers `http.disconnect`, which
makes Starlette cancel the streaming response.
"""

import asyncio
import json
from typing import Any


async def call(
    app: Any,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    hang_up_after: bytes | None = None,
    hang_up_delay: float = 0.005,
) -> tuple[int, bytes]:
    body = json.dumps(json_body).encode() if json_body is not None else b""
    hdrs = {"content-type": "application/json", **(headers or {})}
    gone = asyncio.Event()
    status = 0
    chunks: list[bytes] = []
    sent_request = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            chunks.append(chunk)
            if not message.get("more_body", False):
                gone.set()
            elif hang_up_after is not None and hang_up_after in chunk:
                # The FIN reaches the server a moment later, while the app is
                # already doing whatever it does after that chunk.
                asyncio.get_running_loop().call_later(hang_up_delay, gone.set)

    scope = {
        "type": "http",
        # What uvicorn 0.54 announces; below 2.4 Starlette's StreamingResponse
        # watches receive() for the disconnect.
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in hdrs.items()],
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    return status, b"".join(chunks)
