"""TV5: answers know when their sources were last updated."""

import inspect
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import callobuzz_rag.conversation.answering as answering
from callobuzz_rag.conversation.envelope import source_of, widget_source
from callobuzz_rag.conversation.freshness import updated_on
from callobuzz_rag.retrieval.context_assembly import ContextAssembler, ContextSearchResult
from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator
from callobuzz_rag.retrieval.prompt_templates import format_citation
from tests.unit.conversation_helpers import make_config
from tests.unit.test_context_assembler import (
    _fixed_counter,
    _mock_chunk,
    _mock_session,
    _mock_settings,
)


def test_format_citation_default_is_byte_identical() -> None:
    assert (
        format_citation(1, "Root canal: Rs 6,000", "pricing", None, "Fees")
        == "[1] (source: pricing, section: Fees)\nRoot canal: Rs 6,000"
    )


def test_format_citation_with_a_date() -> None:
    assert (
        format_citation(1, "Root canal: Rs 6,000", "pricing", None, "Fees", updated="2026-10-03")
        == "[1] (source: pricing, section: Fees, updated: 2026-10-03)\nRoot canal: Rs 6,000"
    )


def test_a_date_comes_after_the_page() -> None:
    assert (
        format_citation(2, "Text", "menu.pdf", 4, updated="2026-09-12")
        == "[2] (source: menu.pdf, page: 4, updated: 2026-09-12)\nText"
    )


def test_dates_are_in_the_business_time_zone() -> None:
    created = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)  # 3 October, 01:30 in Pune
    assert updated_on(created, "Asia/Kolkata") == "2026-10-03"
    assert updated_on(created, "UTC") == "2026-10-02"


def test_a_missing_or_odd_date_is_no_date() -> None:
    assert updated_on(None, "Asia/Kolkata") is None
    assert updated_on("yesterday", "Asia/Kolkata") is None  # type: ignore[arg-type]
    # an unknown zone falls back to UTC rather than failing the answer
    assert updated_on(datetime(2026, 10, 2, 20, 0, tzinfo=UTC), "Mars/Base") == "2026-10-02"
    assert updated_on(date(2026, 1, 1), "UTC") is None  # a plain date is not a moment


def _dated_chunk(cid: uuid.UUID, created: datetime | None) -> MagicMock:
    chunk = _mock_chunk(cid, "Root canal costs Rs 6,000.", file_name="pricing")
    chunk.structure_path = ["Fees"]
    chunk.document_version.created_at = created
    return chunk


async def _assemble(created: datetime | None, **kwargs: Any) -> Any:
    cid = uuid.uuid4()
    session = _mock_session([_dated_chunk(cid, created)])
    assembler = ContextAssembler(
        _mock_settings(context_max_tokens=500), token_counter=_fixed_counter(10)
    )
    return await assembler.assemble(
        session, [ContextSearchResult(id=cid, score=0.9)], "Root canal price?", **kwargs
    )


async def test_the_prompt_carries_the_date_and_needs_no_extra_query() -> None:
    created = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
    got = await _assemble(created, updated_tz="Asia/Kolkata")
    assert "updated: 2026-10-03" in got.messages[-1].content
    assert got.cited_chunks[0].updated_at == "2026-10-03"


async def test_without_the_flag_the_prompt_is_unchanged() -> None:
    created = datetime(2026, 10, 2, 20, 0, tzinfo=UTC)
    got = await _assemble(created)
    assert "updated" not in got.messages[-1].content
    assert got.cited_chunks[0].updated_at is None


async def test_a_version_without_a_date_never_fails_the_answer() -> None:
    got = await _assemble(None, updated_tz="Asia/Kolkata")
    assert "updated:" not in got.messages[-1].content
    assert got.chunks_included == 1


def _cited(**extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        citation_index=1,
        chunk_id=uuid.uuid4(),
        document_id=None,
        external_id=None,
        file_name="pricing",
        section="Fees",
        page=None,
        text="Root canal costs Rs 6,000.",
        audience=None,
        score=0.9,
        **extra,
    )


def test_sources_carry_the_date_and_the_widget_shows_it() -> None:
    source = source_of(_cited(updated_at="2026-10-03"))
    assert source["updated_at"] == "2026-10-03"
    assert widget_source(source)["updated_at"] == "2026-10-03"


def test_a_chunk_without_a_date_has_none() -> None:
    assert source_of(_cited())["updated_at"] is None


async def test_the_assistant_asks_for_dates_in_the_card_time_zone(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    class Orchestrator:
        async def run_retrieval_pipeline(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            raise RuntimeError("stop here")

    class Session:
        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        answering,
        "compile_prompt",
        lambda *_a, **_k: SimpleNamespace(system_prompt="s", constraints="c"),
    )
    config = make_config()
    deps = SimpleNamespace(
        llm=SimpleNamespace(has_credentials=False),
        session_factory=Session,
        orchestrator_factory=lambda _s: Orchestrator(),
        retrieval_kwargs={},
        settings=SimpleNamespace(),
        translate_cache=None,
    )
    retrieved = await answering.start_retrieval(
        deps,  # type: ignore[arg-type]
        corpus_id=uuid.uuid4(),
        text="price?",
        info=SimpleNamespace(code="en"),  # type: ignore[arg-type]
        config=config,
        compile_turn=MagicMock(),
        applied_embedding=None,
    )
    assert retrieved.error == "stop here"
    assert seen["updated_tz"] == config.identity.card.timezone


def test_the_orchestrator_accepts_the_flag() -> None:
    assert "updated_tz" in inspect.signature(QueryOrchestrator.run_retrieval_pipeline).parameters


def test_the_widget_entry_has_no_date_key_when_unknown() -> None:
    assert "updated_at" not in widget_source(source_of(_cited()))


def _gate_for(updated_at: str | None) -> Any:
    from callobuzz_rag.conversation.answering import answer_gate, source_passages, source_updates

    source: dict[str, Any] = {"text": "Root canal: Rs 6,000", "updated_at": updated_at}
    compiled = SimpleNamespace(system_prompt="s", speakable=())
    return answer_gate(
        make_config(),
        compiled,
        source_passages([source]),
        "Root canal price?",
        "en",
        updates=source_updates([source]),
    )


def _stream(gate: Any, text: str) -> str:
    out = "".join(gate.feed(text))
    out += "".join(gate.flush())
    return out


def test_a_sentence_giving_the_source_date_is_not_removed() -> None:
    gate = _gate_for("2026-10-03")
    text = "Root canal costs Rs 6,000. It was last updated on 3 October 2026."
    _stream(gate, text)
    assert gate.removed == 0


def test_a_different_date_is_still_removed() -> None:
    gate = _gate_for("2026-10-03")
    _stream(gate, "Root canal costs Rs 6,000. It was last updated on 5 November 2026.")
    assert gate.removed == 1


def test_without_a_source_date_a_date_claim_is_removed() -> None:
    gate = _gate_for(None)
    _stream(gate, "Root canal costs Rs 6,000. It was last updated on 3 October 2026.")
    assert gate.removed == 1


def test_the_update_date_supports_only_a_sentence_about_updating() -> None:
    """The page's edit date is not a fact about anything else on the page."""
    gate = _gate_for("2026-10-03")
    out = _stream(
        gate,
        "The next free check-up camp is on 3 October 2026. "
        "This information was last updated on 3 October 2026.",
    )
    assert gate.removed == 1
    assert "check-up camp" not in out and "last updated on 3 October 2026" in out


def test_an_invented_event_date_equal_to_the_update_date_is_removed() -> None:
    gate = _gate_for("2026-10-03")
    _stream(gate, "The free camp is on 03/10/2026.")
    assert gate.removed == 1


@pytest.mark.parametrize(
    "sentence",
    [
        "Last updated: 3 October 2026.",
        "The page was updated on 3 October 2026.",
        "We updated this on 03/10/2026.",
    ],
)
def test_update_wording_supports_the_update_date(sentence: str) -> None:
    gate = _gate_for("2026-10-03")
    _stream(gate, sentence)
    assert gate.removed == 0


def test_source_passages_no_longer_carry_the_update_date() -> None:
    from callobuzz_rag.conversation.answering import source_passages, source_updates

    sources = [{"text": "Root canal: Rs 6,000", "updated_at": "2026-10-03"}]
    assert source_passages(sources) == ["Root canal: Rs 6,000"]
    assert source_updates(sources) == ["updated 3 October 2026 (03/10/2026)"]
