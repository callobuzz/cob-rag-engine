"""Tests for retrieval_config column on Corpus model."""

from callobuzz_rag.db.models.corpus import Corpus


def test_corpus_has_retrieval_config_column():
    """Verify the corpora table has a retrieval_config JSONB column."""
    columns = {c.name for c in Corpus.__table__.columns}
    assert "retrieval_config" in columns


def test_corpus_retrieval_config_nullable():
    """retrieval_config should be nullable (None = use engine defaults)."""
    col = Corpus.__table__.columns["retrieval_config"]
    assert col.nullable is True


def test_corpus_instantiation_with_retrieval_config():
    """Corpus can be instantiated with retrieval_config dict."""
    corpus = Corpus(
        name="test",
        retrieval_config={"top_k": 10, "mmr_enabled": True},
    )
    assert corpus.retrieval_config == {"top_k": 10, "mmr_enabled": True}


def test_corpus_instantiation_without_retrieval_config():
    """Corpus without retrieval_config defaults to None."""
    corpus = Corpus(name="test")
    assert corpus.retrieval_config is None
