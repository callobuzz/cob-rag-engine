"""PIP-2: the free local model fails in the middle of a turn (it will not load, the server just
stopped). A free business never gets an error for that: the turn becomes the polite busy reply
and the engine stops calling the model ready until it loads again."""

from collections.abc import AsyncIterator
from typing import Any

import pytest

from callobuzz_rag.conversation.envelope import DoneEvent, ErrorEvent, MetaEvent, TokenEvent
from callobuzz_rag.conversation.offers import OfferRegistry
from callobuzz_rag.conversation.schema import DEFAULT_BUSY
from callobuzz_rag.conversation.types import Route
from callobuzz_rag.llm import local as local_mod
from callobuzz_rag.llm.models import LLMMessage
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


class BrokenLocal(FakeLLM):
    """Answers nothing: the stream raises the way LiteLLM does when Ollama cannot load."""

    provider_name = "ollama_chat"

    async def complete_stream(self, messages: list[LLMMessage], **kw: Any) -> AsyncIterator[str]:
        raise ConnectionError("model requires more system memory")
        yield ""  # pragma: no cover - makes this an async generator


class BrokenCloud(BrokenLocal):
    provider_name = "openrouter"


async def _run(llm: FakeLLM) -> list[Any]:
    director = make_director(llm, fake_orch(), OfferRegistry())
    return [e async for e in director.run_stream(make_turn("what are your prices?"))]


async def test_a_local_answer_that_fails_becomes_the_busy_reply(
    monkeypatch: pytest.MonkeyPatch, _io: Any
) -> None:
    marked: list[object] = []
    monkeypatch.setattr(local_mod, "mark_unavailable", lambda s: marked.append(s))
    events = await _run(BrokenLocal(json_replies=[{}]))
    assert not [e for e in events if isinstance(e, ErrorEvent)]
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.HANDOFF
    text = "".join(e.content for e in events if isinstance(e, TokenEvent))
    assert text.startswith(DEFAULT_BUSY["en"].rstrip(":"))
    assert len(marked) == 1  # the next question is gated by the readiness cache
    assert len([e for e in events if isinstance(e, MetaEvent)]) == 1  # one meta frame
    # only the failed attempt is metered, as the failure it was: no answered question
    _io.meter.assert_awaited_once()
    assert _io.meter.await_args.kwargs["requests"] == 0


async def test_a_cloud_model_failure_is_still_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[object] = []
    monkeypatch.setattr(local_mod, "mark_unavailable", lambda s: marked.append(s))
    events = await _run(BrokenCloud(json_replies=[{}]))
    assert [e for e in events if isinstance(e, ErrorEvent)] and not marked
