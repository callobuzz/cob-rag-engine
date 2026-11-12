"""Conversation summary service — generates and manages conversation summaries.

When a conversation exceeds a configurable message threshold, the service
generates a summary via LLM and stores it on the conversation record.
Context assembly uses the summary + recent messages to stay within token budget.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from callobuzz_rag.llm.models import LLMMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.llm.base import LLMProvider

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = (
    "You are a conversation summarizer. Given the following conversation, "
    "produce a concise summary that captures the key topics discussed, "
    "important facts mentioned, and any decisions or conclusions reached. "
    "Keep it under 200 words.\n\n"
    "Conversation:\n{transcript}"
)


class ConversationSummaryService:
    """Generates and manages conversation summaries via LLM."""

    def __init__(
        self,
        session: AsyncSession,
        llm: LLMProvider,
        *,
        summary_threshold: int = 10,
        model: str | None = None,
    ) -> None:
        self._session = session
        self._llm = llm
        self._summary_threshold = summary_threshold
        self._model = model

    async def maybe_update_summary(self, conversation_id: str) -> str | None:
        """Generate a summary if the conversation has enough messages.

        Returns the summary text if generated, None otherwise.
        """
        from sqlalchemy import select

        from callobuzz_rag.db.models.conversation import Conversation
        from callobuzz_rag.db.models.message import Message

        stmt = select(Conversation).where(Conversation.id == conversation_id)
        result = await self._session.execute(stmt)
        conversation = result.scalar_one_or_none()
        if conversation is None:
            return None

        if conversation.message_count < self._summary_threshold:
            return None

        # Fetch all messages for transcript
        msg_stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        msg_result = await self._session.execute(msg_stmt)
        messages = msg_result.scalars().all()

        if not messages:
            return None

        transcript = "\n".join(f"{m.role}: {m.content}" for m in messages)

        prompt = SUMMARY_PROMPT.format(transcript=transcript)
        llm_messages = [LLMMessage(role="user", content=prompt)]
        response = await self._llm.complete(
            llm_messages,
            model=self._model,
            temperature=0.3,
            max_tokens=512,
        )

        conversation.summary = response.content
        await self._session.flush()
        logger.info(
            "Updated summary for conversation %s (%d messages)",
            conversation_id,
            len(messages),
        )
        return response.content

    async def get_context_messages(
        self,
        conversation_id: str,
        *,
        max_messages: int = 20,
        max_tokens: int = 4096,
    ) -> list[LLMMessage]:
        """Return context messages for a conversation, token-budgeted.

        If a summary exists, it is prepended as a system message. Recent
        messages are then appended up to the token budget.
        """
        from sqlalchemy import select

        from callobuzz_rag.db.models.conversation import Conversation
        from callobuzz_rag.db.models.message import Message

        # Fetch conversation for summary
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id)
        conv_result = await self._session.execute(conv_stmt)
        conversation = conv_result.scalar_one_or_none()

        context: list[LLMMessage] = []
        token_budget = max_tokens

        # Prepend summary if available
        if conversation and conversation.summary:
            summary_msg = LLMMessage(
                role="system",
                content=f"Previous conversation summary:\n{conversation.summary}",
            )
            # Rough token estimate: ~4 chars per token
            summary_tokens = len(summary_msg.content) // 4
            context.append(summary_msg)
            token_budget -= summary_tokens

        # Fetch recent messages (most recent first, then reverse)
        msg_stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(max_messages)
        )
        msg_result = await self._session.execute(msg_stmt)
        messages = list(reversed(msg_result.scalars().all()))

        # Add messages until budget exhausted
        for msg in messages:
            estimated_tokens = msg.token_count if msg.token_count else (len(msg.content) // 4)
            if estimated_tokens > token_budget:
                break
            context.append(LLMMessage(role=msg.role, content=msg.content))
            token_budget -= estimated_tokens

        return context
