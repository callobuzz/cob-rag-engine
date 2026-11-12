"""Unit tests for AI Chat schemas and service (CP-10)."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from callobuzz_rag.content_portal.schemas.ai_chat import (
    ChatHistoryMessage,
    ChatHistoryResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ExecuteActionRequest,
    ExecuteActionResponse,
    ToolCallInfo,
)
from callobuzz_rag.content_portal.services.ai_chat_service import (
    MAX_CHUNK_PREVIEW_CHARS,
    MAX_CONTEXT_CHUNKS,
    ActionExecutionError,
    AIChatError,
    AIChatService,
    _build_workspace_context,
    _parse_tool_calls,
)
from callobuzz_rag.llm.models import LLMResponse, LLMUsage

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeChunk:
    """Minimal staged chunk fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    text: str = "Some chunk text."
    chunk_index: int = 0
    confidence_score: float | None = 0.95
    staged_document_id: uuid.UUID | None = None
    source: str = "ai_generated"
    edit_history: list | None = None
    status: str = "draft"


@dataclass
class FakeWorkspace:
    """Minimal workspace fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    corpus_id: uuid.UUID = field(default_factory=uuid.uuid4)
    title: str = "Test Workspace"
    status: str = "draft"


@dataclass
class FakeConversation:
    """Minimal conversation fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    messages: list = field(default_factory=list)


@dataclass
class FakeMessage:
    """Minimal message fake."""

    id: uuid.UUID = field(default_factory=uuid.uuid4)
    conversation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    role: str = "assistant"
    content: str = "Hello!"
    tool_calls: list | None = None
    token_usage: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ===========================================================================
# _build_workspace_context tests
# ===========================================================================


class TestBuildWorkspaceContext:
    """Tests for the workspace context builder."""

    def test_empty_chunks(self) -> None:
        result = _build_workspace_context([], "My Workspace")
        assert "My Workspace" in result
        assert "No chunks available yet" in result

    def test_single_chunk(self) -> None:
        chunk = FakeChunk(text="Hello world", confidence_score=0.9)
        result = _build_workspace_context([chunk], "WS")
        assert "Chunk #0" in result
        assert "Hello world" in result
        assert "(confidence: 0.90)" in result

    def test_truncation(self) -> None:
        long_text = "A" * (MAX_CHUNK_PREVIEW_CHARS + 100)
        chunk = FakeChunk(text=long_text)
        result = _build_workspace_context([chunk], "WS")
        assert "..." in result
        # Should not contain the full text
        assert long_text not in result

    def test_max_chunks(self) -> None:
        chunks = [FakeChunk(text=f"Chunk {i}") for i in range(MAX_CONTEXT_CHUNKS + 5)]
        result = _build_workspace_context(chunks, "WS")
        assert "5 more chunks" in result

    def test_no_confidence(self) -> None:
        chunk = FakeChunk(text="Text", confidence_score=None)
        result = _build_workspace_context([chunk], "WS")
        assert "confidence" not in result


# ===========================================================================
# _parse_tool_calls tests
# ===========================================================================


class TestParseToolCalls:
    """Tests for tool call parsing from AI responses."""

    def test_no_calls(self) -> None:
        assert _parse_tool_calls("Just a normal response.") == []

    def test_single_call(self) -> None:
        content = (
            "I suggest splitting:\n"
            "```tool_call\n"
            '{"tool_name": "split_chunk", "arguments": {"chunk_id": "abc"}}\n'
            "```"
        )
        result = _parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["tool_name"] == "split_chunk"
        assert result[0]["arguments"]["chunk_id"] == "abc"

    def test_multiple_calls(self) -> None:
        content = (
            "```tool_call\n"
            '{"tool_name": "split_chunk", "arguments": {}}\n'
            "```\n"
            "And also:\n"
            "```tool_call\n"
            '{"tool_name": "rewrite_chunk", "arguments": {}}\n'
            "```"
        )
        result = _parse_tool_calls(content)
        assert len(result) == 2

    def test_malformed_json(self) -> None:
        content = "```tool_call\n{not valid json}\n```"
        assert _parse_tool_calls(content) == []

    def test_missing_tool_name(self) -> None:
        content = '```tool_call\n{"arguments": {}}\n```'
        assert _parse_tool_calls(content) == []

    def test_unclosed_block(self) -> None:
        content = '```tool_call\n{"tool_name": "split_chunk"}\n'
        assert _parse_tool_calls(content) == []


# ===========================================================================
# Schema tests
# ===========================================================================


class TestChatMessageRequest:
    """Tests for ChatMessageRequest schema."""

    def test_valid(self) -> None:
        req = ChatMessageRequest(message="Hello", user="alice")
        assert req.message == "Hello"
        assert req.user == "alice"
        assert req.stream is False
        assert req.model is None

    def test_stream(self) -> None:
        req = ChatMessageRequest(message="Hello", user="alice", stream=True)
        assert req.stream is True

    def test_custom_model(self) -> None:
        req = ChatMessageRequest(message="Hello", user="alice", model="gpt-4o")
        assert req.model == "gpt-4o"

    def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatMessageRequest(message="", user="alice")

    def test_empty_user_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatMessageRequest(message="Hello", user="")


class TestToolCallInfo:
    """Tests for ToolCallInfo schema."""

    def test_valid(self) -> None:
        tc = ToolCallInfo(
            tool_name="split_chunk",
            arguments={"chunk_id": "abc"},
        )
        assert tc.tool_name == "split_chunk"

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Invalid tool name"):
            ToolCallInfo(tool_name="delete_everything", arguments={})

    def test_all_valid_names(self) -> None:
        for name in ["split_chunk", "merge_chunks", "rewrite_chunk", "search_corpus"]:
            tc = ToolCallInfo(tool_name=name)
            assert tc.tool_name == name

    def test_default_arguments(self) -> None:
        tc = ToolCallInfo(tool_name="split_chunk")
        assert tc.arguments == {}


class TestChatMessageResponse:
    """Tests for ChatMessageResponse schema."""

    def test_construction(self) -> None:
        now = datetime.now(UTC)
        resp = ChatMessageResponse(
            message_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            content="Hi there!",
            created_at=now,
        )
        assert resp.role == "assistant"
        assert resp.tool_calls is None

    def test_with_tool_calls(self) -> None:
        now = datetime.now(UTC)
        tc = ToolCallInfo(tool_name="split_chunk", arguments={"chunk_id": "x"})
        resp = ChatMessageResponse(
            message_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            content="I suggest splitting",
            tool_calls=[tc],
            token_usage={"total_tokens": 100},
            created_at=now,
        )
        assert len(resp.tool_calls) == 1
        assert resp.token_usage["total_tokens"] == 100


class TestChatHistoryResponse:
    """Tests for ChatHistoryResponse schema."""

    def test_empty(self) -> None:
        resp = ChatHistoryResponse(
            workspace_id=uuid.uuid4(),
            messages=[],
            total=0,
        )
        assert resp.conversation_id is None
        assert resp.total == 0

    def test_with_messages(self) -> None:
        now = datetime.now(UTC)
        msg = ChatHistoryMessage(
            id=uuid.uuid4(),
            role="assistant",
            content="Hello",
            created_at=now,
        )
        resp = ChatHistoryResponse(
            workspace_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            messages=[msg],
            total=1,
        )
        assert len(resp.messages) == 1


class TestExecuteActionSchemas:
    """Tests for ExecuteActionRequest and ExecuteActionResponse."""

    def test_request_valid(self) -> None:
        req = ExecuteActionRequest(
            action=ToolCallInfo(
                tool_name="rewrite_chunk",
                arguments={"chunk_id": "abc", "new_text": "better text"},
            ),
            user="alice",
        )
        assert req.action.tool_name == "rewrite_chunk"

    def test_response_valid(self) -> None:
        resp = ExecuteActionResponse(
            action="split_chunk",
            success=True,
            message="Split into 3 parts",
            result={"new_chunk_ids": ["a", "b", "c"]},
        )
        assert resp.success is True
        assert resp.result is not None


# ===========================================================================
# AIChatService tests
# ===========================================================================


class TestAIChatServiceSendMessage:
    """Tests for AIChatService.send_message."""

    @pytest.fixture()
    def llm(self) -> MagicMock:
        mock = MagicMock()
        mock.complete = AsyncMock(
            return_value=LLMResponse(
                content="Here is my response.",
                model="gpt-4o",
                usage=LLMUsage(
                    prompt_tokens=10,
                    completion_tokens=20,
                    total_tokens=30,
                ),
            )
        )
        return mock

    @pytest.fixture()
    def service(self, llm: MagicMock) -> AIChatService:
        return AIChatService(llm=llm)

    @pytest.fixture()
    def session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_creates_conversation(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        workspace = FakeWorkspace(id=ws_id)
        conversation = FakeConversation(workspace_id=ws_id)
        saved_msg = FakeMessage(
            conversation_id=conversation.id,
            content="Here is my response.",
        )

        with (
            patch.object(service, "_get_workspace", return_value=workspace),
            patch.object(service, "_get_or_create_conversation", return_value=conversation),
            patch.object(service, "_save_message", return_value=saved_msg),
            patch.object(service, "_get_workspace_chunks", return_value=[]),
        ):
            result = await service.send_message(session, ws_id, user="alice", message="Hello")

        assert result["content"] == "Here is my response."
        assert result["conversation_id"] == conversation.id
        assert result["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_with_tool_calls(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        workspace = FakeWorkspace(id=ws_id)
        conversation = FakeConversation(workspace_id=ws_id)
        saved_msg = FakeMessage(conversation_id=conversation.id)

        response_with_tools = LLMResponse(
            content=(
                "I suggest:\n"
                "```tool_call\n"
                '{"tool_name": "split_chunk", "arguments": {"chunk_id": "x"}}\n'
                "```"
            ),
            model="gpt-4o",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
        )
        service._llm.complete = AsyncMock(return_value=response_with_tools)

        with (
            patch.object(service, "_get_workspace", return_value=workspace),
            patch.object(service, "_get_or_create_conversation", return_value=conversation),
            patch.object(service, "_save_message", return_value=saved_msg),
            patch.object(service, "_get_workspace_chunks", return_value=[]),
        ):
            result = await service.send_message(session, ws_id, user="alice", message="Split chunk")

        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["tool_name"] == "split_chunk"

    @pytest.mark.asyncio
    async def test_llm_failure(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        workspace = FakeWorkspace(id=ws_id)
        conversation = FakeConversation(workspace_id=ws_id)

        service._llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

        with (
            patch.object(service, "_get_workspace", return_value=workspace),
            patch.object(service, "_get_or_create_conversation", return_value=conversation),
            patch.object(service, "_save_message", return_value=FakeMessage()),
            patch.object(service, "_get_workspace_chunks", return_value=[]),
            pytest.raises(AIChatError, match="LLM completion failed"),
        ):
            await service.send_message(session, ws_id, user="alice", message="Hello")


class TestAIChatServiceExecuteAction:
    """Tests for AIChatService.execute_action."""

    @pytest.fixture()
    def service(self) -> AIChatService:
        llm = MagicMock()
        return AIChatService(llm=llm)

    @pytest.fixture()
    def session(self) -> AsyncMock:
        return AsyncMock()

    @pytest.mark.asyncio
    async def test_split(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        chunk = FakeChunk(
            workspace_id=ws_id,
            text="First part. Second part.",
            staged_document_id=None,
        )

        # Mock the SQLAlchemy select result
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = chunk
        session.execute = AsyncMock(return_value=mock_result)
        session.add = MagicMock()
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await service.execute_action(
            session,
            ws_id,
            user="alice",
            tool_name="split_chunk",
            arguments={"chunk_id": str(chunk.id), "split_points": [12]},
        )
        assert result["success"] is True
        assert result["action"] == "split_chunk"
        # The editor's entry format: a timestamp, the source id in details.
        entry = session.add.call_args.args[0].edit_history[0]
        assert entry["action"] == "split_from"
        assert entry["timestamp"]
        assert entry["details"] == {"source_chunk_id": str(chunk.id)}

    @pytest.mark.asyncio
    async def test_merge(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        c1 = FakeChunk(workspace_id=ws_id, text="Part 1", chunk_index=0)
        c2 = FakeChunk(workspace_id=ws_id, text="Part 2", chunk_index=1)

        call_count = 0
        chunks = [c1, c2]

        def make_result() -> MagicMock:
            nonlocal call_count
            r = MagicMock()
            if call_count < len(chunks):
                r.scalar_one_or_none.return_value = chunks[call_count]
            else:
                r.scalar_one_or_none.return_value = None
            call_count += 1
            return r

        session.execute = AsyncMock(side_effect=lambda *a, **kw: make_result())
        session.add = MagicMock()
        session.delete = AsyncMock()
        session.flush = AsyncMock()

        result = await service.execute_action(
            session,
            ws_id,
            user="alice",
            tool_name="merge_chunks",
            arguments={"chunk_ids": [str(c1.id), str(c2.id)]},
        )
        assert result["success"] is True
        assert result["action"] == "merge_chunks"
        entry = session.add.call_args.args[0].edit_history[0]
        assert entry["action"] == "merged_from"
        assert entry["timestamp"]
        assert entry["details"] == {"source_chunk_ids": [str(c1.id), str(c2.id)]}

    @pytest.mark.asyncio
    async def test_rewrite(self, service: AIChatService, session: AsyncMock) -> None:
        ws_id = uuid.uuid4()
        chunk = FakeChunk(workspace_id=ws_id, text="Old text")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = chunk
        session.execute = AsyncMock(return_value=mock_result)
        session.flush = AsyncMock()

        result = await service.execute_action(
            session,
            ws_id,
            user="alice",
            tool_name="rewrite_chunk",
            arguments={"chunk_id": str(chunk.id), "new_text": "Improved text"},
        )
        assert result["success"] is True
        assert result["action"] == "rewrite_chunk"
        assert chunk.text == "Improved text"
        entry = chunk.edit_history[-1]
        assert (entry["action"], entry["old_text"]) == ("rewrite", "Old text")
        assert entry["timestamp"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, service: AIChatService, session: AsyncMock) -> None:
        with pytest.raises(ActionExecutionError, match="Unknown action"):
            await service.execute_action(
                session,
                uuid.uuid4(),
                user="alice",
                tool_name="delete_all",
                arguments={},
            )

    @pytest.mark.asyncio
    async def test_missing_args_split(self, service: AIChatService, session: AsyncMock) -> None:
        with pytest.raises(ActionExecutionError, match="chunk_id is required"):
            await service.execute_action(
                session,
                uuid.uuid4(),
                user="alice",
                tool_name="split_chunk",
                arguments={},
            )

    @pytest.mark.asyncio
    async def test_search_no_orchestrator(self, service: AIChatService, session: AsyncMock) -> None:
        result = await service.execute_action(
            session,
            uuid.uuid4(),
            user="alice",
            tool_name="search_corpus",
            arguments={"query": "test"},
        )
        assert result["success"] is False
        assert "not available" in result["message"]

    @pytest.mark.asyncio
    async def test_search_calls_real_orchestrator_api(
        self, service: AIChatService, session: AsyncMock
    ) -> None:
        """search_corpus must only call methods QueryOrchestrator really has.

        Regression test. This path previously called `query_orchestrator.search`,
        which does not exist — every real search raised AttributeError, caught by
        the surrounding `except Exception` and re-raised as the plausible-looking
        "Corpus search failed", so it read like a search miss rather than a bug.
        A plain MagicMock hides this because it invents `.search` on demand;
        create_autospec is constrained to the real class, so it cannot.
        """
        from unittest.mock import create_autospec

        from callobuzz_rag.retrieval.context_assembly import CitedChunk
        from callobuzz_rag.retrieval.orchestrator import QueryOrchestrator

        chunk = CitedChunk(
            citation_index=1,
            chunk_id=uuid.uuid4(),
            text="Bulk orders ship in 48 hours.",
            file_name="ops.md",
            page=None,
            score=0.91,
            token_count=7,
        )
        orchestrator = create_autospec(QueryOrchestrator, instance=True)
        orchestrator.run_retrieval_pipeline.return_value = MagicMock(
            assembled=MagicMock(cited_chunks=[chunk])
        )

        result = await service.execute_action(
            session,
            uuid.uuid4(),
            user="alice",
            tool_name="search_corpus",
            arguments={"query": "shipping time", "top_k": 3},
            corpus_id=uuid.uuid4(),
            query_orchestrator=orchestrator,
        )

        assert result["success"] is True, result
        assert result["result"]["results"][0]["chunk_id"] == str(chunk.chunk_id)
        assert result["result"]["results"][0]["score"] == 0.91
        orchestrator.run_retrieval_pipeline.assert_awaited_once()
        assert orchestrator.run_retrieval_pipeline.await_args.kwargs["top_k"] == 3
