"""Auto-title generation for conversations — derives a short title from first message."""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from callobuzz_rag.llm.models import LLMMessage, LLMResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_TITLE_PROMPT = (
    "Generate a short, descriptive title (under 60 characters) for a "
    "conversation that starts with the following message. Return ONLY "
    "the title text, no quotes or extra formatting.\n\n"
    "Message: {message}"
)


async def generate_title(
    session: "AsyncSession",
    llm: "LLMProvider",
    conversation_id: UUID,
    first_message: str,
    *,
    on_response: Callable[[LLMResponse], None] | None = None,
) -> str | None:
    """Generate and save a title for a conversation from its first message.

    Non-fatal: catches all exceptions and returns None on failure.
    Only sets the title if the conversation does not already have one.

    Runs on the provider's fast model: naming a conversation is not reasoning.
    `on_response` receives the model's reply, so the caller can meter the call.

    Returns the generated title, or None if skipped/failed.
    """
    try:
        from callobuzz_rag.db.models.conversation import Conversation

        stmt = select(Conversation).where(Conversation.id == conversation_id)
        result = await session.execute(stmt)
        conversation = result.scalar_one_or_none()

        if conversation is None:
            return None

        # Skip if title already set
        if conversation.title:
            return conversation.title

        prompt = _TITLE_PROMPT.format(message=first_message[:500])
        messages = [LLMMessage(role="user", content=prompt)]
        response = await llm.complete(messages, **_title_call_options(llm))
        if on_response is not None:
            on_response(response)

        title = response.content.strip()[:60]
        conversation.title = title
        await session.flush()

        logger.info(
            "Auto-generated title for conversation %s: %s",
            conversation_id,
            title,
        )
        return title
    except Exception:
        logger.warning(
            "Failed to auto-generate title for conversation %s",
            conversation_id,
            exc_info=True,
        )
        return None


def _title_call_options(llm: "LLMProvider") -> dict[str, Any]:
    """The fast model, a little warmer, and a short cap only where it is safe.

    `fast_call_options` leaves out the token cap for a model that may reason
    before answering (a tight cap would truncate it to nothing), so the title's
    own cap follows the same rule.
    """
    options = dict(llm.fast_call_options())
    options["temperature"] = 0.3
    if "max_tokens" in options:
        options["max_tokens"] = 60
    return options
