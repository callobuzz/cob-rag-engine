"""End-to-end conversation lifecycle test.

Create conversation -> send messages -> verify DB state -> list messages -> delete.
Requires Docker (PostgreSQL via testcontainers).
"""

import pytest
from sqlalchemy import select

from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.message import Message
from tests.integration.conftest import new_tenant_id


@pytest.mark.integration
async def test_conversation_lifecycle(db_session):
    """Test full conversation lifecycle: create -> message -> list -> delete."""
    corpus = Corpus(
        name="Conv Test Corpus",
        description="For conversation E2E testing",
        tenant_id=await new_tenant_id(db_session),
    )
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    # Create conversation
    conv = Conversation(
        corpus_id=corpus.id,
        title="Test Conversation",
        created_by="test-user",
    )
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)
    assert conv.id is not None
    assert conv.message_count == 0

    # Add messages
    user_msg = Message(conversation_id=conv.id, role="user", content="What is RAG?")
    db_session.add(user_msg)
    await db_session.flush()

    assistant_msg = Message(
        conversation_id=conv.id,
        role="assistant",
        content="RAG stands for Retrieval-Augmented Generation.",
        token_count=12,
    )
    db_session.add(assistant_msg)
    conv.message_count = 2
    conv.total_tokens = 20
    await db_session.commit()

    # Verify messages persisted
    result = await db_session.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
    )
    messages = result.scalars().all()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"

    # Verify conversation counters
    await db_session.refresh(conv)
    assert conv.message_count == 2
    assert conv.total_tokens == 20

    # Delete conversation (messages cascade)
    await db_session.delete(conv)
    await db_session.commit()
    remaining = await db_session.execute(select(Message).where(Message.conversation_id == conv.id))
    assert len(remaining.scalars().all()) == 0


@pytest.mark.integration
async def test_context_builder_with_real_db(db_session):
    """Test ConversationContextBuilder reads real messages from DB."""
    from callobuzz_rag.memory.context_builder import ConversationContextBuilder

    corpus = Corpus(
        name="Context Test Corpus", description="Test", tenant_id=await new_tenant_id(db_session)
    )
    db_session.add(corpus)
    await db_session.commit()
    await db_session.refresh(corpus)

    conv = Conversation(corpus_id=corpus.id, title="Context test", message_count=0)
    db_session.add(conv)
    await db_session.commit()
    await db_session.refresh(conv)

    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        msg = Message(conversation_id=conv.id, role=role, content=f"Message {i}")
        db_session.add(msg)
    conv.message_count = 5
    await db_session.commit()

    builder = ConversationContextBuilder(db_session, max_messages=3, token_budget=500)
    context = await builder.build_context(conv.id)
    assert len(context) <= 3
    assert all(isinstance(c, dict) for c in context)
