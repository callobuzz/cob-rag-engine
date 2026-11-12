"""POST /api/v1/widget/handoff (C5): records the tap, queues the notification once."""

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from callobuzz_rag.api.app import create_app
from callobuzz_rag.api.deps import get_session
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.config import Settings

SITE = "https://shree-labs.in"
HOOK = "callobuzz_rag.api.routes.widget."
TOKEN = "ws_" + "s" * 40
CFG = AssistantConfig.model_validate(
    {
        "identity": {
            "card": {
                "whatsapp": "+919812345678",
                "phone": "+919876543210",
                # open on Mondays 09:00-18:00 only, so Tuesday is closed
                "hours": [{"day": "mon", "open": "09:00", "close": "18:00"}],
            }
        },
        "handoff": {
            "channels": [
                {"kind": "whatsapp"},
                {"kind": "call", "available": "open_hours"},
                {"kind": "email", "enabled": False},
            ]
        },
    }
)


def _world() -> SimpleNamespace:
    app = create_app()
    app.state.settings = Settings(_env_file=None, admin_api_key="adm")  # type: ignore[call-arg]
    hit = AsyncMock(return_value=(True, 0))
    app.state.widget_rate_limiter = SimpleNamespace(_hit=hit, check=AsyncMock())
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="Patient help")
    key = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        public_key="pk_" + "a" * 32,
        allowed_origins=[SITE],
        is_active=True,
        appearance={},
    )
    session = AsyncMock()
    session.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", uuid.uuid4()))

    async def override() -> AsyncIterator[Any]:
        yield session

    app.dependency_overrides[get_session] = override
    conv = SimpleNamespace(
        id=uuid.uuid4(),
        corpus_id=corpus.id,
        language="hi-Latn",
        page_url=SITE + "/tests",
        assistant_state={"last_handoff_reason": "keyword", "handoff_offered": True},
    )
    return SimpleNamespace(app=app, corpus=corpus, key=key, session=session, conv=conv, hit=hit)


async def _post(
    w: SimpleNamespace,
    body: dict[str, Any],
    *,
    config: AssistantConfig | None = CFG,
    conv: Any = "default",
    recent: Any = None,
    origin: str = SITE,
) -> tuple[Any, MagicMock]:
    conversation = w.conv if conv == "default" else conv
    runtime = None if config is None else SimpleNamespace(config=config)
    with (
        patch(HOOK + "_authorize_widget", AsyncMock(return_value=(w.key, w.corpus))),
        patch(HOOK + "surfaces.assistant_for", AsyncMock(return_value=runtime)),
        patch(HOOK + "resolve_widget_session", AsyncMock(return_value=conversation)),
        patch(HOOK + "_recent_handoff", AsyncMock(return_value=recent)),
        patch("callobuzz_rag.worker.conversation_tasks.queue_handoff_notification") as notify,
    ):
        async with AsyncClient(transport=ASGITransport(app=w.app), base_url="http://test") as c:
            res = await c.post(
                "/api/v1/widget/handoff",
                json=body,
                headers={"X-Widget-Key": w.key.public_key, "Origin": origin},
            )
    return res, notify


def _body(channel: str = "whatsapp") -> dict[str, Any]:
    return {"session": TOKEN, "channel": channel}


async def test_a_tap_is_recorded_and_notified() -> None:
    w = _world()
    res, notify = await _post(w, _body())
    assert res.status_code == 201, res.text
    handoff_id = res.json()["handoff_id"]
    assert res.json()["status"] == "recorded"
    notify.assert_called_once_with(uuid.UUID(handoff_id))
    w.session.commit.assert_awaited()
    (row,) = [c.args[0] for c in w.session.add.call_args_list]
    assert row.channel == "whatsapp" and row.reason == "keyword"  # the reason the card showed
    assert row.language == "hi-Latn" and row.page_url == SITE + "/tests"
    assert row.conversation_id == w.conv.id and row.corpus_id == w.corpus.id
    assert w.conv.assistant_state["handoff_ids"] == [handoff_id]
    assert w.session.refresh.await_args.kwargs == {"with_for_update": True}
    # its own bucket: the chat limiter is not touched, a tap never spends the message quota
    assert w.hit.await_args.args[0].startswith(f"widget:handoff:{w.key.id}:")
    assert w.hit.await_args.args[1:] == (20, 3600)


async def test_the_reason_defaults_to_an_explicit_request() -> None:
    w = _world()
    w.conv.assistant_state = {}
    res, _ = await _post(w, _body())
    assert res.status_code == 201
    assert w.session.add.call_args.args[0].reason == "explicit_request"


async def test_a_card_shown_while_open_can_still_be_tapped_after_closing() -> None:
    """`call` is offered only in office hours, but a tap on it is recorded at any hour."""
    w = _world()
    res, notify = await _post(w, _body("call"))
    assert res.status_code == 201
    notify.assert_called_once()


async def test_a_channel_that_is_off_or_has_no_value_is_refused() -> None:
    w = _world()
    res, notify = await _post(w, _body("email"))  # disabled
    assert res.status_code == 409
    assert res.json() == {
        "detail": "That way of reaching us is not available right now.",
        "code": "handoff_channel_unavailable",
        "channel": "email",
    }
    res, _ = await _post(w, _body("booking"))  # never configured
    assert res.json()["code"] == "handoff_channel_unavailable"
    notify.assert_not_called()


async def test_unknown_session_is_400_and_foreign_conversation_too() -> None:
    w = _world()
    res, _ = await _post(w, _body(), conv=None)
    assert res.status_code == 400 and res.json()["code"] == "widget_session_invalid"
    other = SimpleNamespace(id=uuid.uuid4(), corpus_id=uuid.uuid4(), assistant_state={})
    res, _ = await _post(w, _body(), conv=other)
    assert res.status_code == 400 and res.json()["code"] == "widget_session_invalid"


async def test_assistant_off_and_handoff_off() -> None:
    w = _world()
    res, _ = await _post(w, _body(), config=None)
    assert res.status_code == 409 and res.json()["code"] == "assistant_disabled"
    off = CFG.model_copy(update={"handoff": CFG.handoff.model_copy(update={"enabled": False})})
    res, _ = await _post(w, _body(), config=off)
    assert res.status_code == 409 and res.json()["code"] == "handoff_disabled"


async def test_a_repeat_tap_returns_the_same_handoff() -> None:
    w = _world()
    existing = SimpleNamespace(id=uuid.uuid4())
    res, notify = await _post(w, _body(), recent=existing)
    assert res.status_code == 201 and res.json()["handoff_id"] == str(existing.id)
    notify.assert_not_called()
    w.session.add.assert_not_called()


async def test_rate_limit_has_its_own_code_and_retry_after() -> None:
    w = _world()
    w.hit.return_value = (False, 120)
    res, notify = await _post(w, _body())
    assert res.status_code == 429 and res.json()["code"] == "handoff_rate_limited"
    assert res.headers["retry-after"] == "120"
    notify.assert_not_called()


async def test_a_broken_limiter_lets_the_tap_through() -> None:
    w = _world()
    w.hit.side_effect = ConnectionError("redis down")
    res, _ = await _post(w, _body())
    assert res.status_code == 201


async def test_strict_body_and_origin() -> None:
    w = _world()
    res, _ = await _post(w, {**_body(), "page_url": "https://evil.example"})
    assert res.status_code == 422  # no client-supplied page
    res, _ = await _post(w, _body("callback"))
    assert res.status_code == 422  # a callback goes through the lead form


async def test_the_repeat_lookup_is_per_conversation_and_channel_within_ten_minutes() -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.dialects import postgresql

    from callobuzz_rag.api.routes.widget import _recent_handoff

    seen: list[Any] = []
    found = SimpleNamespace(id=uuid.uuid4())

    class Result:
        def scalars(self) -> Any:
            return SimpleNamespace(first=lambda: found)

    session = MagicMock()

    async def execute(stmt: Any) -> Result:
        seen.append(stmt)
        return Result()

    session.execute = execute
    conversation_id = uuid.uuid4()
    before = datetime.now(UTC)
    assert await _recent_handoff(session, conversation_id, "whatsapp") is found
    compiled = seen[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "handoffs.conversation_id =" in sql and "handoffs.channel =" in sql
    assert "handoffs.created_at >" in sql and "ORDER BY handoffs.created_at DESC" in sql
    assert conversation_id in compiled.params.values() and "whatsapp" in compiled.params.values()
    cutoff = next(v for v in compiled.params.values() if isinstance(v, datetime))
    assert before - timedelta(minutes=10, seconds=5) < cutoff < before - timedelta(minutes=9)
