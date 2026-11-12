"""Widget starters and click events (conversation C6)."""

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.core.config import Settings
from callobuzz_rag.widget.security import LimitResult

SITE = "https://shree-labs.in"
HOOK = "callobuzz_rag.api.routes.widget."
TOKEN = "ws_" + "s" * 40


def _runtime() -> SimpleNamespace:
    config = AssistantConfig.model_validate(
        {
            "identity": {"business_name": "Shree Labs"},
            "conversation": {
                "starters": [
                    {"label": "Test prices", "message": "What does a CBC cost?"},
                    {"label": "Home pickup", "message": "Do you collect samples at home?"},
                ]
            },
        }
    )
    return SimpleNamespace(config=config, version=3, draft_revision=None, enabled=True)


def _world() -> SimpleNamespace:
    app = create_app()
    app.state.settings = Settings(_env_file=None, admin_api_key="adm")  # type: ignore[call-arg]
    limiter = SimpleNamespace(
        check_event=AsyncMock(return_value=LimitResult(True)),
        check=AsyncMock(return_value=LimitResult(True)),
    )
    app.state.widget_rate_limiter = limiter
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Patient help")
    key = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        public_key="pk_" + "a" * 32,
        allowed_origins=[SITE],
        is_active=True,
        appearance={},
        rate_limit_per_minute=10,
    )
    session = AsyncMock()

    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_session] = override
    conv = SimpleNamespace(id=uuid.uuid4(), corpus_id=corpus.id, assistant_state={})
    return SimpleNamespace(app=app, corpus=corpus, key=key, session=session, conv=conv, lim=limiter)


async def _call(
    w: SimpleNamespace,
    method: str,
    path: str,
    *,
    json: Any = None,
    runtime: Any = "default",
    conv: Any = "default",
    headers: dict[str, str] | None = None,
) -> Any:
    runtime = _runtime() if runtime == "default" else runtime
    conv = w.conv if conv == "default" else conv
    with (
        patch(HOOK + "_authorize_widget", AsyncMock(return_value=(w.key, w.corpus))),
        patch(HOOK + "surfaces.assistant_for", AsyncMock(return_value=runtime)),
        patch(HOOK + "resolve_widget_session", AsyncMock(return_value=conv)),
    ):
        async with AsyncClient(transport=ASGITransport(app=w.app), base_url="http://test") as c:
            return await c.request(
                method,
                path,
                json=json,
                headers=headers or {"X-Widget-Key": w.key.public_key, "Origin": SITE},
            )


def _written(w: SimpleNamespace) -> tuple[ConversationState, str]:
    """The state the event route wrote (its last UPDATE) and the statement's SQL."""
    stmt = w.session.execute.await_args.args[0]
    params = stmt.compile().params
    return ConversationState.from_dict(params["assistant_state"]), str(stmt)


async def test_config_returns_starters() -> None:
    w = _world()
    res = await _call(w, "GET", "/api/v1/widget/config")
    assert res.status_code == 200, res.text
    assert [s["label"] for s in res.json()["starters"]] == ["Test prices", "Home pickup"]
    assert res.json()["assistant_name"]


async def test_config_without_assistant_has_no_starters() -> None:
    w = _world()
    res = await _call(w, "GET", "/api/v1/widget/config", runtime=None)
    assert res.status_code == 200 and res.json()["starters"] == []


async def test_dismiss_marks_the_cta_type_declined() -> None:
    w = _world()
    body = {"session": TOKEN, "type": "cta_dismiss", "cta_type": "whatsapp"}
    res = await _call(w, "POST", "/api/v1/widget/event", json=body)
    assert res.status_code == 204, res.text
    assert _written(w)[0].cta_declined == ["whatsapp"]
    w.session.refresh.assert_awaited_once()
    assert w.session.refresh.await_args.kwargs == {"with_for_update": True}
    w.session.commit.assert_awaited()


async def test_click_and_chip_counters() -> None:
    w = _world()
    for body in (
        {"session": TOKEN, "type": "cta_click", "cta_type": "call"},
        {"session": TOKEN, "type": "chip_click"},
        {"session": TOKEN, "type": "chip_click"},
    ):
        assert (await _call(w, "POST", "/api/v1/widget/event", json=body)).status_code == 204
        w.conv.assistant_state = _written(w)[0].to_dict()  # what the UPDATE stored
    state = _written(w)[0]
    assert state.cta_clicks == 1 and state.chip_clicks == 2 and state.cta_shown == ["call"]


async def test_a_tap_does_not_touch_updated_at() -> None:
    w = _world()
    body = {"session": TOKEN, "type": "chip_click"}
    assert (await _call(w, "POST", "/api/v1/widget/event", json=body)).status_code == 204
    sql = " ".join(_written(w)[1].split())
    # updated_at is set to itself, so the column's onupdate never bumps it.
    assert "updated_at=conversations.updated_at" in sql.replace(" = ", "=")


async def test_unknown_session_is_400() -> None:
    w = _world()
    body = {"session": TOKEN, "type": "chip_click"}
    res = await _call(w, "POST", "/api/v1/widget/event", json=body, conv=None)
    assert res.status_code == 400 and res.json()["code"] == "widget_session_invalid"


async def test_a_session_of_another_knowledge_base_is_400() -> None:
    w = _world()
    other = SimpleNamespace(id=uuid.uuid4(), corpus_id=uuid.uuid4(), assistant_state={})
    body = {"session": TOKEN, "type": "chip_click"}
    res = await _call(w, "POST", "/api/v1/widget/event", json=body, conv=other)
    assert res.status_code == 400 and other.assistant_state == {}


async def test_assistant_off_is_409() -> None:
    w = _world()
    body = {"session": TOKEN, "type": "chip_click"}
    res = await _call(w, "POST", "/api/v1/widget/event", json=body, runtime=None)
    assert res.status_code == 409 and res.json()["code"] == "assistant_disabled"


async def test_body_is_strict_and_a_cta_event_needs_a_type() -> None:
    w = _world()
    res = await _call(
        w, "POST", "/api/v1/widget/event", json={"session": TOKEN, "type": "cta_click"}
    )
    assert res.status_code == 422
    extra = {"session": TOKEN, "type": "chip_click", "label": "free text"}
    assert (await _call(w, "POST", "/api/v1/widget/event", json=extra)).status_code == 422
    bad = {"session": "nope", "type": "chip_click"}
    assert (await _call(w, "POST", "/api/v1/widget/event", json=bad)).status_code == 422


async def test_event_limit_is_its_own_window_not_the_chat_budget() -> None:
    w = _world()
    w.lim.check_event.return_value = LimitResult(False, 12, "Too many requests.")
    body = {"session": TOKEN, "type": "chip_click"}
    res = await _call(w, "POST", "/api/v1/widget/event", json=body)
    assert res.status_code == 429 and res.headers["retry-after"] == "12"
    w.lim.check.assert_not_awaited()
    args = w.lim.check_event.await_args.args
    assert args[0] == str(w.key.id) and args[2] == 10


async def test_preflight_allows_the_widget_key_header() -> None:
    w = _world()
    headers = {
        "Origin": SITE,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-widget-key,content-type",
    }
    res = await _call(w, "OPTIONS", "/api/v1/widget/event", headers=headers)
    assert res.status_code in (200, 204)
    assert "x-widget-key" in res.headers["access-control-allow-headers"].lower()


async def test_check_event_window_is_three_times_the_message_limit() -> None:
    from callobuzz_rag.widget.security import WidgetRateLimiter

    limiter = WidgetRateLimiter.__new__(WidgetRateLimiter)
    seen: list[tuple[str, int]] = []

    async def sliding(base: str, limit: int, window_s: int, now: float) -> tuple[bool, int]:
        seen.append((base, limit))
        return True, 0

    limiter._sliding = sliding  # type: ignore[method-assign]
    await limiter.check_event("k", "1.2.3.4", 10)
    await limiter.check_event("k", "1.2.3.4", 5)
    assert seen == [("widget:ev:k:1.2.3.4", 30), ("widget:ev:k:1.2.3.4", 30)]
    await limiter.check_event("k", "1.2.3.4", 20)
    assert seen[-1][1] == 60


async def test_the_served_script_carries_the_chips_and_the_cta() -> None:
    w = _world()
    async with AsyncClient(transport=ASGITransport(app=w.app), base_url="http://test") as c:
        res = await c.get("/widget.js")
    script = res.text
    for needle in ("/api/v1/widget/event", '"quick_replies"', "cta-btn", "cobrag", "chip_click"):
        assert needle in script, needle
    assert "innerHTML = chip" not in script and "renderMarkdown" not in script
