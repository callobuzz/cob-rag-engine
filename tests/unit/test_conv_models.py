"""C1: settings and the tables the director writes."""

import pytest
from sqlalchemy import ARRAY, Boolean, Float, Integer

from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models import AssistantTurn, Conversation


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(_env_file=None)
        assert s.assistant_label_timeout_ms == 4000
        assert s.assistant_label_cache_ttl_s == 604800
        assert s.assistant_min_retrieval_score is None
        assert s.assistant_session_ttl_hours == 24

    def test_empty_threshold_means_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_ASSISTANT_MIN_RETRIEVAL_SCORE", "")
        assert Settings(_env_file=None).assistant_min_retrieval_score is None

    def test_threshold_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_ASSISTANT_MIN_RETRIEVAL_SCORE", "0.42")
        assert Settings(_env_file=None).assistant_min_retrieval_score == pytest.approx(0.42)

    def test_threshold_bounds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RAG_ASSISTANT_MIN_RETRIEVAL_SCORE", "1.5")
        with pytest.raises(ValueError):
            Settings(_env_file=None)


class TestAssistantTurnTable:
    def test_columns(self) -> None:
        cols = AssistantTurn.__table__.c
        expected = {
            "id",
            "corpus_id",
            "tenant_id",
            "conversation_id",
            "message_id",
            "query_id",
            "channel",
            "assistant_version",
            "label",
            "label_source",
            "label_confidence",
            "language",
            "script",
            "route",
            "topic_id",
            "top_score",
            "gate_passed",
            "claims_removed",
            "claims_flagged",
            "rules_fired",
            "offers",
            "no_answer",
            "question_masked",
            "is_preview",
            "latency_ms",
            "timings",
            "created_at",
        }
        assert expected <= set(cols.keys())
        assert isinstance(cols.rules_fired.type, ARRAY)
        assert isinstance(cols.offers.type, ARRAY)
        assert isinstance(cols.gate_passed.type, Boolean)
        assert isinstance(cols.top_score.type, Float)
        assert isinstance(cols.claims_removed.type, Integer)

    def test_corpus_cascades_and_conversation_sets_null(self) -> None:
        fks = {fk.parent.name: fk.ondelete for fk in AssistantTurn.__table__.foreign_keys}
        assert fks["corpus_id"] == "CASCADE"
        assert fks["conversation_id"] == "SET NULL"
        assert fks["message_id"] == "SET NULL"

    def test_indexes(self) -> None:
        names = {ix.name for ix in AssistantTurn.__table__.indexes}
        assert {
            "ix_assistant_turns_corpus_created",
            "ix_assistant_turns_corpus_label",
            "ix_assistant_turns_no_answer",
        } <= names


class TestConversationColumns:
    def test_new_columns(self) -> None:
        cols = Conversation.__table__.c
        for name in (
            "channel",
            "widget_key_id",
            "widget_session_hash",
            "page_url",
            "utm",
            "language",
            "assistant_state",
            "assistant_version",
        ):
            assert name in cols, name
        fk = next(iter(cols.widget_key_id.foreign_keys))
        assert fk.column.table.name == "widget_keys" and fk.ondelete == "SET NULL"
        assert cols.channel.server_default is not None
