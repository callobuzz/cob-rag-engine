"""Unit tests for Corpus model Phase 4 column extensions."""

from sqlalchemy import inspect as sa_inspect

from callobuzz_rag.db.models.corpus import Corpus


class TestCorpusModelColumns:
    def _get_column(self, name: str):
        table = Corpus.__table__
        assert name in table.columns, f"Column '{name}' not found on Corpus model"
        return table.columns[name]

    def test_corpus_has_api_key_hash_field(self) -> None:
        col = self._get_column("api_key_hash")
        assert col is not None
        assert not col.nullable

    def test_corpus_has_qdrant_collection_field(self) -> None:
        col = self._get_column("qdrant_collection")
        assert col is not None
        assert not col.nullable

    def test_corpus_has_llm_config_field(self) -> None:
        col = self._get_column("llm_config")
        assert col is not None
        assert col.nullable

    def test_corpus_has_prompt_config_field(self) -> None:
        col = self._get_column("prompt_config")
        assert col is not None
        assert col.nullable

    def test_corpus_llm_config_accepts_dict(self) -> None:
        col = self._get_column("llm_config")
        col_type = str(col.type)
        assert "JSONB" in col_type or "JSON" in col_type

    def test_corpus_prompt_config_accepts_dict(self) -> None:
        col = self._get_column("prompt_config")
        col_type = str(col.type)
        assert "JSONB" in col_type or "JSON" in col_type

    def test_corpus_llm_config_default_none(self) -> None:
        col = self._get_column("llm_config")
        assert col.nullable is True

    def test_corpus_qdrant_collection_default(self) -> None:
        col = self._get_column("qdrant_collection")
        assert col.server_default is not None
        default_text = str(col.server_default.arg)
        assert "chunks" in default_text

    def test_corpus_api_key_hash_default(self) -> None:
        col = self._get_column("api_key_hash")
        assert col.server_default is not None
        default_text = str(col.server_default.arg)
        assert default_text == "''"

    def test_corpus_backward_compatible(self) -> None:
        table = Corpus.__table__
        for col_name in ["id", "name", "description", "created_at"]:
            assert col_name in table.columns, f"Original column '{col_name}' missing"


class TestCorpusModelRelationships:
    def test_documents_relationship_exists(self) -> None:
        mapper = sa_inspect(Corpus)
        assert "documents" in mapper.relationships

    def test_chunks_relationship_exists(self) -> None:
        mapper = sa_inspect(Corpus)
        assert "chunks" in mapper.relationships


class TestCorpusModelColumnTypes:
    def test_api_key_hash_max_length(self) -> None:
        col = Corpus.__table__.columns["api_key_hash"]
        assert hasattr(col.type, "length")
        assert col.type.length == 255

    def test_qdrant_collection_max_length(self) -> None:
        col = Corpus.__table__.columns["qdrant_collection"]
        assert hasattr(col.type, "length")
        assert col.type.length == 255
