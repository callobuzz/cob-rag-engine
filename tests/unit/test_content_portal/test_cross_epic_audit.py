"""Cross-epic audit: validates consistency across all 11 Content Portal epics.

Structural and import-chain tests that verify the portal codebase is wired
correctly, exceptions follow the hierarchy, schemas are consistent, state
machines match the spec, and services use provider ABCs.

No Docker or running services required — these are pure structural assertions
against the real source code.
"""

import importlib
import inspect
import uuid

import pytest
from pydantic import ValidationError

# ============================================================================
# 1. Router wiring
# ============================================================================


class TestRouterWiring:
    """Validate all portal routers are properly wired."""

    def test_portal_router_has_all_subrouters(self):
        """portal_router should include exactly 7 sub-routers."""
        tags = {tag for _, _, op in _portal_operations() for tag in op.get("tags", [])}

        expected_tags = {
            "portal-workspaces",
            "portal-staging",
            "portal-rules",
            "portal-chunks",
            "portal-review",
            "portal-chat",
            "portal-post-ingest",
        }
        assert expected_tags.issubset(tags), f"Missing tags: {expected_tags - tags}"

    def test_portal_router_prefix(self):
        """The portal_router must have prefix /api/v1/portal."""
        from callobuzz_rag.content_portal.routes import portal_router

        assert portal_router.prefix == "/api/v1/portal"

    def test_workspace_crud_routes_exist(self):
        """Workspace router should have POST, GET (list), GET (detail),
        PATCH, DELETE endpoints."""
        paths_methods = {(path, method) for path, method, _ in _portal_operations()}

        pfx = "/api/v1/portal"
        # POST /workspaces (create)
        assert (f"{pfx}/workspaces", "POST") in paths_methods
        # GET /workspaces (list)
        assert (f"{pfx}/workspaces", "GET") in paths_methods
        # GET /workspaces/{workspace_id} (detail)
        assert (f"{pfx}/workspaces/{{workspace_id}}", "GET") in paths_methods
        # PATCH /workspaces/{workspace_id} (update)
        assert (f"{pfx}/workspaces/{{workspace_id}}", "PATCH") in paths_methods
        # DELETE /workspaces/{workspace_id} (delete)
        assert (f"{pfx}/workspaces/{{workspace_id}}", "DELETE") in paths_methods

    def test_review_workflow_routes_exist(self):
        """Review sub-router should have submit, approve, reject, ingest,
        audit-trail endpoints."""
        paths = {path for path, _, _ in _portal_operations()}

        pfx = "/api/v1/portal"
        expected_paths = {
            f"{pfx}/workspaces/{{workspace_id}}/submit",
            f"{pfx}/workspaces/{{workspace_id}}/approve",
            f"{pfx}/workspaces/{{workspace_id}}/reject",
            f"{pfx}/workspaces/{{workspace_id}}/ingest",
            f"{pfx}/workspaces/{{workspace_id}}/audit-trail",
        }
        assert expected_paths.issubset(paths), f"Missing review paths: {expected_paths - paths}"

    def test_ai_chat_routes_exist(self):
        """AI chat sub-router should have chat, chat/history, and
        chat/execute endpoints."""
        paths = {path for path, _, _ in _portal_operations()}

        pfx = "/api/v1/portal"
        expected = {
            f"{pfx}/workspaces/{{workspace_id}}/chat",
            f"{pfx}/workspaces/{{workspace_id}}/chat/history",
            f"{pfx}/workspaces/{{workspace_id}}/chat/execute",
        }
        assert expected.issubset(paths), f"Missing chat paths: {expected - paths}"


# ============================================================================
# 2. Exception hierarchy
# ============================================================================


class TestExceptionHierarchy:
    """All portal exceptions inherit correctly."""

    def test_content_portal_error_inherits_from_rag_kernel_error(self):
        from callobuzz_rag.core.exceptions import (
            ContentPortalError,
            RAGKernelError,
        )

        assert issubclass(ContentPortalError, RAGKernelError)

    def test_all_portal_exceptions_inherit_content_portal_error(self):
        """Every portal-defined exception must inherit ContentPortalError."""
        from callobuzz_rag.core.exceptions import ContentPortalError

        portal_exception_classes = _collect_portal_exceptions()
        for cls in portal_exception_classes:
            assert issubclass(cls, ContentPortalError), (
                f"{cls.__name__} does not inherit ContentPortalError"
            )

    def test_review_workflow_exceptions_map_to_correct_http_codes(self):
        """Review exceptions should map to 403, 409, 422, 404."""
        from callobuzz_rag.content_portal.routes.review import (
            _handle_workflow_error,
        )
        from callobuzz_rag.content_portal.services.review_workflow import (
            ChunkValidationError,
            InvalidTransitionError,
            MakerCheckerViolationError,
            ReviewWorkflowError,
        )

        assert _handle_workflow_error(MakerCheckerViolationError("test")).status_code == 403
        assert _handle_workflow_error(ChunkValidationError("test")).status_code == 422
        assert _handle_workflow_error(InvalidTransitionError("test")).status_code == 409
        assert _handle_workflow_error(ReviewWorkflowError("test")).status_code == 404

    def test_ai_chat_exceptions_map_to_correct_http_codes(self):
        """Chat exceptions should map to 404, 422, 500."""
        from callobuzz_rag.content_portal.routes.ai_chat import (
            _handle_chat_error,
        )
        from callobuzz_rag.content_portal.services.ai_chat_service import (
            ActionExecutionError,
            AIChatError,
            ConversationNotFoundError,
        )

        assert _handle_chat_error(ConversationNotFoundError("x")).status_code == 404
        assert _handle_chat_error(ActionExecutionError("x")).status_code == 422
        assert _handle_chat_error(AIChatError("x")).status_code == 500

    def test_post_ingest_exceptions_map_to_correct_http_codes(self):
        """Post-ingest exceptions should map to 404, 409, 422, 400."""
        from callobuzz_rag.content_portal.routes.post_ingest import (
            _handle_post_ingest_error,
        )
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            DocumentNotFoundError,
            NoChunksToReviewError,
            PostIngestReviewError,
            ReviewAlreadyExistsError,
        )

        assert _handle_post_ingest_error(DocumentNotFoundError("x")).status_code == 404
        assert _handle_post_ingest_error(ReviewAlreadyExistsError("x")).status_code == 409
        assert _handle_post_ingest_error(NoChunksToReviewError("x")).status_code == 422
        assert _handle_post_ingest_error(PostIngestReviewError("x")).status_code == 400

    def test_review_exists_names_the_existing_workspace(self):
        """409 review_exists carries the open review's id, so a client can open it."""
        import uuid

        from callobuzz_rag.api.errors import APIError
        from callobuzz_rag.content_portal.routes.post_ingest import (
            _handle_post_ingest_error,
        )
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            ReviewAlreadyExistsError,
        )

        ws = uuid.uuid4()
        err = _handle_post_ingest_error(ReviewAlreadyExistsError("x", workspace_id=ws))
        assert isinstance(err, APIError)
        assert (err.status_code, err.code, err.extra) == (
            409,
            "review_exists",
            {"workspace_id": str(ws)},
        )

    def test_review_transitions_are_coded(self):
        """submit / approve / reject / ingest refuse a transition with the catalog code;
        maker-checker has no catalog code and stays a plain 403."""
        from callobuzz_rag.api.errors import APIError
        from callobuzz_rag.content_portal.routes.review import _handle_workflow_error
        from callobuzz_rag.content_portal.services.review_workflow import (
            InvalidTransitionError,
            MakerCheckerViolationError,
        )

        err = _handle_workflow_error(InvalidTransitionError("x"))
        assert isinstance(err, APIError)
        assert (err.status_code, err.code) == (409, "invalid_status_transition")
        own = _handle_workflow_error(MakerCheckerViolationError("x"))
        assert own.status_code == 403
        assert not isinstance(own, APIError)


# ============================================================================
# 3. Schema validation consistency
# ============================================================================


class TestSchemaConsistency:
    """Validate schema patterns are consistent."""

    def test_user_fields_have_correct_constraints(self):
        """All request schemas with a 'user' field should have
        min_length=1 and max_length=255."""
        schemas = _collect_request_schemas()
        for schema_cls in schemas:
            fields = schema_cls.model_fields
            if "user" not in fields:
                continue
            info = fields["user"]
            # Pydantic v2: constraints stored as annotated metadata
            min_len = _get_field_constraint(info, "min_length")
            max_len = _get_field_constraint(info, "max_length")
            assert min_len == 1, f"{schema_cls.__name__}.user min_length={min_len}, expected 1"
            assert max_len == 255, f"{schema_cls.__name__}.user max_length={max_len}, expected 255"

    def test_response_uuid_fields_use_uuid_type(self):
        """Response schemas with 'workspace_id', 'corpus_id', or
        'document_version_id' should use uuid.UUID type."""
        from callobuzz_rag.content_portal.schemas import ai_chat, post_ingest, review, workspaces

        response_schemas = [
            workspaces.WorkspaceResponse,
            review.SubmitForReviewResponse,
            review.WorkspaceStatusResponse,
            review.IngestWorkspaceResponse,
            ai_chat.ChatMessageResponse,
            post_ingest.CreatePostIngestReviewResponse,
            post_ingest.FinalizeReviewResponse,
            post_ingest.PostIngestReviewStatus,
        ]
        uuid_field_names = {
            "id",
            "workspace_id",
            "corpus_id",
            "document_version_id",
            "message_id",
            "conversation_id",
        }

        for schema_cls in response_schemas:
            for name, field_info in schema_cls.model_fields.items():
                if name not in uuid_field_names:
                    continue
                annotation = field_info.annotation
                # Handle Optional[UUID] = UUID | None
                actual_type = _unwrap_optional(annotation)
                assert actual_type is uuid.UUID, (
                    f"{schema_cls.__name__}.{name} type is {annotation}, expected uuid.UUID"
                )

    def test_workspace_lock_schema_user_constraint(self):
        """WorkspaceLock.user should have min_length=1, max_length=255."""
        from callobuzz_rag.content_portal.schemas.workspaces import WorkspaceLock

        info = WorkspaceLock.model_fields["user"]
        assert _get_field_constraint(info, "min_length") == 1
        assert _get_field_constraint(info, "max_length") == 255

    def test_submit_for_review_response_has_chunk_counts(self):
        """SubmitForReviewResponse must include total, approved, rejected,
        and draft chunk counts."""
        from callobuzz_rag.content_portal.schemas.review import (
            SubmitForReviewResponse,
        )

        field_names = set(SubmitForReviewResponse.model_fields.keys())
        expected = {
            "workspace_id",
            "status",
            "message",
            "total_chunks",
            "approved_chunks",
            "rejected_chunks",
            "draft_chunks",
        }
        assert expected.issubset(field_names), f"Missing fields: {expected - field_names}"

    def test_intelligence_request_validates_analysis_types(self):
        """IntelligenceRequest should reject unknown analysis_types."""
        from callobuzz_rag.content_portal.schemas.intelligence import (
            IntelligenceRequest,
        )

        # Valid
        req = IntelligenceRequest(analysis_types=["duplicates", "gaps"])
        assert req.analysis_types == ["duplicates", "gaps"]

        # Invalid
        with pytest.raises(ValidationError):
            IntelligenceRequest(analysis_types=["unknown_type"])


# ============================================================================
# 4. State machine consistency
# ============================================================================


class TestStateMachineConsistency:
    """State machine and status values are correct."""

    def test_valid_transitions_matches_spec(self):
        """VALID_TRANSITIONS should allow:
        draft -> in_review
        in_review -> approved | draft (reject)
        approved -> ingested
        ingested -> (none)
        archived -> (none)
        """
        from callobuzz_rag.content_portal.services.review_workflow import (
            VALID_TRANSITIONS,
        )

        assert set(VALID_TRANSITIONS["draft"]) == {"in_review"}
        assert set(VALID_TRANSITIONS["in_review"]) == {"approved", "draft"}
        assert set(VALID_TRANSITIONS["approved"]) == {"ingested"}
        assert VALID_TRANSITIONS["ingested"] == []
        assert VALID_TRANSITIONS["archived"] == []

    def test_workspace_status_enum_covers_all_transition_keys(self):
        """Every key in VALID_TRANSITIONS should be a valid WorkspaceStatus."""
        from callobuzz_rag.content_portal.models.workspace import (
            WorkspaceStatus,
        )
        from callobuzz_rag.content_portal.services.review_workflow import (
            VALID_TRANSITIONS,
        )

        status_values = {s.value for s in WorkspaceStatus}
        transition_keys = set(VALID_TRANSITIONS.keys())
        assert transition_keys.issubset(status_values), (
            f"Transition keys not in WorkspaceStatus: {transition_keys - status_values}"
        )

    def test_staged_chunk_status_enum_values(self):
        """StagedChunkStatus should have exactly draft, approved, rejected."""
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunkStatus,
        )

        values = {s.value for s in StagedChunkStatus}
        assert values == {"draft", "approved", "rejected"}

    def test_staged_chunk_source_enum_values(self):
        """StagedChunkSource should have ai_generated, manual,
        from_ingestion."""
        from callobuzz_rag.content_portal.models.staged_chunk import (
            StagedChunkSource,
        )

        values = {s.value for s in StagedChunkSource}
        assert values == {"ai_generated", "manual", "from_ingestion"}

    def test_workspace_status_enum_values(self):
        """WorkspaceStatus should have draft, in_review, approved,
        ingested, archived."""
        from callobuzz_rag.content_portal.models.workspace import (
            WorkspaceStatus,
        )

        values = {s.value for s in WorkspaceStatus}
        assert values == {
            "draft",
            "in_review",
            "approved",
            "ingested",
            "archived",
        }


# ============================================================================
# 5. Import chain validation
# ============================================================================


class TestImportChain:
    """No circular imports, deferred imports resolve."""

    def test_portal_routes_init_imports_cleanly(self):
        """The routes __init__.py should import without errors."""
        mod = importlib.import_module("callobuzz_rag.content_portal.routes")
        assert hasattr(mod, "portal_router")

    def test_all_portal_models_importable(self):
        """All portal model modules should import cleanly."""
        modules = [
            "callobuzz_rag.content_portal.models.workspace",
            "callobuzz_rag.content_portal.models.staged_content",
            "callobuzz_rag.content_portal.models.staged_document",
            "callobuzz_rag.content_portal.models.staged_chunk",
            "callobuzz_rag.content_portal.models.content_rule",
            "callobuzz_rag.content_portal.models.workspace_conversation",
        ]
        for module_path in modules:
            mod = importlib.import_module(module_path)
            assert mod is not None, f"Failed to import {module_path}"

    def test_all_portal_schemas_importable(self):
        """All portal schema modules should import cleanly."""
        modules = [
            "callobuzz_rag.content_portal.schemas.workspaces",
            "callobuzz_rag.content_portal.schemas.staging",
            "callobuzz_rag.content_portal.schemas.rules",
            "callobuzz_rag.content_portal.schemas.chunks",
            "callobuzz_rag.content_portal.schemas.review",
            "callobuzz_rag.content_portal.schemas.ai_chat",
            "callobuzz_rag.content_portal.schemas.post_ingest",
            "callobuzz_rag.content_portal.schemas.intelligence",
        ]
        for module_path in modules:
            mod = importlib.import_module(module_path)
            assert mod is not None, f"Failed to import {module_path}"

    def test_all_portal_services_importable(self):
        """All portal service modules should import cleanly."""
        modules = [
            "callobuzz_rag.content_portal.services.workspace_service",
            "callobuzz_rag.content_portal.services.staging_service",
            "callobuzz_rag.content_portal.services.content_router",
            "callobuzz_rag.content_portal.services.ai_transformer",
            "callobuzz_rag.content_portal.services.corpus_intelligence",
            "callobuzz_rag.content_portal.services.chunk_editor",
            "callobuzz_rag.content_portal.services.review_workflow",
            "callobuzz_rag.content_portal.services.ai_chat_service",
            "callobuzz_rag.content_portal.services.post_ingest_review",
        ]
        for module_path in modules:
            mod = importlib.import_module(module_path)
            assert mod is not None, f"Failed to import {module_path}"

    def test_portal_config_importable(self):
        """Portal config module should import and expose key functions."""
        from callobuzz_rag.content_portal.config import (
            get_portal_ai_model,
            is_portal_enabled,
            resolve_portal_config,
        )

        assert callable(resolve_portal_config)
        assert callable(get_portal_ai_model)
        assert callable(is_portal_enabled)


# ============================================================================
# 6. Provider pattern compliance
# ============================================================================


class TestProviderPattern:
    """Services use ABCs, not concrete classes."""

    def test_ai_chat_service_takes_llm_provider_abc(self):
        """AIChatService.__init__ should accept LLMProvider (ABC)."""
        from callobuzz_rag.content_portal.services.ai_chat_service import (
            AIChatService,
        )

        sig = inspect.signature(AIChatService.__init__)
        params = sig.parameters

        assert "llm" in params, "AIChatService.__init__ missing 'llm' param"

        # With `from __future__ import annotations`, annotation may be a string
        ann = params["llm"].annotation
        ann_name = ann if isinstance(ann, str) else getattr(ann, "__name__", str(ann))
        assert "LLMProvider" in ann_name, (
            f"AIChatService.llm annotation is {ann}, expected LLMProvider"
        )

    def test_post_ingest_service_takes_provider_abcs(self):
        """PostIngestReviewService.__init__ should accept
        VectorStorageProvider and EmbeddingProvider ABCs."""
        from callobuzz_rag.content_portal.services.post_ingest_review import (
            PostIngestReviewService,
        )

        sig = inspect.signature(PostIngestReviewService.__init__)
        params = sig.parameters

        assert "vector_store" in params
        assert "embedder" in params

        # The annotations use TYPE_CHECKING imports, so verify the string
        # annotation or the actual resolved type.
        vector_ann = params["vector_store"].annotation
        embedder_ann = params["embedder"].annotation

        # With `from __future__ import annotations`, these may be strings
        vector_name = (
            vector_ann
            if isinstance(vector_ann, str)
            else getattr(vector_ann, "__name__", str(vector_ann))
        )
        embedder_name = (
            embedder_ann
            if isinstance(embedder_ann, str)
            else getattr(embedder_ann, "__name__", str(embedder_ann))
        )

        assert "VectorStorageProvider" in vector_name, (
            f"vector_store annotation is {vector_ann}, expected VectorStorageProvider"
        )
        assert "EmbeddingProvider" in embedder_name, (
            f"embedder annotation is {embedder_ann}, expected EmbeddingProvider"
        )

    def test_corpus_intelligence_service_takes_abcs(self):
        """CorpusIntelligenceService.__init__ should accept
        QueryOrchestrator and LLMProvider."""
        from callobuzz_rag.content_portal.services.corpus_intelligence import (
            CorpusIntelligenceService,
        )

        sig = inspect.signature(CorpusIntelligenceService.__init__)
        params = sig.parameters

        assert "orchestrator" in params
        assert "llm" in params

        # With `from __future__ import annotations`, annotations are strings
        orch_ann = params["orchestrator"].annotation
        llm_ann = params["llm"].annotation

        orch_name = (
            orch_ann if isinstance(orch_ann, str) else getattr(orch_ann, "__name__", str(orch_ann))
        )
        llm_name = (
            llm_ann if isinstance(llm_ann, str) else getattr(llm_ann, "__name__", str(llm_ann))
        )

        assert "QueryOrchestrator" in orch_name, f"orchestrator annotation is {orch_ann}"
        assert "LLMProvider" in llm_name, f"llm annotation is {llm_ann}"


# ============================================================================
# Helpers
# ============================================================================


def _collect_portal_exceptions() -> list[type]:
    """Collect all exception classes defined in portal service modules."""
    from callobuzz_rag.core.exceptions import ContentPortalError

    service_modules = [
        "callobuzz_rag.content_portal.services.review_workflow",
        "callobuzz_rag.content_portal.services.ai_chat_service",
        "callobuzz_rag.content_portal.services.post_ingest_review",
        "callobuzz_rag.content_portal.services.corpus_intelligence",
    ]

    exceptions: list[type] = []
    for mod_path in service_modules:
        mod = importlib.import_module(mod_path)
        for _name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, Exception)
                and obj is not Exception
                and obj is not ContentPortalError
                and obj.__module__ == mod.__name__
            ):
                exceptions.append(obj)

    return exceptions


def _collect_request_schemas() -> list[type]:
    """Collect all request Pydantic schemas from portal schema modules."""
    from pydantic import BaseModel

    schema_modules = [
        "callobuzz_rag.content_portal.schemas.workspaces",
        "callobuzz_rag.content_portal.schemas.review",
        "callobuzz_rag.content_portal.schemas.ai_chat",
        "callobuzz_rag.content_portal.schemas.post_ingest",
        "callobuzz_rag.content_portal.schemas.chunks",
    ]

    schemas: list[type] = []
    for mod_path in schema_modules:
        mod = importlib.import_module(mod_path)
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj is not BaseModel
                and obj.__module__ == mod.__name__
                and ("Request" in name or "Lock" in name)
            ):
                schemas.append(obj)
    return schemas


def _get_field_constraint(field_info: object, constraint_name: str) -> object:
    """Extract a Pydantic v2 field constraint (min_length, max_length, etc.)."""
    # Pydantic v2 stores constraints in metadata as annotated types
    for meta in getattr(field_info, "metadata", []):
        val = getattr(meta, constraint_name, None)
        if val is not None:
            return val
    return None


def _portal_operations() -> list[tuple[str, str, dict]]:
    """(path, METHOD, operation) for every route the portal router serves.

    Read from the generated OpenAPI spec rather than `portal_router.routes`:
    newer FastAPI keeps included routers nested instead of flattening their
    routes, so walking `.routes` directly finds nothing.
    """
    from fastapi import FastAPI

    from callobuzz_rag.content_portal.routes import portal_router

    app = FastAPI()
    app.include_router(portal_router)
    return [
        (path, method.upper(), op)
        for path, item in app.openapi()["paths"].items()
        for method, op in item.items()
    ]


def _unwrap_optional(annotation: object) -> type:
    """Unwrap Optional[X] (i.e. X | None) to get X."""
    import types
    import typing

    # typing.get_origin, not `__origin__`: before Python 3.14 a PEP 604 `X | None`
    # is a types.UnionType, which has no `__origin__` attribute at all.
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation  # type: ignore[return-value]
