# src/callobuzz_rag/conversation/state.py
"""What the director remembers about one conversation (`conversations.assistant_state`).

Every key of the layer exists from C1 on (README P10); C4/C5/C6 read and write only their
own keys (lead_*, handoff_*, cta_*/chip_*) and add methods for them.
"""

from dataclasses import asdict, dataclass, field, fields
from typing import TYPE_CHECKING, Any

from callobuzz_rag.conversation.types import Route, TurnLabel

if TYPE_CHECKING:
    from callobuzz_rag.db.models.conversation import Conversation

_RESOLVED = {Route.GROUNDED, Route.VERBATIM, Route.SMALLTALK}


@dataclass
class ConversationState:
    off_topic_strikes: int = 0
    abuse_strikes: int = 0
    unresolved_turns: int = 0
    low_conf_streak: int = 0
    clarify_used: bool = False
    stopped: bool = False
    cta_shown: list[str] = field(default_factory=list)
    cta_declined: list[str] = field(default_factory=list)
    lead_id: str | None = None
    lead_offers: int = 0
    lead_dismissed: bool = False
    handoff_offered: bool = False
    handoff_ids: list[str] = field(default_factory=list)
    message_count: int = 0
    last_label: str | None = None
    # C4 (leads)
    lead_form_ids: list[str] = field(default_factory=list)
    lead_offer_reason: str | None = None
    lead_capture: dict[str, Any] | None = None
    # C5 (handoff)
    last_handoff_reason: str | None = None
    # C6 (sales replies)
    chip_clicks: int = 0
    cta_clicks: int = 0
    # TV1 human
    miss_count: int = 0
    reply_fp: str | None = None
    repeat_count: int = 0
    # /TV1 human
    # TV2 reference: the visitor's last 3 masked questions, for the WhatsApp prefill
    recent_questions: list[str] = field(default_factory=list)
    # /TV2 reference
    # TV5 disclosure: the fixed "you are talking to an AI" line was already sent in this chat
    # (WhatsApp); a new service window is a new conversation and says it again
    ai_disclosed: bool = False

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "ConversationState":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (raw or {}).items() if k in known})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    # C6 (sales replies): the CTA and chip bookkeeping. `cta_declined`, `chip_clicks` and
    # `cta_clicks` are also written by the widget event route between two turns.
    def record_cta_shown(self, cta_type: str) -> None:
        if cta_type not in self.cta_shown:
            self.cta_shown.append(cta_type)

    def decline_cta(self, cta_type: str) -> None:
        if cta_type not in self.cta_declined:
            self.cta_declined.append(cta_type)

    def record_cta_click(self, cta_type: str) -> None:
        self.record_cta_shown(cta_type)
        self.cta_clicks += 1

    def record_chip_click(self) -> None:
        self.chip_clicks += 1

    def merge_event_keys(self, stored: dict[str, Any] | None) -> None:
        """Fold in what the event route wrote while this turn ran (a dismissed CTA must not
        come back, a click counter must not go down): union for `cta_declined`, max for the
        two counters. Call it on the locked row just before writing the state back."""
        if not stored:
            return
        for cta_type in stored.get("cta_declined") or []:
            self.decline_cta(str(cta_type))
        for name in ("chip_clicks", "cta_clicks"):
            value = stored.get(name)
            if isinstance(value, int):
                setattr(self, name, max(getattr(self, name), value))

    # C5 (handoff)
    def record_handoff_offer(self, reason: str | None = None) -> None:
        """A handoff card was shown in this conversation (C5 stops repeating it). The handoff
        row made on the visitor's tap carries `last_handoff_reason` as its reason."""
        self.handoff_offered = True
        self.last_handoff_reason = reason

    def record_handoff(self, handoff_id: str) -> None:
        """A handoff row was created for this conversation (once per id, last 20 kept)."""
        if handoff_id not in self.handoff_ids:
            self.handoff_ids = [*self.handoff_ids, handoff_id][-20:]

    def after_turn(
        self,
        *,
        label: TurnLabel,
        route: Route,
        gate_passed: bool | None,
        was_stopped: bool = False,  # TV1 human
        question: str | None = None,  # TV2 reference: the masked visitor message
    ) -> None:
        self.message_count += 1
        if question and question.strip():  # TV2 reference
            self.recent_questions = [*self.recent_questions, question.strip()[:200]][-3:]
        # TV1 human: a miss is a turn the assistant could not answer: no answer, a failed
        # confidence check, or a decline (but not the stop reply of an ended conversation)
        declined = route is Route.DECLINE and not was_stopped
        if route is Route.NO_ANSWER or gate_passed is False or declined:
            self.miss_count += 1
        self.last_label = label.value
        if route in _RESOLVED:
            self.unresolved_turns = 0
        elif route in (Route.NO_ANSWER, Route.DECLINE, Route.EXPLAINER):
            self.unresolved_turns += 1
        if gate_passed is False:
            self.low_conf_streak += 1
        elif gate_passed is True:
            self.low_conf_streak = 0


def load_state(conversation: "Conversation | None") -> ConversationState:
    """The state stored on a conversation; a fresh one for stateless turns."""
    if conversation is None:
        return ConversationState()
    return ConversationState.from_dict(getattr(conversation, "assistant_state", None))


def save_state(conversation: "Conversation", state: ConversationState) -> None:
    """Write the state back (the caller's session commits). A new dict, so SQLAlchemy sees
    the JSONB change without mutation tracking."""
    conversation.assistant_state = state.to_dict()
