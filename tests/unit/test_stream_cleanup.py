"""A streamed response must never leave a broken database connection behind.

After `POST /query/stream` the next request could fail with HTTP 500
"connection is closed". A client closes the connection as soon as it reads
`done`; Starlette then cancels the streaming response, and the stream was
still writing (usage metering ran AFTER `done`). The write was cut off
mid-query by the CancelledError and its connection went back to the pool dead.

Now: every write happens before the final event and is shielded, the request
session is released before streaming, the pool pre-pings, and the auth and
widget-CORS middlewares are pure ASGI (BaseHTTPMiddleware ran the endpoint in
its own task and cancel scope, which is where the traces pointed).
"""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from callobuzz_rag.api.auth import APIKeyAuthMiddleware
from callobuzz_rag.api.streaming import shielded
from callobuzz_rag.widget.security import WidgetCORSMiddleware
from tests.unit.asgi_client import call

ADMIN = "admin-secret-for-tests"


async def _write(events: list[str], name: str) -> None:
    """Stands in for a database write: it takes a moment and must not be cut off."""
    try:
        await asyncio.sleep(0.02)
        events.append(name)
    except asyncio.CancelledError:
        events.append(f"{name}:cancelled")
        raise


def _app(events: list[str]) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(admin_api_key=SecretStr(ADMIN))

    async def resource() -> AsyncIterator[str]:
        yield "session"
        await _write(events, "closed")

    async def old_order() -> AsyncIterator[str]:
        yield "event: token\ndata: {}\n\n"
        yield "event: done\ndata: {}\n\n"
        await _write(events, "metered")  # after done: what the routes used to do

    async def new_order() -> AsyncIterator[str]:
        yield "event: token\ndata: {}\n\n"
        await shielded(_write(events, "metered"))
        yield "event: done\ndata: {}\n\n"

    @app.post("/api/v1/old")
    async def old(_: str = Depends(resource)) -> StreamingResponse:
        return StreamingResponse(old_order(), media_type="text/event-stream")

    @app.post("/api/v1/query/stream")
    async def new(_: str = Depends(resource)) -> StreamingResponse:
        return StreamingResponse(new_order(), media_type="text/event-stream")

    @app.post("/api/v1/widget/chat")
    async def widget(_: str = Depends(resource)) -> StreamingResponse:
        return StreamingResponse(new_order(), media_type="text/event-stream")

    @app.get("/api/v1/state")
    async def state(request: Request) -> dict[str, Any]:
        return {"is_admin": request.state.is_admin, "corpus": request.state.corpus}

    app.add_middleware(APIKeyAuthMiddleware)
    app.add_middleware(WidgetCORSMiddleware)
    return app


async def test_base_http_middleware_cancels_a_write_after_done() -> None:
    """The mechanism: under a BaseHTTPMiddleware (what auth and widget CORS used to
    be), a client hanging up on `done` cancels the write the stream does next."""
    from starlette.middleware.base import BaseHTTPMiddleware

    class Passthrough(BaseHTTPMiddleware):
        async def dispatch(self, request: Any, call_next: Any) -> Any:
            return await call_next(request)

    events: list[str] = []
    app = _app(events)
    app.add_middleware(Passthrough)
    status, _ = await call(
        app, "POST", "/api/v1/old", headers={"X-API-Key": ADMIN}, hang_up_after=b"event: done"
    )
    assert status == 200
    await asyncio.sleep(0.1)
    assert "metered:cancelled" in events


async def test_writes_before_done_survive_a_hang_up_twenty_times() -> None:
    events: list[str] = []
    app = _app(events)
    for _ in range(20):
        status, body = await call(
            app,
            "POST",
            "/api/v1/query/stream",
            headers={"X-API-Key": ADMIN},
            hang_up_after=b"event: done",
        )
        assert status == 200 and b"event: done" in body
    await asyncio.sleep(0.1)
    assert not [e for e in events if e.endswith(":cancelled")]
    assert events.count("metered") == 20
    assert events.count("closed") == 20


async def test_widget_stream_keeps_cors_and_survives_a_hang_up() -> None:
    events: list[str] = []
    app = _app(events)
    origin = "https://shop.example"
    status, _ = await call(
        app,
        "POST",
        "/api/v1/widget/chat",
        headers={"Origin": origin},
        hang_up_after=b"event: done",
    )
    assert status == 200
    await asyncio.sleep(0.1)
    assert events == ["metered", "closed"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        res = await client.post("/api/v1/widget/chat", headers={"Origin": origin})
        assert res.headers["access-control-allow-origin"] == origin
        assert res.headers["vary"] == "Origin"
        pre = await client.options("/api/v1/widget/chat", headers={"Origin": origin})
        assert pre.status_code == 204
        assert pre.headers["access-control-allow-headers"] == "Content-Type, X-Widget-Key"
        assert pre.headers["access-control-allow-origin"] == origin


async def test_auth_state_reaches_the_route_and_401_short_circuits() -> None:
    app = _app([])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        ok = await client.get("/api/v1/state", headers={"X-API-Key": ADMIN})
        assert ok.json() == {"is_admin": True, "corpus": None}
        missing = await client.get("/api/v1/state")
        assert missing.status_code == 401 and missing.json() == {
            "detail": "API key required",
            "code": "auth_required",
        }
        wrong = await client.get("/api/v1/state", headers={"X-API-Key": "nope"})
        assert wrong.status_code == 401 and wrong.json() == {
            "detail": "Invalid API key",
            "code": "invalid_api_key",
        }


async def test_conversation_stream_uses_and_closes_its_own_session() -> None:
    from callobuzz_rag.api.services.streaming_service import stream_message_response

    session = AsyncMock()
    session.get = AsyncMock(return_value=None)  # conversation not found: short stream
    factory = MagicMock(return_value=session)
    built: list[Any] = []

    events = [
        e
        async for e in stream_message_response(
            conversation_id=MagicMock(),
            corpus_id=MagicMock(),
            content="hi",
            session_factory=factory,
            llm_provider=MagicMock(),
            make_searcher=lambda s: built.append(s),  # type: ignore[func-returns-value]
        )
    ]
    assert events and "Conversation not found" in events[0]
    factory.assert_called_once()
    session.close.assert_awaited_once()
    assert built == [session]


def test_pool_pre_pings() -> None:
    from callobuzz_rag.core.config import Settings
    from callobuzz_rag.db.engine import create_engine

    engine = create_engine(Settings())
    assert engine.pool._pre_ping is True  # type: ignore[attr-defined]
