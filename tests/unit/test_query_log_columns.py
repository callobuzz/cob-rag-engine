"""Tests for QueryLog model column additions (corpus_id, session_id)."""

from callobuzz_rag.db.models.query_log import QueryLog


class TestQueryLogColumns:
    def test_has_corpus_id(self) -> None:
        cols = {c.name for c in QueryLog.__table__.columns}
        assert "corpus_id" in cols

    def test_has_session_id(self) -> None:
        cols = {c.name for c in QueryLog.__table__.columns}
        assert "session_id" in cols

    def test_corpus_id_nullable(self) -> None:
        col = QueryLog.__table__.columns["corpus_id"]
        assert col.nullable is True

    def test_session_id_nullable(self) -> None:
        col = QueryLog.__table__.columns["session_id"]
        assert col.nullable is True
