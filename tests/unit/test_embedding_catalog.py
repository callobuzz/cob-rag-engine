"""Unit tests for embedding catalog (EMBEDDING_CATALOG)."""

from callobuzz_rag.core.model_catalog import (
    EMBEDDING_CATALOG,
    get_embedding_catalog_entry,
    get_embedding_entry_by_id,
)


class TestEmbeddingCatalog:
    def test_catalog_not_empty(self) -> None:
        assert len(EMBEDDING_CATALOG) > 0

    def test_all_entries_have_required_fields(self) -> None:
        for entry in EMBEDDING_CATALOG:
            assert "id" in entry
            assert "name" in entry
            assert "provider" in entry
            assert "model" in entry
            assert "dimensions" in entry
            assert entry["dimensions"] > 0

    def test_lookup_bge_m3_local(self) -> None:
        entry = get_embedding_catalog_entry("BAAI/bge-m3", provider="local")
        assert entry is not None
        assert entry["provider"] == "local"
        assert entry["dimensions"] == 1024

    def test_lookup_bge_m3_tei(self) -> None:
        entry = get_embedding_catalog_entry("BAAI/bge-m3", provider="tei")
        assert entry is not None
        assert entry["provider"] == "tei"

    def test_lookup_jina_v3(self) -> None:
        entry = get_embedding_catalog_entry("jina-embeddings-v3", provider="jina")
        assert entry is not None
        assert entry["provider"] == "jina"

    def test_provider_disambiguation(self) -> None:
        """Same model, different providers — must disambiguate."""
        local = get_embedding_catalog_entry("BAAI/bge-m3", provider="local")
        tei = get_embedding_catalog_entry("BAAI/bge-m3", provider="tei")
        assert local is not None
        assert tei is not None
        assert local["id"] != tei["id"]

    def test_ambiguous_lookup_returns_first_match(self) -> None:
        entry = get_embedding_catalog_entry("BAAI/bge-m3")
        assert entry is not None
        assert entry["provider"] == "local"

    def test_unknown_model_returns_none(self) -> None:
        entry = get_embedding_catalog_entry("nonexistent-model")
        assert entry is None

    def test_lookup_by_id(self) -> None:
        entry = get_embedding_entry_by_id("bge-m3-local")
        assert entry is not None
        assert entry["model"] == "BAAI/bge-m3"

    def test_lookup_by_id_unknown(self) -> None:
        entry = get_embedding_entry_by_id("nonexistent-id")
        assert entry is None

    def test_each_id_is_unique(self) -> None:
        ids = [e["id"] for e in EMBEDDING_CATALOG]
        assert len(ids) == len(set(ids))

    def test_multilingual_flag_comes_with_its_source(self) -> None:
        for entry in EMBEDDING_CATALOG:
            if "multilingual" in entry:
                assert isinstance(entry["multilingual"], bool), entry["id"]
                assert entry.get("multilingual_source", "").startswith("https://"), entry["id"]
            else:
                assert "multilingual_source" not in entry, entry["id"]

    def test_hindi_capable_models_are_flagged(self) -> None:
        m3 = get_embedding_catalog_entry("BAAI/bge-m3", provider="tei")
        en = get_embedding_catalog_entry("BAAI/bge-base-en-v1.5", provider="tei")
        assert m3 is not None and m3.get("multilingual") is True
        assert en is not None and en.get("multilingual") is False

    async def test_route_returns_the_flag(self) -> None:
        from callobuzz_rag.api.deps import Scope
        from callobuzz_rag.api.routes.migrations import list_embedding_models

        items = await list_embedding_models(_scope=Scope(is_admin=True))
        assert any(item.get("multilingual") is True for item in items)
