"""An unknown request field must 422, not vanish.

The defect this pins was invisible by construction. A client sent
``search_mode: 'hybrid'`` on its fallback retrieval; pydantic's default
``extra='ignore'`` dropped it; the engine re-ran the identical dense query,
returned the identical chunks, and the client reported ``searchMode: 'hybrid'`` to
its own caller. Nothing anywhere was an error. It survived months.
"""

import pytest
from pydantic import ValidationError

from callobuzz_rag.api.schemas.query import QueryOptions, QueryRequest


class TestUnknownFieldsAreRejected:
    def test_the_field_that_actually_caused_this(self) -> None:
        # `search_mode` is a top-level field on the wire, not a QueryOption.
        # Before this change QueryRequest(...) accepted it and `hasattr` was
        # False — accepted and gone.
        with pytest.raises(ValidationError, match="search_mode"):
            QueryRequest(query="x", search_mode="hybrid")

    def test_an_unknown_option_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="rerank_mode"):
            QueryOptions(rerank_mode="listwise")

    @pytest.mark.parametrize("typo", ["final_K", "finalk", "top-k"])
    def test_a_near_miss_is_rejected_rather_than_ignored(self, typo: str) -> None:
        """The dangerous case is not a nonsense field, it is a typo of a real
        one: the caller believes it set `final_k` and the engine uses its
        default, so the request looks like it worked."""
        with pytest.raises(ValidationError):
            QueryOptions(**{typo: 5})

    def test_the_error_names_the_offending_field(self) -> None:
        """A 422 that does not say WHICH field is barely better than silence."""
        with pytest.raises(ValidationError) as exc_info:
            QueryOptions(category_weight={"operations": 1.0})  # singular typo

        errors = exc_info.value.errors()
        assert any("category_weight" in str(e.get("loc", "")) for e in errors), errors
        assert any(e["type"] == "extra_forbidden" for e in errors), errors


class TestKnownFieldsStillWork:
    """Strictness is worthless if it also breaks the real contract."""

    def test_a_full_realistic_request_round_trips(self) -> None:
        request = QueryRequest(
            query="packing procedure",
            corpus_id=None,
            options=QueryOptions(
                top_k=20,
                final_k=5,
                category="operations",
                category_weights={"operations": 1.2},
            ),
        )
        assert request.options.final_k == 5
        assert request.options.category_weights == {"operations": 1.2}

    def test_options_default_when_omitted(self) -> None:
        assert QueryRequest(query="x").options.final_k is None


class TestStrictnessAudit:
    """The audit exists so the NEXT silently-dropped field is discoverable."""

    def test_the_query_schemas_are_not_in_the_permissive_list(self) -> None:
        from callobuzz_rag.api.app import create_app
        from callobuzz_rag.api.schema_strictness import (
            audit_request_schema_strictness,
        )

        permissive = audit_request_schema_strictness(create_app())

        assert "QueryRequest" not in permissive
        assert isinstance(permissive, list)

    def test_no_request_body_is_permissive(self) -> None:
        """Every request body (and every model nested in one) rejects unknown fields.

        A new request model that inherits plain `BaseModel` fails this: inherit
        `core.schemas.StrictRequest`.
        """
        from callobuzz_rag.api.app import create_app
        from callobuzz_rag.api.schema_strictness import (
            _request_body_models,
            audit_request_schema_strictness,
        )

        app = create_app()
        assert len(_request_body_models(app)) > 40, "the audit is not seeing the routes"
        assert audit_request_schema_strictness(app) == []

    def test_the_audit_walks_every_included_router(self) -> None:
        """FastAPI 0.14x wraps each `include_router` in an object with no `endpoint`.

        The audit iterated `app.routes` and skipped anything without one, so it
        inspected nothing and logged nothing, silently. It must see through the
        wrappers, however deep they nest.
        """
        from callobuzz_rag.api.app import create_app
        from callobuzz_rag.api.schema_strictness import iter_endpoints

        endpoints = list(iter_endpoints(create_app()))

        assert len(endpoints) > 100
        names = {endpoint.__name__ for endpoint in endpoints}
        assert {"create_corpus", "execute_query", "import_golden_pairs"} <= names

    def test_nested_routers_are_found(self) -> None:
        from fastapi import APIRouter, FastAPI
        from pydantic import BaseModel, ConfigDict

        from callobuzz_rag.api.schema_strictness import audit_request_schema_strictness

        class Loose(BaseModel):
            a: int

        class Tight(BaseModel):
            model_config = ConfigDict(extra="forbid")
            b: int

        inner = APIRouter(prefix="/inner")
        outer = APIRouter(prefix="/outer")

        @inner.post("/loose")
        async def loose(body: Loose) -> None: ...

        @outer.post("/tight")
        async def tight(body: Tight) -> None: ...

        outer.include_router(inner)
        app = FastAPI()
        app.include_router(outer)

        assert audit_request_schema_strictness(app) == ["Loose"]

    def test_a_permissive_model_is_logged_by_name(self, caplog) -> None:
        import logging

        from fastapi import FastAPI
        from pydantic import BaseModel

        from callobuzz_rag.api.schema_strictness import audit_request_schema_strictness
        from callobuzz_rag.core.schemas import StrictRequest

        class Loose(BaseModel):
            a: int

        class Strict(StrictRequest):
            b: int

        app = FastAPI()

        @app.post("/loose")
        async def loose(body: Loose) -> None: ...

        @app.post("/strict")
        async def strict(body: Strict) -> None: ...

        with caplog.at_level(logging.INFO, logger="callobuzz_rag.api.schema_strictness"):
            permissive = audit_request_schema_strictness(app)

        assert permissive == ["Loose"]
        warning = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning) == 1 and "Loose" in warning[0].getMessage()
        info = [r for r in caplog.records if r.levelno == logging.INFO]
        assert len(info) == 1 and "Strict" in info[0].getMessage()

    def test_a_permissive_model_nested_in_a_body_is_found(self) -> None:
        from fastapi import FastAPI
        from pydantic import BaseModel

        from callobuzz_rag.api.schema_strictness import audit_request_schema_strictness
        from callobuzz_rag.core.schemas import StrictRequest

        class Item(BaseModel):
            a: int

        class Outer(StrictRequest):
            items: list[Item]

        app = FastAPI()

        @app.post("/outer")
        async def outer(body: Outer) -> None: ...

        assert audit_request_schema_strictness(app) == ["Item"]
