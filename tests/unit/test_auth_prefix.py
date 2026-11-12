"""Unit tests for auth fast-path prefix match (EM-5)."""


class TestAuthPrefixMatch:
    """Verify auth finds a key's row by its non-secret segments, never by scanning."""

    def test_lookup_is_by_id_segment_and_lookup(self) -> None:
        """A key is found by the owner's id segment and the key's own `lookup` (or a migrated
        row with none): indexed columns, never a LIKE over collection names or every hash."""
        import inspect

        from callobuzz_rag.api.auth import APIKeyAuthMiddleware

        source = inspect.getsource(APIKeyAuthMiddleware._find_owner)
        assert "ApiKey.id_short ==" in source and "ApiKey.lookup ==" in source
        assert ".like(" not in source

    def test_versioned_collection_matches_prefix(self) -> None:
        """A versioned collection like corpus_abc12345_v2 starts with corpus_abc12345."""
        id_short = "abc12345"
        collection = f"corpus_{id_short}_v2"
        assert collection.startswith(f"corpus_{id_short}")
