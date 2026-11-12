"""What every channel shares (TV6): the message types, the adapter protocol and the request
stand-in the answering gates need.

A channel is a place a customer can talk to the business's assistant besides the website widget.
WhatsApp is the first; the others come with the omnichannel inbox. A new channel implements
`ChannelAdapter` and answers through the conversation director, with the "talk to a person"
check first (`surfaces.is_human_turn`), then the question cap, billing and fair use
(`fairuse.gate.answer_gate_for`), passing a stable `request_id` so a retry of the same message
is metered and charged once.
"""

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class InboundMessage:
    """One thing a customer sent, in a channel-neutral shape."""

    channel: str
    external_id: str  # the channel's own id for the message (a wamid)
    contact: str  # the customer's address in E.164 form
    text: str
    kind: Literal["text", "reply", "unsupported"] = "text"
    profile_name: str | None = None
    sent_at: datetime | None = None
    reply_id: str | None = None  # the id of a tapped button


@dataclass(frozen=True)
class OutboundMessage:
    """One thing to send: text, or text with up to three buttons."""

    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)  # (id, title)


class ChannelAdapter(Protocol):
    """What the answer loop needs from a channel to send."""

    async def send_text(self, to: str, text: str) -> str: ...

    async def send_buttons(self, to: str, body: str, buttons: list[tuple[str, str]]) -> str: ...


class ChannelRequest:
    """A stand-in for the HTTP request the gates read: `request.app.state` (settings, session
    factory, fair-use Redis), `request.state.is_admin` (a customer is never the operator) and
    `request.headers` (a customer sends no `X-LLM-*`). A message that arrives by webhook and is
    answered by a worker has no request; this carries the same facts."""

    def __init__(self, *, settings: Any, session_factory: Any, redis: Any = None) -> None:
        self.app = SimpleNamespace(
            state=SimpleNamespace(
                settings=settings,
                session_factory=session_factory,
                fair_use_redis=redis,
                fair_use_tasks=set(),
            )
        )
        self.state = SimpleNamespace(is_admin=False)
        self.headers: dict[str, str] = {}
