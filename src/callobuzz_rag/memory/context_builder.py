"""Conversation context builder — assembles recent messages + summary for LLM."""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ConversationContextBuilder:
    """Builds conversation context (summary + recent messages) within a token budget.

    Used by ConversationService to inject conversation history into LLM calls.
    """

    def __init__(
        self,
        session: "AsyncSession",
        *,
        max_messages: int = 10,
        token_budget: int = 2000,
    ) -> None:
        self._session = session
        self._max_messages = max_messages
        self._token_budget = token_budget

    async def build_context(self, conversation_id: UUID) -> list[dict[str, Any]]:
        """Build context messages for a conversation.

        Returns a list of {"role": ..., "content": ...} dicts, starting with
        a summary (if available and message count exceeds max_messages), followed
        by recent messages within the token budget.
        """
        from callobuzz_rag.db.models.conversation import Conversation
        from callobuzz_rag.db.models.message import Message

        # Fetch conversation for summary
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_result = await self._session.execute(conv_stmt)
        conversation = conv_result.scalar_one_or_none()

        context: list[dict[str, Any]] = []
        remaining_budget = self._token_budget

        # Prepend summary if available and conversation is long enough
        if (
            conversation
            and conversation.summary
            and (conversation.message_count or 0) > self._max_messages
        ):
            summary_content = f"Previous conversation summary:\n{conversation.summary}"
            summary_tokens = len(summary_content) // 4
            context.append({"role": "system", "content": summary_content})
            remaining_budget -= summary_tokens

        # Fetch recent messages (most recent first, then reverse)
        msg_stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(self._max_messages)
        )
        msg_result = await self._session.execute(msg_stmt)
        messages = list(reversed(msg_result.scalars().all()))

        # Add messages until budget exhausted
        for msg in messages:
            estimated_tokens = len(msg.content) // 4
            if estimated_tokens > remaining_budget:
                break
            context.append({"role": msg.role, "content": msg.content})
            remaining_budget -= estimated_tokens

        return context
