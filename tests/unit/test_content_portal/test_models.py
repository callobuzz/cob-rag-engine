"""Unit tests for Content Portal database models."""

from callobuzz_rag.content_portal.models.content_rule import (
    ContentRule,
    RuleAction,
    RuleType,
)
from callobuzz_rag.content_portal.models.staged_chunk import (
    StagedChunk,
    StagedChunkSource,
    StagedChunkStatus,
)
from callobuzz_rag.content_portal.models.staged_content import (
    AIProcessingStatus,
    SourceType,
    StagedContent,
)
from callobuzz_rag.content_portal.models.staged_document import (
    StagedDocument,
    StagedDocumentStatus,
)
from callobuzz_rag.content_portal.models.workspace import (
    WorkflowMode,
    Workspace,
    WorkspaceStatus,
)
from callobuzz_rag.content_portal.models.workspace_conversation import (
    MessageRole,
    WorkspaceConversation,
    WorkspaceMessage,
)


class TestWorkspaceStatusEnum:
    def test_all_values(self) -> None:
        """WorkspaceStatus has all 5 expected values."""
        assert WorkspaceStatus.DRAFT == "draft"
        assert WorkspaceStatus.IN_REVIEW == "in_review"
        assert WorkspaceStatus.APPROVED == "approved"
        assert WorkspaceStatus.INGESTED == "ingested"
        assert WorkspaceStatus.ARCHIVED == "archived"

    def test_is_str_enum(self) -> None:
        """WorkspaceStatus values are strings."""
        assert isinstance(WorkspaceStatus.DRAFT, str)


class TestWorkflowModeEnum:
    def test_all_values(self) -> None:
        """WorkflowMode has both expected values."""
        assert WorkflowMode.SINGLE_REVIEWER == "single_reviewer"
        assert WorkflowMode.MAKER_CHECKER == "maker_checker"


class TestWorkspaceModel:
    def test_tablename(self) -> None:
        """Workspace uses portal_workspaces table."""
        assert Workspace.__tablename__ == "portal_workspaces"

    def test_has_required_columns(self) -> None:
        """Workspace model has all expected columns."""
        columns = {c.name for c in Workspace.__table__.columns}
        expected = {
            "id",
            "corpus_id",
            "title",
            "status",
            "workflow_mode",
            "created_by",
            "assigned_reviewer",
            "locked_by",
            "locked_at",
            "review_feedback",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_corpus_id_is_indexed(self) -> None:
        """corpus_id column has an index for efficient lookups."""
        col = Workspace.__table__.columns["corpus_id"]
        assert col.index is True


class TestAIProcessingStatusEnum:
    def test_all_values(self) -> None:
        """AIProcessingStatus has all 4 expected values."""
        assert AIProcessingStatus.PENDING == "pending"
        assert AIProcessingStatus.PROCESSING == "processing"
        assert AIProcessingStatus.COMPLETED == "completed"
        assert AIProcessingStatus.FAILED == "failed"


class TestSourceTypeEnum:
    def test_all_values(self) -> None:
        """SourceType has all 4 expected values."""
        assert SourceType.FILE == "file"
        assert SourceType.TEXT_DUMP == "text_dump"
        assert SourceType.URL == "url"
        assert SourceType.SCRAPED == "scraped"


class TestStagedContentModel:
    def test_tablename(self) -> None:
        """StagedContent uses portal_staged_content table."""
        assert StagedContent.__tablename__ == "portal_staged_content"

    def test_has_required_columns(self) -> None:
        """StagedContent model has all expected columns."""
        columns = {c.name for c in StagedContent.__table__.columns}
        expected = {
            "id",
            "workspace_id",
            "source_type",
            "original_filename",
            "mime_type",
            "raw_storage_key",
            "ai_status",
            "ai_output_type",
            "ai_quality_report",
            "token_usage",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_workspace_id_has_fk(self) -> None:
        """workspace_id has a foreign key to portal_workspaces."""
        col = StagedContent.__table__.columns["workspace_id"]
        fk_targets = [fk.target_fullname for fk in col.foreign_keys]
        assert "portal_workspaces.id" in fk_targets


class TestStagedDocumentModel:
    def test_tablename(self) -> None:
        """StagedDocument uses portal_staged_documents table."""
        assert StagedDocument.__tablename__ == "portal_staged_documents"

    def test_has_required_columns(self) -> None:
        """StagedDocument model has all expected columns."""
        columns = {c.name for c in StagedDocument.__table__.columns}
        expected = {
            "id",
            "workspace_id",
            "staged_content_id",
            "title",
            "structured_text",
            "metadata",
            "status",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_status_enum_values(self) -> None:
        """StagedDocumentStatus has draft and approved."""
        assert StagedDocumentStatus.DRAFT == "draft"
        assert StagedDocumentStatus.APPROVED == "approved"


class TestStagedChunkModel:
    def test_tablename(self) -> None:
        """StagedChunk uses portal_staged_chunks table."""
        assert StagedChunk.__tablename__ == "portal_staged_chunks"

    def test_has_required_columns(self) -> None:
        """StagedChunk model has all expected columns."""
        columns = {c.name for c in StagedChunk.__table__.columns}
        expected = {
            "id",
            "workspace_id",
            "staged_document_id",
            "text",
            "chunk_index",
            "metadata",
            "confidence_score",
            "warnings",
            "status",
            "source",
            "edit_history",
            "created_at",
            "updated_at",
        }
        assert expected.issubset(columns)

    def test_staged_document_id_nullable(self) -> None:
        """staged_document_id is nullable (chunks can exist without doc)."""
        col = StagedChunk.__table__.columns["staged_document_id"]
        assert col.nullable is True

    def test_status_enum_values(self) -> None:
        """StagedChunkStatus has draft, approved, rejected."""
        assert StagedChunkStatus.DRAFT == "draft"
        assert StagedChunkStatus.APPROVED == "approved"
        assert StagedChunkStatus.REJECTED == "rejected"

    def test_source_enum_values(self) -> None:
        """StagedChunkSource has ai_generated, manual, from_ingestion."""
        assert StagedChunkSource.AI_GENERATED == "ai_generated"
        assert StagedChunkSource.MANUAL == "manual"
        assert StagedChunkSource.FROM_INGESTION == "from_ingestion"


class TestWorkspaceConversationModel:
    def test_tablename(self) -> None:
        """WorkspaceConversation uses portal_workspace_conversations table."""
        assert WorkspaceConversation.__tablename__ == "portal_workspace_conversations"

    def test_has_required_columns(self) -> None:
        """WorkspaceConversation has id, workspace_id, created_at."""
        columns = {c.name for c in WorkspaceConversation.__table__.columns}
        expected = {"id", "workspace_id", "created_at"}
        assert expected.issubset(columns)


class TestWorkspaceMessageModel:
    def test_tablename(self) -> None:
        """WorkspaceMessage uses portal_workspace_messages table."""
        assert WorkspaceMessage.__tablename__ == "portal_workspace_messages"

    def test_has_required_columns(self) -> None:
        """WorkspaceMessage has all expected columns."""
        columns = {c.name for c in WorkspaceMessage.__table__.columns}
        expected = {
            "id",
            "conversation_id",
            "role",
            "content",
            "tool_calls",
            "token_usage",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_role_enum_values(self) -> None:
        """MessageRole has user, assistant, system."""
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert MessageRole.SYSTEM == "system"


class TestContentRuleModel:
    def test_tablename(self) -> None:
        """ContentRule uses portal_content_rules table."""
        assert ContentRule.__tablename__ == "portal_content_rules"

    def test_has_required_columns(self) -> None:
        """ContentRule has all expected columns."""
        columns = {c.name for c in ContentRule.__table__.columns}
        expected = {
            "id",
            "corpus_id",
            "rule_type",
            "condition",
            "action",
            "priority",
            "enabled",
            "created_at",
        }
        assert expected.issubset(columns)

    def test_rule_type_enum_values(self) -> None:
        """RuleType has mime_type, size, confidence, always."""
        assert RuleType.MIME_TYPE == "mime_type"
        assert RuleType.SIZE == "size"
        assert RuleType.CONFIDENCE == "confidence"
        assert RuleType.ALWAYS == "always"

    def test_rule_action_enum_values(self) -> None:
        """RuleAction has stage and direct_ingest."""
        assert RuleAction.STAGE == "stage"
        assert RuleAction.DIRECT_INGEST == "direct_ingest"

    def test_corpus_id_is_indexed(self) -> None:
        """corpus_id column has an index for efficient lookups."""
        col = ContentRule.__table__.columns["corpus_id"]
        assert col.index is True


class TestAllModelsImported:
    def test_models_init_exports(self) -> None:
        """content_portal.models.__init__ exports all 7 models."""
        from callobuzz_rag.content_portal.models import __all__

        expected = {
            "ContentRule",
            "StagedChunk",
            "StagedContent",
            "StagedDocument",
            "Workspace",
            "WorkspaceConversation",
            "WorkspaceMessage",
        }
        assert set(__all__) == expected

    def test_all_models_share_base(self) -> None:
        """All portal models inherit from the same Base."""
        from callobuzz_rag.db.base import Base

        models = [
            Workspace,
            StagedContent,
            StagedDocument,
            StagedChunk,
            WorkspaceConversation,
            WorkspaceMessage,
            ContentRule,
        ]
        for model in models:
            assert issubclass(model, Base), f"{model.__name__} missing Base"

    def test_all_tables_prefixed_with_portal(self) -> None:
        """All portal table names start with 'portal_'."""
        models = [
            Workspace,
            StagedContent,
            StagedDocument,
            StagedChunk,
            WorkspaceConversation,
            WorkspaceMessage,
            ContentRule,
        ]
        for model in models:
            assert model.__tablename__.startswith("portal_"), (
                f"{model.__name__} table {model.__tablename__} missing portal_ prefix"
            )
