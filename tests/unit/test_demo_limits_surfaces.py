"""The demo's daily answer limits on every answering surface: the gate, the director's polite
reply, the legacy paths (a sentence, never a 500), COB, the public numbers, and a normal install
where nothing changes."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    ErrorEvent,
    SourcesEvent,
    Surface,
    TokenEvent,
    UiEvent,
    to_sse,
)
from callobuzz_rag.conversation.types import Route
from callobuzz_rag.demo import budget, limits
from callobuzz_rag.demo.limits import DemoLimiter
from callobuzz_rag.fairuse import gate
from tests.unit.accounts_fakes import FakeRedis
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn
from tests.unit.scope_harness import call, scoped_app
from tests.unit.test_cap_call_sites import quota
from tests.unit.test_director_billing_paused import _registry
from tests.unit.test_fairuse_gate import STD, effective, env

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
VISITOR_TEXT_START = "You've used today's demo questions (15)."
GLOBAL_TEXT_START = "Today's demo answers are all used up."


@pytest.fixture(autouse=True)
def _no_scope() -> Iterator[None]:
    limits.set_scope(None)
    yield
    limits.set_scope(None)


def _demo_settings(**over: Any) -> Any:
    base: dict[str, Any] = {
        "demo_mode": True,
        "demo_answers_per_day": 300,
        "demo_answers_per_visitor_per_day": 15,
        "demo_reset_at_utc": "21:30",
        "session_secret": SecretStr("s0"),
        "admin_api_key": SecretStr("admin-key"),
        "trust_proxy_headers": False,
        "trusted_proxy_hops": 1,
        "fair_use_enabled": True,
    }
    base.update(over)
    return SimpleNamespace(**base)


def _gate_request(
    redis: FakeRedis | None, *, admin: bool = False, auth_kind: str = "none", **settings: Any
) -> Any:
    state = SimpleNamespace(demo_redis=redis, settings=_demo_settings(**settings))
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(is_admin=admin, auth_kind=auth_kind, user=None),
        headers={},
        client=SimpleNamespace(host="203.0.113.7"),
    )


async def _used_up_visitor(redis: FakeRedis, request: Any) -> None:
    """Fill this request's visitor to the limit."""
    scope = await limits.check(request, redis, request.app.state.settings)
    assert scope is not None
    for _ in range(request.app.state.settings.demo_answers_per_visitor_per_day):
        await scope.limiter.reserve(scope.visitor)
    limits.set_scope(None)


async def _gate(req: Any, **kw: Any) -> gate.GateDecision:
    corpus = SimpleNamespace(tenant_id=uuid.uuid4())
    return await gate.answer_gate(
        AsyncMock(),
        req,
        corpus,  # type: ignore[arg-type]
        payer=kw.pop("payer", "operator"),
        surface=kw.pop("surface", "widget"),
        plan_profile=STD,
        **kw,
    )


# ------------------------------------------------------------------ the gate


async def test_a_visitor_over_their_quota_is_paused_with_no_model_and_no_metering() -> None:
    redis = FakeRedis()
    req = _gate_request(redis)
    await _used_up_visitor(redis, req)
    with env(effective()):
        decision = await _gate(req)
    assert (decision.action, decision.paused_by, decision.reasons) == (
        "pause",
        "demo_limit",
        ("demo_limit",),
    )
    assert decision.waive_charges is False and (decision.retry_after_s or 0) > 0
    scope = limits.current_scope()
    assert scope is not None and scope.standing.kind == "visitor"
    assert scope.text().startswith(VISITOR_TEXT_START)
    assert gate.pause_args(decision) == {"billing_paused": False, "paused_by": "demo_limit"}


async def test_the_whole_demo_used_up_pauses_everyone() -> None:
    redis = FakeRedis()
    req = _gate_request(redis, demo_answers_per_day=2)
    scope = await limits.check(req, redis, req.app.state.settings)
    assert scope is not None
    for name in ("x", "y"):
        await scope.limiter.reserve(name)
    with env(effective()):
        decision = await _gate(req)
    assert decision.paused_by == "demo_limit"
    current = limits.current_scope()
    assert current is not None and current.standing.kind == "global"
    assert current.text().startswith(GLOBAL_TEXT_START)


async def test_a_visitor_with_answers_left_goes_on_and_nothing_is_counted_yet() -> None:
    redis = FakeRedis()
    req = _gate_request(redis)
    with env(effective()):
        decision = await _gate(req)
    assert decision.action == "allow"
    scope = limits.current_scope()
    assert scope is not None and not scope.counted
    assert redis.values == {}  # counted at the first model call, not at the gate


async def test_the_gate_fails_closed_when_redis_is_down() -> None:
    redis = FakeRedis()
    redis.down = True
    with env(effective()):
        decision = await _gate(_gate_request(redis))
    assert decision.paused_by == "demo_limit"
    scope = limits.current_scope()
    assert scope is not None and scope.standing.kind == "unavailable"
    assert "right now" in scope.text()


async def test_the_shared_super_admin_sign_in_is_limited_but_the_admin_key_is_not() -> None:
    redis = FakeRedis()
    session = _gate_request(redis, admin=True, auth_kind="session")
    await _used_up_visitor(redis, session)
    with env(effective(exempt=True)):
        assert (await _gate(session)).paused_by == "demo_limit"
        key = _gate_request(redis, admin=True, auth_kind="api_key")
        assert (await _gate(key)).action == "allow"
    assert limits.current_scope() is None


async def test_cob_and_eval_surfaces_are_not_this_gate() -> None:
    redis = FakeRedis()
    req = _gate_request(redis)
    await _used_up_visitor(redis, req)
    with env(effective()):
        for surface in ("cob", "eval"):
            assert (await _gate(req, surface=surface)).action == "allow"


async def test_nothing_changes_with_demo_mode_off() -> None:
    redis = FakeRedis()
    req = _gate_request(redis, demo_mode=False)
    with env(effective()):
        decision = await _gate(req)
    assert decision.action == "allow" and limits.current_scope() is None
    assert redis.values == {}


async def test_the_legacy_refusal_is_a_polite_429_or_503_never_a_500() -> None:
    redis = FakeRedis()
    req = _gate_request(redis)
    await _used_up_visitor(redis, req)
    with env(effective()):
        decision = await _gate(req)
    with pytest.raises(Exception) as err:
        await gate.refuse_paused(decision, req)
    exc = err.value
    assert (exc.status_code, exc.code) == (429, "demo_limit")  # type: ignore[attr-defined]
    assert str(exc.detail).startswith(VISITOR_TEXT_START)  # type: ignore[attr-defined]
    assert int(exc.headers["Retry-After"]) >= 1  # type: ignore[attr-defined]

    down = FakeRedis()
    down.down = True
    req2 = _gate_request(down)
    with env(effective()):
        decision = await _gate(req2)
    with pytest.raises(Exception) as err2:
        await gate.refuse_paused(decision, req2)
    assert (err2.value.status_code, err2.value.code) == (503, "demo_unavailable")  # type: ignore[attr-defined]
    assert "right now" in str(err2.value.detail)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ the director


def _scope_for(kind: str, redis: FakeRedis | None = None) -> None:
    lim = DemoLimiter(
        redis or FakeRedis(),
        answers_per_day=300,
        per_visitor=15,
        reset_at="21:30",
        now_fn=lambda: NOW,
    )
    limits.set_scope(limits.DemoTurnScope(lim, "v", limits.Standing(kind)))  # type: ignore[arg-type]


@pytest.fixture
def _io(director_io: Any) -> Any:
    return director_io


async def _run(text: str, llm: FakeLLM, kind: str = "visitor"):  # type: ignore[no-untyped-def]
    _scope_for(kind)
    turn = make_turn(text)
    turn.paused_by = "demo_limit"
    orch = fake_orch()
    orch.run_retrieval_pipeline = AsyncMock(side_effect=AssertionError("searched while paused"))
    events = [e async for e in make_director(llm, orch, _registry()).run_stream(turn)]
    return events, "".join(e.content for e in events if isinstance(e, TokenEvent))


async def test_a_question_over_the_visitor_quota_is_the_friendly_reply(_io: Any) -> None:
    llm = FakeLLM()
    events, text = await _run("What are your timings?", llm)
    done = events[-1]
    assert text.startswith(VISITOR_TEXT_START)
    assert "21:30 UTC (03:00 IST)" in text and "install your own copy" in text
    assert isinstance(done, DoneEvent) and done.route == Route.HANDOFF
    assert llm.calls == []  # no model call
    assert not [e for e in events if isinstance(e, SourcesEvent | UiEvent)]  # no search, no card
    assert not [e for e in events if isinstance(e, ErrorEvent)]  # a message, not an error
    _io.meter.assert_not_awaited()  # nothing metered


async def test_a_question_when_the_demo_is_used_up_says_so(_io: Any) -> None:
    events, text = await _run("What are your timings?", FakeLLM(), kind="global")
    assert text.startswith(GLOBAL_TEXT_START)
    assert events[-1].paused is True


async def test_the_unavailable_reply_is_a_message_too(_io: Any) -> None:
    events, text = await _run("What are your timings?", FakeLLM(), kind="unavailable")
    assert "right now" in text and not [e for e in events if isinstance(e, ErrorEvent)]


async def test_small_talk_stays_free_for_a_visitor_over_their_quota(_io: Any) -> None:
    llm = FakeLLM()
    events, text = await _run("hello", llm)
    assert events[-1].route == Route.SMALLTALK and not text.startswith(VISITOR_TEXT_START)
    assert llm.calls == []


async def test_a_request_for_a_person_is_not_a_demo_answer(_io: Any) -> None:
    """The human path never reaches the gate, so it never opens a scope or counts."""
    turn = make_turn("Can I talk to a human?")
    turn.human_only = True
    events = [e async for e in make_director(FakeLLM(), fake_orch(), _registry()).run_stream(turn)]
    assert events[-1].route == Route.HANDOFF
    assert limits.current_scope() is None


async def test_callers_are_told_fair_use_and_the_widget_nothing(_io: Any) -> None:
    events, _ = await _run("What are your timings?", FakeLLM())
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.pause_reason == "fair_use"

    def body(surface: Surface) -> dict[str, Any]:
        frame = to_sse(done, surface)
        assert frame is not None
        data = next(line for line in frame.splitlines() if line.startswith("data:"))
        return json.loads(data.removeprefix("data:").strip())

    assert body(Surface.STREAM)["pause_reason"] == "fair_use"
    flat = json.dumps(body(Surface.WIDGET)).lower()
    assert "pause_reason" not in flat and "demo" not in flat


class _RefusingLLM(FakeLLM):
    def __init__(self, error: Exception) -> None:
        super().__init__(json_replies=[{"label": "question", "confidence": 0.9}])
        self._error = error

    async def complete_stream(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error
        yield  # pragma: no cover

    async def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error

    async def complete_json(self, *args: Any, **kwargs: Any) -> Any:
        raise self._error


@pytest.mark.parametrize(
    ("error", "starts", "code"),
    [
        (limits.DemoLimitReached("visitor"), VISITOR_TEXT_START, "demo_limit"),
        (budget.DemoBudgetExceeded("calls"), GLOBAL_TEXT_START, "demo_limit"),
        (
            budget.DemoBudgetUnavailable("redis"),
            "The demo cannot answer right now",
            "demo_unavailable",
        ),
    ],
)
async def test_a_limit_that_surfaces_mid_turn_is_the_polite_text_not_a_failure(
    _io: Any, error: Exception, starts: str, code: str
) -> None:
    _scope_for("ok")
    turn = make_turn("What are your timings?")
    events = [
        e
        async for e in make_director(_RefusingLLM(error), fake_orch(), _registry()).run_stream(turn)
    ]
    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].detail.startswith(starts) and errors[0].code == code
    frame = to_sse(errors[0], Surface.WIDGET)
    assert frame is not None and starts in frame


# ------------------------------------------------------------------ the legacy routes


@pytest.fixture
def demo_app() -> Iterator[tuple[Any, Any, FakeRedis]]:
    redis = FakeRedis()
    with scoped_app() as (app, world), quota(None):
        app.state.settings = app.state.settings.model_copy(
            update={
                "demo_mode": True,
                "demo_answers_per_visitor_per_day": 2,
                "demo_reset_at_utc": "21:30",
                "session_secret": SecretStr("s0"),
            }
        )
        app.state.demo_redis = redis
        yield app, world, redis


async def _fill(app: Any, redis: FakeRedis) -> None:
    lim = limits.limiter_from(redis, app.state.settings)
    # the harness client is 127.0.0.1 behind no proxy, with no signed-in user
    visitor = limits.visitor_hash("s0", "127.0.0.1", None)
    for _ in range(2):
        await lim.reserve(visitor)


def _assert_polite(res: Any) -> None:
    assert res.status_code == 429, res.text
    body = res.json()
    assert body["code"] == "demo_limit"
    assert body["detail"].startswith("You've used today's demo questions (2).")
    assert "21:30 UTC (03:00 IST)" in body["detail"]


async def test_the_legacy_query_says_try_tomorrow(demo_app: Any) -> None:
    app, _, redis = demo_app
    await _fill(app, redis)
    res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    _assert_polite(res)


async def test_the_playground_stream_says_it_before_the_stream_starts(demo_app: Any) -> None:
    app, _, redis = demo_app
    await _fill(app, redis)
    res = await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    _assert_polite(res)


async def test_a_conversation_message_says_it_too(demo_app: Any) -> None:
    app, world, redis = demo_app
    await _fill(app, redis)
    res = await call(
        app,
        "ck_a",
        "POST",
        f"/api/v1/corpora/{world.corpus_a.id}/conversations/{uuid.uuid4()}/messages",
        json={"content": "hi"},
    )
    _assert_polite(res)


@pytest.mark.assistant_on
async def test_the_assistant_path_turns_the_pause_into_the_directors_reply(demo_app: Any) -> None:
    """With the assistant on, the route hands the director `paused_by="demo_limit"`."""
    from callobuzz_rag.conversation import surfaces

    app, _, redis = demo_app
    await _fill(app, redis)
    runtime = SimpleNamespace(config=SimpleNamespace(enabled=True))
    stream = AsyncMock(return_value=MagicMock(status_code=200))
    with (
        patch.object(surfaces, "assistant_for", AsyncMock(return_value=runtime)),
        patch.object(surfaces, "answer_query", AsyncMock()),
        patch.object(surfaces, "stream_query_response", stream),
        patch.object(surfaces, "is_human_turn", lambda *_: False),
    ):
        await call(app, "ck_a", "POST", "/api/v1/query/stream", json={"query": "hi"})
    assert stream.await_args.kwargs["paused_by"] == "demo_limit"


async def test_under_the_quota_the_legacy_path_is_untouched(demo_app: Any) -> None:
    from callobuzz_rag.api.routes import query as query_routes

    app, _, redis = demo_app
    orch = MagicMock()
    orch.llm = MagicMock(cost_ledger=None)
    orch.query = AsyncMock(
        return_value={
            "answer": "Yes",
            "citations": [],
            "confidence": 0.9,
            "query_id": uuid.uuid4(),
            "model_used": "m",
            "latency_ms": 5,
            "token_usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
    )
    with (
        patch.object(query_routes, "_build_orchestrator", return_value=orch),
        patch.object(query_routes, "record_usage", AsyncMock()),
    ):
        res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.status_code == 200, res.text
    assert redis.values == {}  # the gate only read; the model call is what counts


async def test_with_demo_mode_off_the_routes_never_look_at_the_demo(demo_app: Any) -> None:
    app, _, redis = demo_app
    await _fill(app, redis)
    app.state.settings = app.state.settings.model_copy(update={"demo_mode": False})
    before = dict(redis.values)
    res = await call(app, "ck_a", "POST", "/api/v1/query", json={"query": "hi"})
    assert res.json().get("code") != "demo_limit"
    assert redis.values == before


async def test_a_demo_error_that_escapes_a_route_is_polite_not_a_500() -> None:
    from fastapi import APIRouter

    from callobuzz_rag.api.app import create_app

    app = create_app()
    app.state.settings = _demo_settings()
    router = APIRouter()

    @router.get("/_t/calls")
    async def calls() -> None:
        raise budget.DemoBudgetExceeded("the model-call backstop")

    @router.get("/_t/down")
    async def down() -> None:
        raise budget.DemoBudgetUnavailable("redis")

    app.include_router(router)
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        calls_res = await client.get("/_t/calls", headers={"X-API-Key": "x"})
        down_res = await client.get("/_t/down", headers={"X-API-Key": "x"})
    # the key is refused first on a real app; the handler is what is under test, so call it
    from callobuzz_rag.api.errors import demo_limit_handler

    for exc, status, code in (
        (budget.DemoBudgetExceeded("x"), 429, "demo_limit"),
        (budget.DemoBudgetUnavailable("x"), 503, "demo_unavailable"),
    ):
        res = await demo_limit_handler(MagicMock(), exc)
        assert res.status_code == status and code in res.body.decode()
    assert calls_res.status_code != 500 and down_res.status_code != 500


# ------------------------------------------------------------------ COB


def _cob_turn(public: bool = False) -> Any:
    from callobuzz_rag.cob.orchestrator import CobTurn

    caller = SimpleNamespace(role=SimpleNamespace(value="admin"), tenant_id=None, user_id=None)
    return CobTurn(  # type: ignore[arg-type]
        caller=caller,
        message="how do I add a document?",
        conversation_id=None,
        context=None,
        language_hint=None,
        public=public,
    )


async def test_cob_turns_into_the_fixed_reply_for_a_visitor_over_the_quota() -> None:
    from callobuzz_rag.cob.orchestrator import CobOrchestrator, PreparedTurn

    redis = FakeRedis()
    req = _gate_request(redis)
    await _used_up_visitor(redis, req)
    orch = CobOrchestrator(req)
    prepared = PreparedTurn(
        turn=_cob_turn(),
        corpus=SimpleNamespace(id=uuid.uuid4()),
        conversation_id=uuid.uuid4(),
        state=SimpleNamespace(),  # type: ignore[arg-type]
        history=[],
        index=SimpleNamespace(),  # type: ignore[arg-type]
        page=None,
        metering_tenant_id=uuid.uuid4(),
        llm_override=None,
    )
    await orch._demo_gate(prepared)
    assert prepared.mode == "demo_limit"
    frames: list[str] = []
    with patch.object(orch, "_own_turn", AsyncMock(return_value=None)):
        async for frame in orch._demo_limit_turn(prepared, "en", 0.0):
            frames.append(frame)
    joined = "".join(frames)
    assert VISITOR_TEXT_START in joined and "event: done" in joined and "event: error" not in joined


async def test_cob_with_answers_left_keeps_answering_and_opens_the_count() -> None:
    from callobuzz_rag.cob.orchestrator import CobOrchestrator, PreparedTurn

    redis = FakeRedis()
    req = _gate_request(redis)
    prepared = PreparedTurn(
        turn=_cob_turn(),
        corpus=SimpleNamespace(),
        conversation_id=uuid.uuid4(),
        state=SimpleNamespace(),  # type: ignore[arg-type]
        history=[],
        index=SimpleNamespace(),  # type: ignore[arg-type]
        page=None,
        metering_tenant_id=uuid.uuid4(),
        llm_override=None,
    )
    await CobOrchestrator(req)._demo_gate(prepared)
    assert prepared.mode == "answer"
    assert limits.current_scope() is not None  # the first model call counts one answer


async def test_cob_search_mode_is_left_alone() -> None:
    """Search mode is free and already a no-model reply, so the demo gate does not replace it."""
    from callobuzz_rag.cob.orchestrator import CobOrchestrator, PreparedTurn

    redis = FakeRedis()
    req = _gate_request(redis)
    await _used_up_visitor(redis, req)
    prepared = PreparedTurn(
        turn=_cob_turn(),
        corpus=SimpleNamespace(),
        conversation_id=uuid.uuid4(),
        state=SimpleNamespace(),  # type: ignore[arg-type]
        history=[],
        index=SimpleNamespace(),  # type: ignore[arg-type]
        page=None,
        metering_tenant_id=uuid.uuid4(),
        llm_override=None,
        mode="search",
    )
    await CobOrchestrator(req)._demo_gate(prepared)
    assert prepared.mode == "search"


async def test_cob_off_demo_does_nothing() -> None:
    from callobuzz_rag.cob.orchestrator import CobOrchestrator, PreparedTurn

    redis = FakeRedis()
    req = _gate_request(redis, demo_mode=False)
    prepared = PreparedTurn(
        turn=_cob_turn(),
        corpus=SimpleNamespace(),
        conversation_id=uuid.uuid4(),
        state=SimpleNamespace(),  # type: ignore[arg-type]
        history=[],
        index=SimpleNamespace(),  # type: ignore[arg-type]
        page=None,
        metering_tenant_id=uuid.uuid4(),
        llm_override=None,
    )
    await CobOrchestrator(req)._demo_gate(prepared)
    assert prepared.mode == "answer" and limits.current_scope() is None


# ------------------------------------------------------------------ the numbers everyone sees


async def test_the_public_branding_carries_the_numbers_and_no_addresses() -> None:
    from callobuzz_rag.api.routes.engine_settings import _demo_answers, _demo_info

    redis = FakeRedis()
    settings = _demo_settings(
        superadmin_email="op@demo.test", demo_owner_email="own@demo.test", demo_password="pw"
    )
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, demo_redis=redis))
    lim = limits.limiter_from(redis, settings)
    for name in ("a", "b", "c"):
        await lim.reserve(name)
    info = _demo_info(settings)
    assert info is not None
    info.answers = await _demo_answers(SimpleNamespace(app=app))  # type: ignore[arg-type]
    assert info.answers is not None
    assert (info.answers.limit, info.answers.left, info.answers.per_visitor) == (300, 297, 15)
    assert info.answers.resets_at.hour == 21 and info.answers.resets_at.minute == 30
    dumped = info.model_dump_json()
    assert "203.0.113" not in dumped and "visitor" not in dumped.replace("per_visitor", "")


async def test_the_numbers_say_zero_left_when_used_up_and_none_when_unreadable() -> None:
    from callobuzz_rag.api.routes.engine_settings import _demo_answers

    redis = FakeRedis()
    settings = _demo_settings(demo_answers_per_day=2, demo_answers_per_visitor_per_day=9)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, demo_redis=redis))
    lim = limits.limiter_from(redis, settings)
    await lim.reserve("a")
    await lim.reserve("b")
    answers = await _demo_answers(SimpleNamespace(app=app))  # type: ignore[arg-type]
    assert answers is not None and answers.left == 0
    redis.down = True
    assert await _demo_answers(SimpleNamespace(app=app)) is None  # type: ignore[arg-type]


async def test_the_admin_status_has_the_answer_counts() -> None:
    from callobuzz_rag.demo import runtime

    redis = FakeRedis()
    settings = _demo_settings(demo_model_calls_per_day=1200)
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, demo_redis=redis))
    await limits.limiter_from(redis, settings).reserve("a")
    status = await runtime.read_status(app, now=NOW)
    assert (status.answers_today, status.answers_limit, status.answers_per_visitor_limit) == (
        1,
        300,
        15,
    )
