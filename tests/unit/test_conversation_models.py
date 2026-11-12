"""Tests for Conversation and Message DB models."""

from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message


class TestConversationModel:
    def test_has_required_fields(self):
        column_names = {c.name for c in Conversation.__table__.columns}
        expected = {
            "id",
            "corpus_id",
            "title",
            "created_by",
            "metadata",
            "message_count",
            "total_tokens",
            "summary",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(column_names)

    def test_tablename(self):
        assert Conversation.__tablename__ == "conversations"

    def test_field_assignment(self):
        conv = Conversation()
        conv.title = "Test conversation"
        conv.created_by = "user-123"
        conv.metadata_ = {"key": "value"}
        conv.message_count = 0
        conv.total_tokens = 0
        assert conv.title == "Test conversation"
        assert conv.created_by == "user-123"
        assert conv.message_count == 0

    def test_metadata_is_jsonb(self):
        conv = Conversation()
        conv.metadata_ = {"key": "value", "nested": {"a": 1}}
        assert conv.metadata_["key"] == "value"


class TestMessageModel:
    def test_has_required_fields(self):
        column_names = {c.name for c in Message.__table__.columns}
        expected = {
            "id",
            "conversation_id",
            "role",
            "content",
            "token_count",
            "retrieval_context",
            "agent_log",
            "created_at",
        }
        assert expected.issubset(column_names)

    def test_tablename(self):
        assert Message.__tablename__ == "messages"

    def test_field_assignment(self):
        msg = Message()
        msg.role = "user"
        msg.content = "Hello"
        msg.token_count = 5
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_role_values(self):
        for role in ("user", "assistant", "system"):
            msg = Message()
            msg.role = role
            assert msg.role == role

    def test_retrieval_context_is_jsonb(self):
        msg = Message()
        msg.retrieval_context = {"chunks": [{"id": "c1", "score": 0.9}]}
        assert msg.retrieval_context["chunks"][0]["score"] == 0.9


class TestModelRegistration:
    def test_conversation_in_init(self):
        from callobuzz_rag.db import models

        assert hasattr(models, "Conversation")

    def test_message_in_init(self):
        from callobuzz_rag.db import models

        assert hasattr(models, "Message")
