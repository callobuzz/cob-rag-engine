"""The assistant while the business's website is still being learned (BIZ4, D15).

A question gets a polite "I'm still learning" reply and the handoff card, with no search and no
model call; greetings and rules still answer; billing's pause message wins when both apply.
The flag itself is one query, and only true while a site's first pass is running and the
knowledge base has no active document of its own.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from callobuzz_rag.api.schemas.assistant import HandoffCardBlock
from callobuzz_rag.conversation import learning
from callobuzz_rag.conversation.envelope import DoneEvent, TokenEvent, UiEvent
from callobuzz_rag.conversation.learning import LEARNING_TEXT
from callobuzz_rag.conversation.offers import OfferKind, OfferRegistry
from callobuzz_rag.conversation.schema import DEFAULT_PAUSED
from callobuzz_rag.conversation.types import Route
from tests.unit.conversation_helpers import FakeLLM, fake_orch, make_director, make_turn


@pytest.fixture(autouse=True)
def _io(director_io: Any) -> Any:
    return director_io


class Card:
    kind = OfferKind.HANDOFF_CARD

    def wanted(self, ctx: Any) -> str | None:
        return None

    async def build(self, ctx: Any, reason: str) -> HandoffCardBlock:
        return HandoffCardBlock(open_now=True, message="Talk to us", channels=[], reason=reason)


def _registry() -> OfferRegistry:
    reg = OfferRegistry()
    reg.register(Card())
    return reg


async def _run(
    text: str, *, learning_now: bool = True, paused: bool = False, **cfg: Any
) -> tuple[list[Any], str, FakeLLM, Any]:
    llm = FakeLLM(stream=["answered from the documents"])
    turn = make_turn(text, **cfg)
    turn.learning = learning_now
    turn.billing_paused = paused
    orch = fake_orch()
    events = [e async for e in make_director(llm, orch, _registry()).run_stream(turn)]
    spoken = "".join(e.content for e in events if isinstance(e, TokenEvent))
    return events, spoken, llm, orch


async def test_a_question_waits_politely_with_the_handoff_card_and_no_model() -> None:
    events, spoken, llm, orch = await _run("What are the charges for a root canal?")
    done = events[-1]
    assert isinstance(done, DoneEvent) and done.route == Route.HANDOFF
    assert spoken.startswith("I'm still learning about Shree Labs")
    assert [type(e) for e in events if isinstance(e, UiEvent)] == [UiEvent]
    assert llm.calls == []  # no labelling, no answer
    orch.run_retrieval_pipeline.assert_not_awaited()


async def test_a_hinglish_question_is_answered_in_hinglish() -> None:
    _, spoken, llm, orch = await _run("Root canal ka kitna charge hai?")
    assert spoken.startswith(LEARNING_TEXT["hi-Latn"].format(business_name="Shree Labs")), spoken
    assert llm.calls == []
    orch.run_retrieval_pipeline.assert_not_awaited()


async def test_a_greeting_still_gets_the_greeting() -> None:
    events, spoken, llm, _ = await _run("Namaste")
    assert events[-1].route == Route.SMALLTALK  # type: ignore[union-attr]
    assert "learning" not in spoken.lower() and "seekh" not in spoken.lower()
    assert llm.calls == []


async def test_once_the_first_pass_is_done_questions_take_the_normal_path() -> None:
    events, spoken, _llm, orch = await _run(
        "What are the charges for home collection?", learning_now=False
    )
    assert events[-1].route == Route.GROUNDED  # type: ignore[union-attr]
    assert "still learning" not in spoken
    orch.run_retrieval_pipeline.assert_awaited()


async def test_billing_pause_wins_when_both_apply() -> None:
    events, spoken, llm, _ = await _run("What are the charges for a root canal?", paused=True)
    assert spoken.startswith(DEFAULT_PAUSED["en"].rstrip(":"))
    assert "still learning" not in spoken
    assert events[-1].paused is True  # type: ignore[union-attr]
    assert llm.calls == []


async def test_without_a_handoff_card_the_contact_line_follows() -> None:
    events, spoken, _, _ = await _run(
        "What are the charges for a root canal?", handoff={"enabled": False}
    )
    assert spoken.startswith("I'm still learning about Shree Labs")
    assert "+919822012345" in spoken
    assert not [e for e in events if isinstance(e, UiEvent)]


async def test_a_business_without_a_name_is_spoken_of_plainly() -> None:
    _, spoken, _, _ = await _run(
        "What are the charges for a root canal?", identity={"business_name": ""}
    )
    assert spoken.startswith("I'm still learning about this business")


async def test_the_learning_turn_is_not_metered(_io: Any) -> None:
    await _run("What are the charges for a root canal?")
    _io.meter.assert_not_awaited()


def test_every_language_has_the_business_name_slot() -> None:
    assert set(LEARNING_TEXT) == {"en", "hi", "hi-Latn"}
    for text in LEARNING_TEXT.values():
        assert "{business_name}" in text


# ---------------------------------------------------------------- the flag


def _session(result: Any) -> MagicMock:
    session = MagicMock()
    answer = MagicMock()
    answer.scalar.return_value = result
    session.execute = AsyncMock(return_value=answer)
    return session


async def test_the_flag_is_one_query() -> None:
    session = _session(True)
    assert await learning.kb_is_learning(session, uuid.uuid4()) is True
    assert session.execute.await_count == 1


async def test_a_false_or_odd_answer_is_not_learning() -> None:
    assert await learning.kb_is_learning(_session(False), uuid.uuid4()) is False
    assert await learning.kb_is_learning(_session(None), uuid.uuid4()) is False
    assert await learning.kb_is_learning(_session(MagicMock()), uuid.uuid4()) is False


async def test_a_database_error_never_blocks_an_answer() -> None:
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("database is down"))
    assert await learning.kb_is_learning(session, uuid.uuid4()) is False


def test_the_query_names_a_running_first_pass_and_no_document_of_their_own() -> None:
    sql = str(learning.learning_statement(uuid.uuid4()).compile(dialect=postgresql.dialect()))
    assert "website_sources" in sql and "first_pass_done_at IS NULL" in sql
    assert "'discovering'" in sql.replace("%(", "'").replace(")s", "'") or "IN" in sql
    assert "NOT (EXISTS" in sql and "document_versions" in sql
    assert "system:" in sql or "LIKE" in sql  # the business card is the engine's, not theirs


async def test_the_surfaces_pass_the_flag_to_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    from callobuzz_rag.conversation import surfaces

    seen: list[bool] = []

    def fake_make_turn(**kw: Any) -> Any:
        seen.append(kw["learning"])
        return MagicMock()

    monkeypatch.setattr(learning, "kb_is_learning", AsyncMock(return_value=True))
    monkeypatch.setattr(surfaces, "make_turn", fake_make_turn)
    monkeypatch.setattr(surfaces, "build_director", lambda *a, **k: MagicMock())
    corpus = MagicMock(id=uuid.uuid4())
    body = MagicMock(query="hi", assistant=None, options=None, system_prompt=None)
    await surfaces._turn_for_query(  # type: ignore[attr-defined]
        MagicMock(), AsyncMock(), corpus, body, MagicMock(), "api", MagicMock()
    )
    assert seen == [True]
