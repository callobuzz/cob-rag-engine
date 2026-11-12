# tests/unit/test_conv_envelope.py
"""C1: the envelope models and the SSE shape of each surface."""

import json
import uuid

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.assistant import (
    AssistantRequestOptions,
    Cta,
    HandoffCardBlock,
    LeadFormBlock,
)
from callobuzz_rag.api.schemas.conversations import MessageSendRequest
from callobuzz_rag.api.schemas.query import QueryRequest, QueryResponse
from callobuzz_rag.api.schemas.widget import WidgetChatRequest
from callobuzz_rag.conversation.envelope import (
    DoneEvent,
    MetaEvent,
    SourcesEvent,
    StageEvent,
    Surface,
    TokenEvent,
    TraceEvent,
    UiEvent,
    to_sse,
)
from callobuzz_rag.conversation.types import Route, Script, TurnLabel

QID = uuid.uuid4()


def _parse(raw: str) -> tuple[str, dict]:
    lines = dict(line.split(": ", 1) for line in raw.strip().split("\n"))
    return lines["event"], json.loads(lines["data"])


class TestModels:
    def test_query_request_options_are_strict(self) -> None:
        QueryRequest(query="hi", assistant={"use_draft": True, "debug": True})
        with pytest.raises(ValidationError):
            QueryRequest(query="hi", assistant={"draft": True})
        with pytest.raises(ValidationError):
            AssistantRequestOptions(simulate={"office_hours": "maybe"})

    def test_query_response_is_backwards_compatible(self) -> None:
        r = QueryResponse(
            answer="a", citations=[], confidence=1.0, query_id=QID, model_used="m", latency_ms=1
        )
        dumped = r.model_dump()
        assert dumped["label"] is None and dumped["quick_replies"] == [] and dumped["ui"] is None

    def test_ui_block_discriminates(self) -> None:
        r = QueryResponse(
            answer="a",
            confidence=1.0,
            query_id=QID,
            model_used="m",
            latency_ms=1,
            ui={
                "kind": "handoff_card",
                "open_now": True,
                "message": "m",
                "channels": [],
                "reason": "explicit_request",
            },
        )
        assert isinstance(r.ui, HandoffCardBlock)

    def test_widget_chat_new_fields_bounded(self) -> None:
        WidgetChatRequest(
            message="hi",
            session="ws_" + "a" * 43,
            page_url="https://shree-labs.in/tests",
            utm={"utm_source": "google"},
        )
        with pytest.raises(ValidationError):
            WidgetChatRequest(message="hi", utm={f"k{i}": "v" for i in range(6)})
        with pytest.raises(ValidationError):
            WidgetChatRequest(message="hi", session="not-a-session")
        with pytest.raises(ValidationError):
            WidgetChatRequest(message="hi", debug=True)  # never on the widget

    def test_message_send_takes_assistant_options(self) -> None:
        MessageSendRequest(content="hi", assistant={"use_draft": True})

    def test_reply_models_come_from_c2(self) -> None:
        # C2 defines them (README P1); C1 only uses them.
        Cta(type="whatsapp", label="WhatsApp us", url="https://wa.me/919822012345")
        LeadFormBlock(
            form_id="lf_" + "a" * 16,
            title="t",
            notice="n",
            consent_label="c",
            privacy_url=None,
            fields=[],
            submit_label="Send",
            trigger="buying_intent",
        )
        assert (
            HandoffCardBlock(open_now=True, message="m", channels=[], reason="r").callback_form
            is None
        )

    def test_done_event_carries_context_texts_but_never_sends_them(self) -> None:
        done = DoneEvent(
            query_id=QID,
            label=TurnLabel.BUSINESS_QUESTION,
            route=Route.GROUNDED,
            language="en",
            cta=None,
            timings={"search": 610},
            gate_threshold=0.61,
            claims_removed=0,
            model="m",
            usage={"total_tokens": 1},
            latency_ms=1,
            text="t",
            message_id=None,
            conversation_id=None,
            context_texts=["Fast 10-12 hours."],
        )
        assert done.context_texts == ["Fast 10-12 hours."]
        _, data = _parse(to_sse(done, Surface.STREAM) or "")
        assert "context_texts" not in data


class TestSse:
    META = MetaEvent(
        query_id=QID,
        conversation_id=None,
        session="ws_abc",
        label=TurnLabel.BUSINESS_QUESTION,
        route=Route.GROUNDED,
        language="en",
        script=Script.LATIN,
        assistant_version=3,
    )

    def test_meta_shape(self) -> None:
        name, data = _parse(to_sse(self.META, Surface.WIDGET) or "")
        assert name == "meta" and data["session"] == "ws_abc"
        assert data["label"] == "business_question" and data["assistant_version"] == 3

    def test_session_only_on_widget(self) -> None:
        _, data = _parse(to_sse(self.META, Surface.STREAM) or "")
        assert data["session"] is None

    def test_sources_per_surface(self) -> None:
        src = SourcesEvent(
            sources=[
                {
                    "index": 1,
                    "chunk_id": "c1",
                    "document_id": None,
                    "external_id": None,
                    "file_name": "prep.pdf",
                    "section": "Lipid",
                    "page": 2,
                    "text": "Fast 10 hours",
                    "score": 0.9,
                    "score_breakdown": {"dense_score": 0.7},
                }
            ],
            query_id=QID,
        )
        _, stream = _parse(to_sse(src, Surface.STREAM) or "")
        assert stream["sources"][0]["score_breakdown"] == {"dense_score": 0.7}
        assert stream["query_id"] == str(QID) and stream["sources"][0]["snippet"] == "Fast 10 hours"
        _, widget = _parse(to_sse(src, Surface.WIDGET) or "")
        assert widget == {
            "sources": [
                {
                    "index": 1,
                    "file_name": "prep.pdf",
                    "section": "Lipid",
                    "page": 2,
                    "snippet": "Fast 10 hours",
                }
            ]
        }
        assert to_sse(src, Surface.WIDGET, show_sources=False) is None

    def test_trace_never_on_widget(self) -> None:
        assert to_sse(TraceEvent(trace={"x": 1}), Surface.WIDGET) is None
        assert _parse(to_sse(TraceEvent(trace={"x": 1}), Surface.STREAM) or "")[0] == "trace"

    def test_done_keeps_old_fields(self) -> None:
        done = DoneEvent(
            query_id=QID,
            label=TurnLabel.SMALLTALK,
            route=Route.SMALLTALK,
            language="en",
            cta=None,
            timings={"total": 5},
            claims_removed=0,
            model=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=5,
            text="Hi!",
            message_id=None,
            conversation_id=None,
        )
        _, stream = _parse(to_sse(done, Surface.STREAM) or "")
        assert {
            "query_id",
            "model",
            "latency_ms",
            "usage",
            "label",
            "route",
            "timings",
            "claims_removed",
            "cta",
            "language",
            "stage_timings_ms",
            "retrieval_ms",
            "processed_query",
        } <= stream.keys()
        _, conv = _parse(to_sse(done, Surface.CONVERSATION) or "")
        assert {"message_id", "total_tokens", "label", "timings", "latency_ms"} <= conv.keys()
        _, widget = _parse(to_sse(done, Surface.WIDGET) or "")
        assert "usage" not in widget and widget["route"] == "smalltalk"

    def test_token_stage_ui(self) -> None:
        assert _parse(to_sse(TokenEvent(content="Hi"), Surface.WIDGET) or "") == (
            "token",
            {"content": "Hi"},
        )
        assert (
            _parse(to_sse(StageEvent(stage="search", elapsed_ms=40), Surface.WIDGET) or "")[0]
            == "stage"
        )
        block = HandoffCardBlock(open_now=True, message="m", channels=[], reason="r")
        name, data = _parse(to_sse(UiEvent(block=block), Surface.WIDGET) or "")
        assert name == "ui" and data["kind"] == "handoff_card"


class TestDoneContract:
    DONE = DoneEvent(
        query_id=QID,
        label=TurnLabel.BUSINESS_QUESTION,
        route=Route.GROUNDED,
        language="en",
        cta=None,
        timings={"understand": 12, "search": 90, "write": 400},
        claims_removed=0,
        model="m",
        usage={"total_tokens": 9, "cost_usd": 0.002, "cost_known": True},
        latency_ms=520,
        text="t",
        message_id=None,
        conversation_id=None,
        stage_timings_ms={"embed": 30},
        retrieval_ms=102,
        processed_query="q",
        thinking_ms=250,
        reasoning_tokens=40,
        gate_threshold=0.61,
    )

    def test_gate_threshold_is_top_level_never_a_timing(self) -> None:
        for surface in (Surface.STREAM, Surface.CONVERSATION):
            _, data = _parse(to_sse(self.DONE, surface) or "")
            assert data["gate_threshold"] == 0.61
            assert set(data["timings"]) == {"understand", "search", "write"}

    def test_stream_done_keeps_every_field_the_playground_reads(self) -> None:
        _, data = _parse(to_sse(self.DONE, Surface.STREAM) or "")
        assert data["stage_timings_ms"] == {"embed": 30} and data["retrieval_ms"] == 102
        assert data["processed_query"] == "q" and data["thinking_ms"] == 250
        assert data["reasoning_tokens"] == 40 and data["usage"]["cost_usd"] == 0.002

    def test_conversation_done_keeps_its_fields(self) -> None:
        _, data = _parse(to_sse(self.DONE, Surface.CONVERSATION) or "")
        assert data["total_tokens"] == 9 and data["latency_ms"] == 520
        assert data["thinking_ms"] == 250 and data["reasoning_tokens"] == 40

    def test_widget_done_is_minimal(self) -> None:
        _, data = _parse(to_sse(self.DONE, Surface.WIDGET) or "")
        assert set(data) == {"label", "route", "language", "cta", "claims_removed"}

    def test_optional_fields_are_absent_when_unset(self) -> None:
        bare = DoneEvent(
            query_id=QID,
            label=TurnLabel.SMALLTALK,
            route=Route.SMALLTALK,
            language="en",
            cta=None,
            timings={},
            claims_removed=0,
            model=None,
            usage={},
            latency_ms=1,
            text="",
            message_id=None,
            conversation_id=None,
        )
        _, data = _parse(to_sse(bare, Surface.STREAM) or "")
        assert "thinking_ms" not in data and "reasoning_tokens" not in data
        assert "gate_threshold" not in data and "truncated" not in data

    def test_a_length_stop_is_marked_on_every_surface(self) -> None:
        from dataclasses import replace

        cut = replace(self.DONE, truncated=True)
        for surface in (Surface.STREAM, Surface.CONVERSATION, Surface.WIDGET):
            _, data = _parse(to_sse(cut, surface) or "")
            assert data["truncated"] is True


class TestRequestShapes:
    def test_retrieve_refuses_assistant_options(self) -> None:
        from callobuzz_rag.api.schemas.query import RetrieveRequest

        RetrieveRequest(query="x")
        with pytest.raises(ValidationError):
            RetrieveRequest(query="x", assistant={"use_draft": True})

    def test_widget_config_has_the_assistant_fields(self) -> None:
        from callobuzz_rag.api.schemas.widget import WidgetConfigResponse

        cfg = WidgetConfigResponse(
            title="t",
            greeting="g",
            placeholder="p",
            primary_color="#4f46e5",
            position="right",
            show_sources=True,
            assistant_name="Asha",
            ai_disclosure="AI assistant",
            language="en",
        )
        assert cfg.assistant_name == "Asha" and cfg.avatar_url is None


class TestSourceFormat:
    def test_stream_source_matches_the_old_route_formatter(self) -> None:
        from types import SimpleNamespace

        from callobuzz_rag.api.routes.query import _stream_source
        from callobuzz_rag.conversation.envelope import source_of, stream_source
        from callobuzz_rag.retrieval.models import ChunkScoreBreakdown

        cc = SimpleNamespace(
            citation_index=1,
            chunk_id=uuid.uuid4(),
            document_id=None,
            external_id=None,
            file_name="a.pdf",
            section="S",
            page=2,
            text="x" * 300,
            score=0.5,
        )
        bd = ChunkScoreBreakdown(fused_score=1.0, pipeline_rank=1, dense_score=0.7)
        assert stream_source(source_of(cc, bd)) == _stream_source(cc, bd)


class TestLabelSource:
    def test_meta_carries_the_label_source_only_when_set(self) -> None:
        plain = MetaEvent(
            query_id=QID,
            conversation_id=None,
            session=None,
            label=TurnLabel.SMALLTALK,
            route=Route.SMALLTALK,
            language="en",
            script=Script.LATIN,
            assistant_version=3,
        )
        assert "label_source" not in _parse(to_sse(plain, Surface.STREAM) or "")[1]
        debug = MetaEvent(
            query_id=QID,
            conversation_id=None,
            session=None,
            label=TurnLabel.SMALLTALK,
            route=Route.SMALLTALK,
            language="en",
            script=Script.LATIN,
            assistant_version=3,
            label_source="rules",
        )
        assert _parse(to_sse(debug, Surface.STREAM) or "")[1]["label_source"] == "rules"
