"""Webhook event types and payload definitions.

Events represent significant kernel state changes that external systems
(e.g., Control Plane) may want to react to.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class WebhookEvent(StrEnum):
    """Events that can trigger outbound webhooks."""

    EVAL_SCORE_DROP = "eval.score_drop"
    KNOWLEDGE_GAP_DETECTED = "knowledge_gap.detected"
    OPTIMIZATION_COMPLETE = "optimization.complete"
    CONFIG_CHANGED = "config.changed"
    LEAD_CREATED = "lead.created"
    HANDOFF_REQUESTED = "handoff.requested"
    # BIL1: a provider's balance (OpenRouter, OpenAI, Anthropic, manual) crossed its alert level
    BILLING_PROVIDER_LOW_BALANCE = "billing.provider_low_balance"
    # BIL4: a workspace's balance crossed a low-balance level, or ran out (answers paused)
    BILLING_LOW_BALANCE = "billing.low_balance"
    BILLING_PAUSED = "billing.paused"
    # PIP3: a business (or the engine, tenant null) reached a fair-use limit or its hard line
    FAIR_USE_LIMIT_REACHED = "fair_use.limit_reached"
    # P1: an online payment was received and credited (no card or UPI data, never a key)
    BILLING_PAYMENT_RECEIVED = "billing.payment_received"


# Carry a visitor's contact data: delivered only to the knowledge base's own webhooks.
CORPUS_ONLY_EVENTS: frozenset[WebhookEvent] = frozenset(
    {WebhookEvent.LEAD_CREATED, WebhookEvent.HANDOFF_REQUESTED}
)


@dataclass(frozen=True)
class WebhookPayload:
    """Structured payload sent to webhook subscribers."""

    event: WebhookEvent
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default="")

    def __post_init__(self) -> None:
        if not self.timestamp:
            object.__setattr__(
                self,
                "timestamp",
                datetime.now(UTC).isoformat(),
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict for HTTP body."""
        return {
            "event": self.event.value,
            "timestamp": self.timestamp,
            "data": self.data,
        }
