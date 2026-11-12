"""C1: conversation messages (JSON and SSE) through the director."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.conversation import surfaces
from callobuzz_rag.conversation.director import DirectorError
from callobuzz_rag.conversation.envelope import DoneEvent, MetaEvent, TokenEvent
from callobuzz_rag.conversation.store import RuntimeConfig
from callobuzz_rag.conversation.types import Route, Script, TurnLabel
from callobuzz_rag.core.config import Settings
from tests.unit.conversation_helpers import make_config

pytestmark = pytest.mark.assistant_on
QID = uuid.uuid4()
CONV = uuid.uuid4()
USER_MSG, BOT_MSG = uuid.uuid4(), uuid.uuid4()


def _done(**kw: Any) -> DoneEvent:
    base: dict[str, Any] = {
        "query_id": QID,
        "label": TurnLabel.SMALLTALK,
        "route": Route.SMALLTALK,
        "language": "en",
        "cta": None,
        "timings": {"write": 2},
        "claims_removed": 0,
        "model": None,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "latency_ms": 2,
        "text": "Hello!",
        "message_id": BOT_MSG,
        "user_message_id": USER_MSG,
        "conversation_id": CONV,
        "thinking_ms": 40,
        "reasoning_tokens": 12,
    }
    base.update(kw)
    return DoneEvent(**base)


class FakeDirector:
    def __init__(self, done: DoneEvent | None = None, error: Exception | None = None) -> None:
        self.turns: list[Any] = []
        self.done = done or _done()
        self.error = error

    async def run(self, turn: Any) -> DoneEvent:
        self.turns.append(turn)
        if self.error is not None:
            raise self.error
        return self.done

    async def run_stream(self, turn: Any):  # type: ignore[no-untyped-def]
        self.turns.append(turn)
        yield MetaEvent(
            query_id=QID,
            conversation_id=CONV,
            session=None,
            label=TurnLabel.SMALLTALK,
            route=Route.SMALLTALK,
            language="en",
            script=Script.LATIN,
            assistant_version=3,
        )
        yield TokenEvent(content="Hello!")
        yield self.done

    async def aclose(self) -> None:
        return None


def _msg(mid: uuid.UUID, role: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        conversation_id=CONV,
        role=role,
        content="x",
        token_count=0,
        retrieval_context=None,
        agent_log=None,
        created_at=datetime.now(UTC),
    )


async def _reply(stream: bool, director: FakeDirector | None = None) -> tuple[Any, FakeDirector]:
    director = director or FakeDirector()
    session = AsyncMock()
    session.get = AsyncMock(
        side_effect=lambda model, mid: _msg(mid, "user" if mid == USER_MSG else "assistant")
    )
    corpus = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), applied_embedding=None)
    body = SimpleNamespace(content="hi", assistant=None)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=Settings(_env_file=None)))
    )
    runtime = RuntimeConfig(config=make_config(), version=3, draft_revision=None, enabled=True)
    with (
        patch.object(surfaces, "build_director", MagicMock(return_value=director)),
        patch.object(
            surfaces,
            "conversation_context",
            AsyncMock(return_value=(SimpleNamespace(id=CONV), MagicMock(), [])),
        ),
    ):
        res = await surfaces.conversation_reply(
            request,  # type: ignore[arg-type]
            session,
            corpus,  # type: ignore[arg-type]
            CONV,
            body,
            runtime,
            stream=stream,
        )
    return res, director


async def test_json_reply_returns_both_messages_and_envelope() -> None:
    res, director = await _reply(stream=False)
    assert res.user_message.id == USER_MSG and res.assistant_message.id == BOT_MSG
    assert res.label == TurnLabel.SMALLTALK and res.route == Route.SMALLTALK
    assert director.turns[0].channel.value == "conversation"
    assert director.turns[0].usage_source == "conversation"
    assert director.turns[0].conversation_id == CONV


async def test_a_turn_that_could_not_be_stored_is_a_server_error() -> None:
    director = FakeDirector(_done(message_id=None, user_message_id=None))
    with pytest.raises(APIError) as err:
        await _reply(stream=False, director=director)
    assert err.value.status_code == 500 and err.value.code == "conversation_not_stored"


@pytest.mark.parametrize(
    ("code", "status"), [("retrieval_failed", 502), ("query_invalid", 422), (None, 502)]
)
async def test_director_errors_map_to_the_status_the_old_path_used(
    code: str | None, status: int
) -> None:
    director = FakeDirector(error=DirectorError("nope", code))
    with pytest.raises(APIError) as err:
        await _reply(stream=False, director=director)
    assert err.value.status_code == status


async def test_stream_reply_frames_keep_the_conversation_done_fields() -> None:
    res, _ = await _reply(stream=True)
    frames = [f async for f in res.body_iterator]
    names = [f.split("\n")[0].removeprefix("event: ") for f in frames]
    assert names == ["meta", "token", "done"]
    done = frames[-1]
    for field in ('"message_id"', '"total_tokens"', '"timings"', '"latency_ms"', '"thinking_ms"'):
        assert field in done, field
    assert res.headers["cache-control"] == "no-cache"


async def test_conversation_context_uses_the_history_budget_and_the_summary() -> None:
    from callobuzz_rag.db.models.conversation import Conversation

    conv = Conversation(corpus_id=uuid.uuid4(), assistant_state={"off_topic_strikes": 1})
    conv.id = uuid.uuid4()
    session = AsyncMock()
    session.get = AsyncMock(return_value=conv)
    builder = MagicMock()
    builder.build_context = AsyncMock(
        return_value=[
            {"role": "system", "content": "Previous conversation summary:\nPrices asked."},
            {"role": "user", "content": "price?"},
            {"role": "assistant", "content": "Rs 150."},
        ]
    )
    settings = Settings(_env_file=None, max_conversation_history=7, conversation_context_budget=900)
    with patch(
        "callobuzz_rag.memory.context_builder.ConversationContextBuilder",
        MagicMock(return_value=builder),
    ) as cls:
        got_conv, state, history = await surfaces.conversation_context(
            session, conv.corpus_id, conv.id, settings
        )
    assert cls.call_args.kwargs == {"max_messages": 7, "token_budget": 900}
    assert got_conv is conv and state.off_topic_strikes == 1
    assert [m.role for m in history] == ["system", "user", "assistant"]
    assert history[0].content.startswith("Previous conversation summary")


async def test_a_conversation_of_another_knowledge_base_is_not_found() -> None:
    from callobuzz_rag.db.models.conversation import Conversation

    conv = Conversation(corpus_id=uuid.uuid4())
    session = AsyncMock()
    session.get = AsyncMock(return_value=conv)
    with pytest.raises(APIError) as err:
        await surfaces.conversation_context(
            session, uuid.uuid4(), uuid.uuid4(), Settings(_env_file=None)
        )
    assert err.value.status_code == 404
