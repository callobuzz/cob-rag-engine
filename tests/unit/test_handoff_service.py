"""Handoff storage, the callback window, the summary and the state methods (C5)."""

import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.handoffs import CallbackWindow
from callobuzz_rag.conversation.handoff.service import (
    create_handoff,
    handoff_payload,
    transcript_url,
    validate_callback_window,
)
from callobuzz_rag.conversation.handoff.summary import fallback_summary, summarize_handoff
from callobuzz_rag.conversation.schema import BusinessCard
from callobuzz_rag.conversation.state import ConversationState
from callobuzz_rag.conversation.types import OfficeState
from callobuzz_rag.db.models import Handoff
from callobuzz_rag.llm.models import LLMResponse

NOW = datetime(2026, 10, 5, 6, tzinfo=UTC)  # 11:30 IST, Monday
CARD = BusinessCard()


@pytest.mark.parametrize(
    ("window", "ok"),
    [
        ({"date": date(2026, 10, 5), "start": "16:00", "end": "18:00"}, True),
        ({"date": date(2026, 10, 5), "start": "09:00", "end": "10:00"}, False),  # already past
        ({"date": date(2026, 10, 6), "start": "18:00", "end": "17:00"}, False),  # inverted
        ({"date": date(2026, 10, 20), "start": "10:00", "end": "11:00"}, False),  # > 14 days
        ({"date": date(2026, 10, 19), "start": "10:00", "end": "11:00"}, True),  # 14 days
    ],
)
def test_callback_window(window: dict[str, object], ok: bool) -> None:
    w = CallbackWindow.model_validate(window)
    if ok:
        validate_callback_window(w, CARD, NOW)
    else:
        with pytest.raises(APIError) as err:
            validate_callback_window(w, CARD, NOW)
        assert err.value.code == "callback_window_invalid" and err.value.status_code == 422


def test_callback_window_time_format_and_strictness() -> None:
    with pytest.raises(ValueError):
        CallbackWindow.model_validate({"date": "2026-10-05", "start": "4pm", "end": "18:00"})
    with pytest.raises(ValueError):
        CallbackWindow.model_validate(
            {"date": "2026-10-05", "start": "16:00", "end": "18:00", "tz": "UTC"}
        )


async def test_create_handoff_adds_and_flushes() -> None:
    session = AsyncMock()
    session.add = MagicMock()
    row = await create_handoff(
        session,
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        conversation_id=None,
        channel="whatsapp",
        reason="keyword",
        language="hi-Latn",
        page_url="https://shree-labs.in/tests",
        office=OfficeState.UNKNOWN,
    )
    assert row.office_open is None and row.status == "new" and row.channel == "whatsapp"
    session.add.assert_called_once_with(row)
    session.flush.assert_awaited_once()
    session.commit.assert_not_awaited()
    closed = await create_handoff(
        session,
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        conversation_id=None,
        channel="call",
        reason="keyword",
        language=None,
        page_url=None,
        office=OfficeState.CLOSED,
    )
    assert closed.office_open is False


def test_transcript_url() -> None:
    cid, hid = uuid.uuid4(), uuid.uuid4()
    assert transcript_url("http://127.0.0.1:8090/", cid, hid) == (
        f"http://127.0.0.1:8090/leads?kb={cid}&tab=handoffs&handoff={hid}"
    )


def test_payload_shape() -> None:
    h = Handoff(
        id=uuid.uuid4(),
        corpus_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        channel="call",
        reason="frustration",
        language="en",
        page_url=None,
        office_open=True,
        created_at=NOW,
    )
    p = handoff_payload(
        h,
        summary="Asked about fasting.",
        question="Fasting?",
        visitor=None,
        transcript_url="http://x/leads",
        sources=["test-prep-guide.pdf"],
    )
    assert p["handoff_id"] == str(h.id)
    assert p["visitor"] == {"name": None, "phone": None, "email": None}
    assert p["reason"] == "frustration" and p["reason_label"] == "Seemed frustrated"
    assert p["created_at"] == "2026-10-05T06:00:00+00:00"


def test_fallback_summary() -> None:
    s = fallback_summary("Is the lipid profile fasting? " * 20, "keyword")
    assert s.startswith("Visitor asked: ")
    assert s.endswith("Reason: Mentioned a keyword you watch (refund, cancel, complaint…).")
    assert len(s) < 400


async def test_summarize_uses_the_shared_summarizer_and_falls_back() -> None:
    llm = AsyncMock()
    llm.has_credentials = True
    llm.fast_call_options = lambda: {"model": "fast"}
    llm.complete = AsyncMock(
        return_value=LLMResponse(content="Priya wants a slot.\nShe asked twice.", model="fast")
    )
    out = await summarize_handoff(
        llm,
        [("user", "home collection?")],
        reason="explicit_request",
        language="hi-Latn",
        question="home collection?",
    )
    assert out == "Priya wants a slot. She asked twice."
    assert llm.complete.await_args.kwargs["model"] == "fast"
    llm.complete = AsyncMock(side_effect=RuntimeError("down"))
    out = await summarize_handoff(
        llm, [("user", "x")], reason="keyword", language="en", question="refund?"
    )
    assert out.startswith("Visitor asked: refund?")
    none = await summarize_handoff(None, [], reason="keyword", language="en", question="q")
    assert none.startswith("Visitor asked")


def test_state_methods() -> None:
    s = ConversationState()
    s.record_handoff_offer("keyword")
    s.record_handoff("h1")
    s.record_handoff("h1")
    assert s.handoff_offered and s.handoff_ids == ["h1"]
    assert s.last_handoff_reason == "keyword"
    for i in range(30):
        s.record_handoff(f"x{i}")
    assert len(s.handoff_ids) == 20 and s.handoff_ids[-1] == "x29"
