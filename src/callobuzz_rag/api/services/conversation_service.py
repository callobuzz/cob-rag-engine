"""Conversation message send service — retrieve context, generate, store."""

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from callobuzz_rag.llm.models import LLMMessage, LLMResponse
from callobuzz_rag.memory.context_builder import ConversationContextBuilder

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from callobuzz_rag.llm.base import LLMProvider
    from callobuzz_rag.retrieval.models import RetrievalResult
    from callobuzz_rag.retrieval.search import HybridSearcher

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a helpful AI assistant. Answer the user's question based on the "
    "provided context. If the context does not contain enough information to "
    "answer, say so honestly. Be concise and accurate."
)


class ConversationNotFoundError(RAGKernelError):
    """Raised when the target conversation does not exist."""


class ConversationService:
    """Orchestrates the message send flow.

    1. Verify conversation exists
    2. Store user message
    3. Load recent history via ConversationContextBuilder
    4. Retrieve relevant documents (if searcher available)
    5. Build LLM messages (system + context + history + user)
    6. Generate response via LLM
    7. Store assistant message with retrieval_context
    8. Update conversation counters
    9. Auto-generate title on first message (if enabled)
    """

    def __init__(
        self,
        session: "AsyncSession",
        llm_provider: "LLMProvider",
        searcher: "HybridSearcher | None" = None,
        *,
        max_conversation_history: int = 10,
        conversation_context_budget: int = 2000,
        auto_title_enabled: bool = True,
    ) -> None:
        self._session = session
        self._llm = llm_provider
        self._searcher = searcher
        self._auto_title_enabled = auto_title_enabled
        # Every model reply made while answering, answer first, for metering.
        self.llm_responses: list[LLMResponse] = []
        self._context_builder = ConversationContextBuilder(
            session,
            max_messages=max_conversation_history,
            token_budget=conversation_context_budget,
        )

    async def send_message(
        self,
        conversation_id: UUID,
        corpus_id: UUID,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Message, Message, dict[str, Any] | None]:
        """Execute the full message send flow.

        Returns:
            (user_message, assistant_message, retrieval_context)

        Raises:
            ConversationNotFoundError: If the conversation does not exist.
        """
        # 1. Verify conversation exists and belongs to corpus
        conversation = await self._get_conversation(conversation_id, corpus_id)

        # Track whether this is the first message (for auto-title)
        is_first_message = (conversation.message_count or 0) == 0

        # 2. Store user message
        user_msg = Message(
            conversation_id=conversation_id,
            role="user",
            content=content,
            token_count=0,
        )
        self._session.add(user_msg)
        await self._session.flush()

        # 3. Get recent messages via context builder
        history_dicts = await self._context_builder.build_context(conversation_id)

        # 4. Retrieve relevant documents
        retrieval_context: dict[str, Any] | None = None
        context_text = ""
        if self._searcher is not None:
            try:
                results = await self._retrieve_context(content, corpus_id)
                if results:
                    context_text = self._format_retrieval_context(results)
                    retrieval_context = {
                        "chunks": [
                            {
                                "chunk_id": r.chunk_id,
                                "text": r.text[:500],
                                "score": r.score,
                                "document_id": r.document_id,
                            }
                            for r in results
                        ],
                        "total_chunks": len(results),
                    }
            except Exception:
                logger.warning(
                    "Retrieval failed for conversation %s, proceeding without context",
                    conversation_id,
                    exc_info=True,
                )

        # 5. Build LLM messages
        llm_messages = self._build_llm_messages(
            context_text=context_text,
            history=history_dicts,
            user_content=content,
        )

        # 6. Generate response via LLM
        llm_response = await self._llm.complete(llm_messages)
        self.llm_responses.append(llm_response)

        # 7. Store assistant message
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=llm_response.content,
            token_count=llm_response.usage.total_tokens,
            retrieval_context=retrieval_context,
        )
        self._session.add(assistant_msg)

        # 8. Update conversation counters
        conversation.message_count = (conversation.message_count or 0) + 2
        conversation.total_tokens = (
            conversation.total_tokens or 0
        ) + llm_response.usage.total_tokens

        await self._session.commit()
        await self._session.refresh(user_msg)
        await self._session.refresh(assistant_msg)

        # 9. Auto-generate title on first message
        if is_first_message and self._auto_title_enabled:
            from callobuzz_rag.memory.auto_title import generate_title

            answered = len(self.llm_responses)
            await generate_title(
                self._session,
                self._llm,
                conversation_id,
                content,
                on_response=self.llm_responses.append,
            )
            # generate_title only flushes; without a commit the title was
            # rolled back when the request's session closed.
            if len(self.llm_responses) > answered:
                await self._session.commit()

        return user_msg, assistant_msg, retrieval_context

    async def _get_conversation(self, conversation_id: UUID, corpus_id: UUID) -> Conversation:
        """Load conversation, raising ConversationNotFoundError if missing."""
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.corpus_id == corpus_id,
                Conversation.kind == "assistant",
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ConversationNotFoundError(
                f"Conversation {conversation_id} not found in corpus {corpus_id}"
            )
        return conversation

    async def _retrieve_context(self, query: str, corpus_id: UUID) -> "list[RetrievalResult]":
        """Run hybrid search for relevant chunks."""
        from callobuzz_rag.retrieval.models import SearchFilters

        filters = SearchFilters(corpus_id=str(corpus_id))
        return await self._searcher.search(query, filters, top_k_dense=5)  # type: ignore[union-attr]

    @staticmethod
    def _format_retrieval_context(results: "list[RetrievalResult]") -> str:
        """Format retrieval results into a context block for the LLM."""
        parts: list[str] = []
        for i, r in enumerate(results, 1):
            parts.append(f"[{i}] {r.text}")
        return "\n\n".join(parts)

    @staticmethod
    def _build_llm_messages(
        *,
        context_text: str,
        history: list[dict[str, Any]],
        user_content: str,
    ) -> list[LLMMessage]:
        """Build the LLM message list: system + context + history + user."""
        messages: list[LLMMessage] = []

        # System prompt
        system_content = _SYSTEM_PROMPT
        if context_text:
            system_content += "\n\nRelevant context from the knowledge base:\n" + context_text
        messages.append(LLMMessage(role="system", content=system_content))

        # Conversation history from context builder (skip last — it's the
        # message we're about to send, already included in build_context output)
        for msg_dict in history[:-1]:
            messages.append(LLMMessage(role=msg_dict["role"], content=msg_dict["content"]))

        # Current user message
        messages.append(LLMMessage(role="user", content=user_content))

        return messages
