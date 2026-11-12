"""Director events and their SSE shape per surface (README "The reply envelope and SSE
contract"). The director yields these; surfaces only translate them.

`done` keeps every field each surface already sends (`/query/stream`: ids, usage with the real
cost, public stage timings, engine step timings, retrieval time, processed query, thinking
time; conversation messages: message id, tokens, timings, latency) and adds the assistant's
fields next to them. `timings` holds public stages only (the playground draws one bar per key);
the search-confidence threshold travels as its own `gate_threshold`.
"""

import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from callobuzz_rag.api.schemas.assistant import Cta, HandoffCardBlock, LeadFormBlock
from callobuzz_rag.api.streaming import sse_event
from callobuzz_rag.conversation.schema import QuickReply
from callobuzz_rag.conversation.types import Route, Script, TurnLabel

STREAM_SNIPPET = 200
WIDGET_SNIPPET = 240


class Surface(StrEnum):
    STREAM = "stream"
    WIDGET = "widget"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class MetaEvent:
    query_id: uuid.UUID
    conversation_id: uuid.UUID | None
    session: str | None
    label: TurnLabel
    route: Route  # the planned route; DoneEvent.route is final
    language: str
    script: Script
    assistant_version: int | None
    # How the label was decided (rules | cache | model | fallback). Debug turns only: it is
    # what the label benchmark reads without paying for an answer.
    label_source: str | None = None


@dataclass(frozen=True)
class StageEvent:
    stage: str  # understand | search | rerank | check | write | verify | form | handoff | queued
    elapsed_ms: int


@dataclass(frozen=True)
class SourcesEvent:
    sources: list[dict[str, Any]]  # `source_of` dicts: index, chunk_id, document_id, external_id,
    query_id: uuid.UUID  # file_name, section, page, text, score, score_breakdown


@dataclass(frozen=True)
class TokenEvent:
    content: str


@dataclass(frozen=True)
class QuickRepliesEvent:
    items: list[QuickReply]


@dataclass(frozen=True)
class UiEvent:
    block: LeadFormBlock | HandoffCardBlock


@dataclass(frozen=True)
class TraceEvent:
    trace: dict[str, Any]


@dataclass(frozen=True)
class DoneEvent:
    query_id: uuid.UUID
    label: TurnLabel
    route: Route
    language: str
    cta: Cta | None
    timings: dict[str, int | float]  # public stage ms only: understand, search, rerank, ...
    claims_removed: int
    model: str | None
    usage: dict[str, Any]  # token estimates + reasoning_tokens, cost_usd, cost_known
    latency_ms: int
    text: str
    message_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    user_message_id: uuid.UUID | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    quick_replies: list[QuickReply] = field(default_factory=list)
    ui: LeadFormBlock | HandoffCardBlock | None = None
    assistant_version: int | None = None
    trace: dict[str, Any] | None = None
    # The engine's finer pipeline steps (embed, search_dense, ...), the time to the end of
    # retrieval, the query as searched, and what a reasoning model reported: what the
    # playground and the costs screens read from `/query/stream` today.
    stage_timings_ms: dict[str, int] = field(default_factory=dict)
    retrieval_ms: int | None = None
    processed_query: str | None = None
    thinking_ms: int | None = None
    reasoning_tokens: int | None = None
    # The search-confidence threshold the turn was gated on (None = no threshold).
    gate_threshold: float | None = None
    # The model hit its token cap: the answer ends on its last complete sentence or row.
    truncated: bool = False
    # The chunk texts given to the model (README P15; C7's golden runs score them). Never
    # serialised by to_sse.
    context_texts: list[str] = field(default_factory=list)
    # BIL4 paused: the answer was the pause message (the workspace's balance ran out). Sent to
    # key and session callers, never to the widget surface.
    paused: bool = False
    # PIP3 gate: why (`billing`, `fair_use` or `local_unavailable`), sent next to `paused` to key and session callers
    pause_reason: str | None = None


@dataclass(frozen=True)
class ErrorEvent:
    detail: str
    code: str | None = None


DirectorEvent = (
    MetaEvent
    | StageEvent
    | SourcesEvent
    | TokenEvent
    | QuickRepliesEvent
    | UiEvent
    | TraceEvent
    | DoneEvent
    | ErrorEvent
)


def source_of(cc: Any, breakdown: Any = None) -> dict[str, Any]:
    """One cited chunk as the director and `/query/stream` describe it: the ids, the full
    text, the score and the per-signal score breakdown (None when the pipeline has none)."""
    return {
        "index": cc.citation_index,
        "chunk_id": str(cc.chunk_id),
        "document_id": str(cc.document_id) if cc.document_id else None,
        "external_id": cc.external_id,
        "file_name": cc.file_name,
        "section": cc.section,
        "page": cc.page,
        "text": cc.text,
        "audience": getattr(cc, "audience", None),
        "updated_at": getattr(cc, "updated_at", None),  # TV5 freshness
        "score": cc.score,
        "score_breakdown": asdict(breakdown) if breakdown is not None else None,
    }


def _snippet(text: str, limit: int) -> str:
    return text[:limit] + "..." if len(text) > limit else text


def stream_source(source: dict[str, Any]) -> dict[str, Any]:
    """The `sources[]` entry of `/query/stream` and conversation streams."""
    return {
        "index": source["index"],
        "chunk_id": source["chunk_id"],
        "document_id": source["document_id"],
        "external_id": source["external_id"],
        "file_name": source["file_name"],
        "section": source["section"],
        "page": source["page"],
        "snippet": _snippet(source["text"], STREAM_SNIPPET),
        "score": source["score"],
        "score_breakdown": source.get("score_breakdown"),
    }


def widget_source(source: dict[str, Any]) -> dict[str, Any]:
    """The `sources[]` entry the widget gets: no ids, no scores."""
    entry = {
        "index": source["index"],
        "file_name": source["file_name"],
        "section": source["section"],
        "page": source["page"],
        "snippet": source["text"][:WIDGET_SNIPPET],
    }
    if source.get("updated_at"):  # TV5 freshness: only when known, so other entries are unchanged
        entry["updated_at"] = source["updated_at"]
    return entry


def to_sse(event: DirectorEvent, surface: Surface, *, show_sources: bool = True) -> str | None:
    """The SSE frame for `event` on `surface`, or None when that surface does not get it."""
    if isinstance(event, MetaEvent):
        meta: dict[str, Any] = {
            "query_id": str(event.query_id),
            "conversation_id": str(event.conversation_id) if event.conversation_id else None,
            "session": event.session if surface == Surface.WIDGET else None,
            "label": event.label.value,
            "route": event.route.value,
            "language": event.language,
            "script": event.script.value,
            "assistant_version": event.assistant_version,
        }
        if event.label_source is not None:
            meta["label_source"] = event.label_source
        return sse_event("meta", meta)
    if isinstance(event, StageEvent):
        return sse_event("stage", {"stage": event.stage, "elapsed_ms": event.elapsed_ms})
    if isinstance(event, SourcesEvent):
        if surface == Surface.WIDGET:
            if not show_sources:
                return None
            return sse_event("sources", {"sources": [widget_source(s) for s in event.sources]})
        return sse_event(
            "sources",
            {"query_id": str(event.query_id), "sources": [stream_source(s) for s in event.sources]},
        )
    if isinstance(event, TokenEvent):
        return sse_event("token", {"content": event.content})
    if isinstance(event, QuickRepliesEvent):
        return sse_event("quick_replies", {"items": [q.model_dump() for q in event.items]})
    if isinstance(event, UiEvent):
        return sse_event("ui", event.block.model_dump())
    if isinstance(event, TraceEvent):
        return None if surface == Surface.WIDGET else sse_event("trace", event.trace)
    if isinstance(event, ErrorEvent):
        body: dict[str, Any] = {"detail": event.detail}
        if event.code:
            body["code"] = event.code
        return sse_event("error", body)
    return sse_event("done", _done_body(event, surface))


def _done_body(event: DoneEvent, surface: Surface) -> dict[str, Any]:
    assistant: dict[str, Any] = {
        "label": event.label.value,
        "route": event.route.value,
        "language": event.language,
        "cta": event.cta.model_dump() if event.cta else None,
        "claims_removed": event.claims_removed,
    }
    if event.truncated:
        assistant["truncated"] = True
    if surface == Surface.WIDGET:
        return assistant  # minimal: today's `done` was empty
    if event.paused:  # BIL4 paused
        assistant["paused"] = True
        if event.pause_reason:  # PIP3 gate
            assistant["pause_reason"] = event.pause_reason
    body: dict[str, Any]
    if surface == Surface.STREAM:
        body = {
            "query_id": str(event.query_id),
            "model": event.model,
            "latency_ms": event.latency_ms,
            "usage": event.usage,
            "timings": event.timings,
            "stage_timings_ms": event.stage_timings_ms,
            "retrieval_ms": event.retrieval_ms,
            "processed_query": event.processed_query,
        }
    else:
        body = {
            "message_id": str(event.message_id) if event.message_id else None,
            "total_tokens": event.usage.get("total_tokens", 0),
            "timings": event.timings,
            "latency_ms": event.latency_ms,
        }
    if event.thinking_ms is not None:
        body["thinking_ms"] = event.thinking_ms
    if event.reasoning_tokens:
        body["reasoning_tokens"] = event.reasoning_tokens
    if event.gate_threshold is not None:
        body["gate_threshold"] = event.gate_threshold
    return {**body, **assistant}
