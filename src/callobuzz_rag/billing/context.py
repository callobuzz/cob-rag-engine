"""Who is asking, for billing: the operator testing a customer's knowledge base is not charged.

An answering route calls `mark_admin_answer(request)` next to its question cap; `charge_usage`
(inside `record_usage`) reads the flag and writes a waived row (amount 0, `waived = 'admin'`) so
the answer is still metered and counted, just never billed to the customer. A ContextVar is
copied into the streamed response's generator and into tasks spawned afterwards, so it survives
`record_usage_new_session`; every answering route sets it (True or False), so a flag never
carries over from one request to the next.
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import Request

ADMIN_ANSWER: ContextVar[bool] = ContextVar("billing_admin_answer", default=False)


# PIP3 waiver: set by the fair-use gate when an answer is downgraded because the workspace's
# balance ran out (`"downgraded"`); None for every other answer. Like the admin flag, every
# answering route sets it through the gate, so it never carries over between requests.
WAIVED_REASON: ContextVar[str | None] = ContextVar("billing_waived_reason", default=None)


def mark_waived(reason: str | None) -> None:
    WAIVED_REASON.set(reason)


# /PIP3 waiver


def is_admin_request(request: "Request | None") -> bool:
    """Whether the request is made by the admin principal (admin key or super-admin session)."""
    return bool(request is not None and getattr(request.state, "is_admin", False))


def mark_admin_answer(request: "Request | None") -> bool:
    """Record whether this request is made by the admin principal (admin key or super-admin
    session). Returns the flag. Anything the operator does on a customer's knowledge base is
    never billed to the customer: answers, the assistant's helper calls, guided setup and the
    evaluation runs it starts."""
    is_admin = is_admin_request(request)
    ADMIN_ANSWER.set(is_admin)
    return is_admin
