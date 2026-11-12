# src/callobuzz_rag/conversation/signals.py
"""Facts about the current turn that triggers (lead, handoff, CTA) decide on, and the
business's opening hours. `office_state` is the only opening-hours function of the layer;
C5 imports it (README P7)."""

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from callobuzz_rag.conversation.types import OfficeState, Route, TurnLabel

if TYPE_CHECKING:
    from callobuzz_rag.conversation.schema import BusinessCard

_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def office_state(card: "BusinessCard", now: datetime) -> OfficeState:
    """OPEN / CLOSED in the card's timezone; UNKNOWN when no hours are stated.

    A day may have several ranges. A range whose close is at or before its open runs
    overnight: Friday 18:00-02:00 is open from Friday 18:00 to Saturday 02:00. A holiday
    (the local date) is closed all day.
    """
    if not card.hours:
        return OfficeState.UNKNOWN
    local = now.astimezone(ZoneInfo(card.timezone))
    if any(h.date == local.date() for h in card.holidays):
        return OfficeState.CLOSED
    today = _DAYS[local.weekday()]
    yesterday = _DAYS[(local - timedelta(days=1)).weekday()]
    current = local.time()
    for slot in card.hours:
        opens = time.fromisoformat(slot.open)
        closes = time.fromisoformat(slot.close)
        overnight = closes <= opens
        if slot.day == today:
            # "23:59" is how an all-day range ends: open through the last minute of the day.
            through_day_end = closes == time(23, 59)
            if not overnight and opens <= current and (current < closes or through_day_end):
                return OfficeState.OPEN
            if overnight and current >= opens:
                return OfficeState.OPEN
        if slot.day == yesterday and overnight and current < closes:
            return OfficeState.OPEN
    return OfficeState.CLOSED


@dataclass(frozen=True)
class TurnSignals:
    label: TurnLabel
    label_confidence: float
    topic_id: str | None
    frustrated: bool
    needs_human: bool
    route: Route
    gate_passed: bool | None
    top_score: float | None
    no_answer: bool
    office: OfficeState
    page_url: str | None
    language: str
    message_count: int
    keyword_hits: list[str]
    text: str  # the masked visitor message (never called `question`)
    abuse_level: int = 0
    blocked_topic_id: str | None = None
    blocked_action: str | None = None  # "decline" | "handoff"
    asked_for_contact: bool = False
    is_preview: bool = False
    stopped: bool = False  # TV1 human: the conversation was already stopped before this turn
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def for_test(cls, **overrides: Any) -> "TurnSignals":
        """A complete signal set for tests in any epic; override what the test is about."""
        values: dict[str, Any] = {
            "label": TurnLabel.BUSINESS_QUESTION,
            "label_confidence": 0.9,
            "topic_id": None,
            "frustrated": False,
            "needs_human": False,
            "route": Route.GROUNDED,
            "gate_passed": True,
            "top_score": 0.7,
            "no_answer": False,
            "office": OfficeState.UNKNOWN,
            "page_url": None,
            "language": "en",
            "message_count": 1,
            "keyword_hits": [],
            "text": "",
        }
        unknown = set(overrides) - {f.name for f in fields(cls)}
        if unknown:
            raise TypeError(f"unknown TurnSignals fields: {sorted(unknown)}")
        values.update(overrides)
        return cls(**values)
