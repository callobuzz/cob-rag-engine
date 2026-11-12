"""The demo's answer limits on the website widget, and the one thing every streamed surface
relies on: the turn's scope reaches the model-call counter through a real streamed response."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.responses import StreamingResponse

from callobuzz_rag.demo import budget, limits
from tests.unit import test_widget as _w
from tests.unit.accounts_fakes import FakeRedis

SITE = _w.SITE
_key, _session, _use_session, _client = _w._key, _w._session, _w._use_session, _w._client
app = _w.app
corpus = _w.corpus

pytestmark = pytest.mark.usefixtures("unlimited_plan")


@pytest.fixture(autouse=True)
def _no_scope() -> Any:
    limits.set_scope(None)
    yield
    limits.set_scope(None)


def _demo(app: Any, **over: Any) -> FakeRedis:
    redis = FakeRedis()
    update = {
        "demo_mode": True,
        "demo_answers_per_visitor_per_day": 2,
        "demo_reset_at_utc": "21:30",
        "session_secret": SecretStr("s0"),
        **over,
    }
    app.state.settings = app.state.settings.model_copy(update=update)
    app.state.demo_redis = redis
    return redis


async def _chat(app: Any, corpus: Any) -> Any:
    _use_session(app, _session(_key(corpus.id), corpus))
    async with await _client(app) as c:
        return await c.post(
            "/api/v1/widget/chat",
            json={"message": "What are your timings?"},
            headers={"X-Widget-Key": "pk_x", "Origin": SITE},
        )


async def _fill(redis: FakeRedis, app: Any) -> None:
    lim = limits.limiter_from(redis, app.state.settings)
    visitor = limits.visitor_hash("s0", "127.0.0.1", None)
    for _ in range(2):
        await lim.reserve(visitor)


async def test_the_widget_says_try_tomorrow_to_a_visitor_over_their_quota(app, corpus) -> None:
    redis = _demo(app)
    await _fill(redis, app)
    res = await _chat(app, corpus)  # the assistant is off in this test: the legacy path
    assert res.status_code == 429, res.text
    body = res.json()
    assert body["code"] == "demo_limit"
    assert body["detail"].startswith("You've used today's demo questions (2).")
    assert "http" not in body["detail"]


async def test_the_widget_says_it_when_the_whole_demo_is_used_up(app, corpus) -> None:
    redis = _demo(app, demo_answers_per_day=1)
    lim = limits.limiter_from(redis, app.state.settings)
    await lim.reserve("someone-else")
    res = await _chat(app, corpus)
    assert res.status_code == 429 and res.json()["code"] == "demo_limit"
    assert res.json()["detail"].startswith("Today's demo answers are all used up.")


async def test_the_widget_says_it_when_the_counters_are_down(app, corpus) -> None:
    redis = _demo(app)
    redis.down = True
    res = await _chat(app, corpus)
    assert res.status_code == 503 and res.json()["code"] == "demo_unavailable"
    assert "right now" in res.json()["detail"]


@pytest.mark.assistant_on
async def test_with_the_assistant_on_the_widget_asks_the_director_for_the_polite_reply(
    app, corpus
) -> None:
    from callobuzz_rag.conversation import surfaces

    redis = _demo(app)
    await _fill(redis, app)
    _use_session(app, _session(_key(corpus.id), corpus))
    reply = AsyncMock(return_value=MagicMock(status_code=200))
    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "is_human_turn", lambda *_: False),
        patch.object(surfaces, "widget_reply", reply),
    ):
        async with await _client(app) as c:
            await c.post(
                "/api/v1/widget/chat",
                json={"message": "What are your timings?"},
                headers={"X-Widget-Key": "pk_x", "Origin": SITE},
            )
    assert reply.await_args.kwargs["paused_by"] == "demo_limit"


async def test_with_demo_mode_off_the_widget_is_untouched(app, corpus) -> None:
    redis = _demo(app, demo_mode=False)
    await _fill(redis, app)
    before = dict(redis.values)

    class _ReachedError(Exception):
        """The old answer path went on past the gate, as it does on any normal install."""

    with (
        patch("callobuzz_rag.api.routes.widget._build_orchestrator", side_effect=_ReachedError),
        pytest.raises(_ReachedError),
    ):
        await _chat(app, corpus)
    assert redis.values == before


async def test_the_scope_reaches_the_model_call_through_a_real_streamed_response() -> None:
    """The gate runs in the handler; the stream is read afterwards by the server. The first model
    call inside the stream must still count the visitor's answer, once."""
    redis = FakeRedis()
    settings = SimpleNamespace(
        demo_mode=True,
        demo_answers_per_day=300,
        demo_answers_per_visitor_per_day=15,
        demo_reset_at_utc="21:30",
        session_secret=SecretStr("s0"),
        admin_api_key=SecretStr("k"),
        trust_proxy_headers=False,
        trusted_proxy_hops=1,
    )
    spent: list[int] = []

    class _Calls:
        async def spend(self) -> None:
            spent.append(1)

    api = FastAPI()

    @api.post("/answer")
    async def answer(request: Request) -> StreamingResponse:
        await limits.check(
            SimpleNamespace(
                headers=request.headers,
                client=request.client,
                state=SimpleNamespace(user=None),
                # the shared client-ip helper reads the proxy rules from the app's settings
                app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
            ),
            redis,
            settings,
        )

        async def stream() -> Any:
            for _ in range(3):  # label, answer, fact check
                await budget.check_model_call("openrouter", "openrouter/x")
            yield b"data: done\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    with (
        patch.object(budget, "_budget", lambda: _Calls()),
        patch.object(budget, "_demo_on", lambda: True),
    ):
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://t") as client:
            res = await client.post("/answer")
    assert res.status_code == 200
    assert sorted(redis.values.values()) == [1, 1]  # one answer: the demo's and the visitor's
    assert len(spent) == 3  # every call still counts against the backstop
