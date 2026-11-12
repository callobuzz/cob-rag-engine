"""Unit tests for conversation API schemas."""

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

from callobuzz_rag.api.schemas.conversations import (
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageListResponse,
    MessageResponse,
)


class TestConversationCreateRequest:
    def test_minimal(self) -> None:
        req = ConversationCreateRequest()
        assert req.title is None
        assert req.created_by is None
        assert req.metadata is None

    def test_full(self) -> None:
        req = ConversationCreateRequest(
            title="Test Chat",
            created_by="user-1",
            metadata={"source": "web"},
        )
        assert req.title == "Test Chat"
        assert req.created_by == "user-1"
        assert req.metadata == {"source": "web"}


class TestConversationUpdateRequest:
    def test_empty(self) -> None:
        req = ConversationUpdateRequest()
        assert req.title is None
        assert req.metadata is None

    def test_partial(self) -> None:
        req = ConversationUpdateRequest(title="Updated Title")
        assert req.title == "Updated Title"
        assert req.metadata is None


class TestConversationResponse:
    def test_from_dict(self) -> None:
        now = datetime.now(UTC)
        resp = ConversationResponse(
            id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            title="Chat",
            created_by="user-1",
            metadata={"key": "val"},
            message_count=5,
            total_tokens=100,
            summary="A chat",
            created_at=now,
            updated_at=now,
        )
        assert resp.title == "Chat"
        assert resp.metadata == {"key": "val"}
        assert resp.message_count == 5

    def test_from_attributes_with_alias(self) -> None:
        """ConversationResponse should read metadata_ from ORM objects."""
        now = datetime.now(UTC)
        obj = MagicMock()
        obj.id = uuid.uuid4()
        obj.corpus_id = uuid.uuid4()
        obj.title = "Test"
        obj.created_by = None
        obj.metadata_ = {"tag": "important"}
        obj.message_count = 0
        obj.total_tokens = 0
        obj.summary = None
        obj.channel = "widget"
        obj.created_at = now
        obj.updated_at = now

        resp = ConversationResponse.model_validate(obj)
        assert resp.metadata == {"tag": "important"}
        assert resp.channel == "widget"


class TestConversationListResponse:
    def test_structure(self) -> None:
        resp = ConversationListResponse(items=[], count=0, offset=0, limit=50)
        assert resp.items == []
        assert resp.count == 0
        assert resp.offset == 0
        assert resp.limit == 50


class TestMessageResponse:
    def test_from_dict(self) -> None:
        now = datetime.now(UTC)
        resp = MessageResponse(
            id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            role="user",
            content="Hello",
            token_count=3,
            retrieval_context=None,
            agent_log=None,
            created_at=now,
        )
        assert resp.role == "user"
        assert resp.content == "Hello"
        assert resp.token_count == 3

    def test_from_attributes(self) -> None:
        now = datetime.now(UTC)
        obj = MagicMock()
        obj.id = uuid.uuid4()
        obj.conversation_id = uuid.uuid4()
        obj.role = "assistant"
        obj.content = "Hi there"
        obj.token_count = 5
        obj.retrieval_context = {"chunks": []}
        obj.agent_log = None
        obj.created_at = now

        resp = MessageResponse.model_validate(obj)
        assert resp.role == "assistant"
        assert resp.retrieval_context == {"chunks": []}


class TestMessageListResponse:
    def test_structure(self) -> None:
        resp = MessageListResponse(items=[], count=0, offset=0, limit=50)
        assert resp.items == []
        assert resp.count == 0
