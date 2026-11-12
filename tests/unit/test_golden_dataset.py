"""Unit tests for golden dataset management (Epic 3-06)."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.db.models.feedback import Feedback
from callobuzz_rag.db.models.golden_pair import GoldenPair
from callobuzz_rag.db.models.golden_set import GoldenSet
from callobuzz_rag.db.models.query_log import QueryLog


def _session_with_default_set() -> AsyncMock:
    """A mocked session whose default-set lookup finds an existing set."""
    session = AsyncMock()
    found = MagicMock()
    found.scalar_one_or_none.return_value = GoldenSet(id=uuid.uuid4(), name="Golden questions")
    session.execute.return_value = found
    return session


class TestGoldenPairModel:
    def test_golden_pair_model_fields(self) -> None:
        """GoldenPair has all required columns with correct types."""
        pair = GoldenPair(
            id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            query="What is RAG?",
            expected_answer="Retrieval-Augmented Generation is...",
            expected_chunk_ids=[uuid.uuid4(), uuid.uuid4()],
            pair_metadata={"topic": "definitions"},
            source="manual",
        )
        assert pair.query == "What is RAG?"
        assert pair.expected_answer == "Retrieval-Augmented Generation is..."
        assert len(pair.expected_chunk_ids) == 2
        assert pair.pair_metadata == {"topic": "definitions"}
        assert pair.source == "manual"

    def test_golden_pair_nullable_fields(self) -> None:
        """expected_chunk_ids and metadata are nullable."""
        pair = GoldenPair(
            id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            query="What is RAG?",
            expected_answer="Retrieval-Augmented Generation.",
            source="manual",
        )
        assert pair.expected_chunk_ids is None
        assert pair.pair_metadata is None

    def test_golden_pair_source_values(self) -> None:
        """Source field accepts manual, feedback, and import."""
        for source in ("manual", "feedback", "import"):
            pair = GoldenPair(
                id=uuid.uuid4(),
                corpus_id=uuid.uuid4(),
                query="test",
                expected_answer="test",
                source=source,
            )
            assert pair.source == source


class TestGoldenDatasetManagerCreate:
    @pytest.mark.asyncio
    async def test_create_golden_pair(self) -> None:
        """create() persists a golden pair and returns it."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = _session_with_default_set()
        manager = GoldenDatasetManager(session)
        corpus_id = uuid.uuid4()

        await manager.create(
            corpus_id=corpus_id,
            query="What is RAG?",
            expected_answer="Retrieval-Augmented Generation.",
            source="manual",
        )

        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()
        added = session.add.call_args[0][0]
        assert isinstance(added, GoldenPair)
        assert added.corpus_id == corpus_id
        assert added.query == "What is RAG?"
        assert added.source == "manual"

    @pytest.mark.asyncio
    async def test_create_with_chunk_ids_and_metadata(self) -> None:
        """create() supports optional expected_chunk_ids and metadata."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = _session_with_default_set()
        manager = GoldenDatasetManager(session)
        chunk_ids = [uuid.uuid4(), uuid.uuid4()]

        await manager.create(
            corpus_id=uuid.uuid4(),
            query="test",
            expected_answer="test answer",
            expected_chunk_ids=chunk_ids,
            pair_metadata={"topic": "testing"},
            source="import",
        )

        added = session.add.call_args[0][0]
        assert added.expected_chunk_ids == chunk_ids
        assert added.pair_metadata == {"topic": "testing"}
        assert added.source == "import"


class TestGoldenDatasetManagerList:
    @pytest.mark.asyncio
    async def test_list_by_corpus(self) -> None:
        """list_by_corpus() returns pairs filtered by corpus_id."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        corpus_id = uuid.uuid4()
        mock_pairs = [
            GoldenPair(
                id=uuid.uuid4(),
                corpus_id=corpus_id,
                query="q1",
                expected_answer="a1",
                source="manual",
            ),
        ]

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_pairs
        session.execute.return_value = mock_result

        manager = GoldenDatasetManager(session)
        result = await manager.list_by_corpus(corpus_id, limit=50, offset=0)

        assert result == mock_pairs
        session.execute.assert_awaited_once()


class TestGoldenDatasetManagerDelete:
    @pytest.mark.asyncio
    async def test_delete_existing(self) -> None:
        """delete() removes a pair and commits."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        pair_id = uuid.uuid4()
        mock_pair = GoldenPair(
            id=pair_id,
            corpus_id=uuid.uuid4(),
            query="q",
            expected_answer="a",
            source="manual",
        )
        session.get.return_value = mock_pair

        manager = GoldenDatasetManager(session)
        await manager.delete(pair_id)

        session.delete.assert_awaited_once_with(mock_pair)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_not_found_raises(self) -> None:
        """delete() raises ValueError if pair does not exist."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        session.get.return_value = None

        manager = GoldenDatasetManager(session)
        with pytest.raises(ValueError, match="not found"):
            await manager.delete(uuid.uuid4())


class TestGoldenDatasetManagerImportExport:
    @pytest.mark.asyncio
    async def test_import_from_csv(self, tmp_path: Path) -> None:
        """import_from_csv() reads CSV rows and creates pairs."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        csv_file = tmp_path / "golden.csv"
        csv_file.write_text(
            "query,expected_answer,expected_chunk_ids\n"
            "What is RAG?,Retrieval-Augmented Generation.,\n"
            "How does chunking work?,Text is split into segments.,"
            '"550e8400-e29b-41d4-a716-446655440000"\n'
            ",,\n",
            encoding="utf-8",
        )

        session = _session_with_default_set()
        manager = GoldenDatasetManager(session)
        count = await manager.import_from_csv(uuid.uuid4(), csv_file)

        assert count == 2
        assert session.add.call_count == 2
        session.commit.assert_awaited_once()

        second_pair = session.add.call_args_list[1][0][0]
        assert second_pair.expected_chunk_ids is not None
        assert len(second_pair.expected_chunk_ids) == 1
        assert second_pair.source == "import"

    @pytest.mark.asyncio
    async def test_import_reads_a_csv_with_a_byte_order_mark(self, tmp_path: Path) -> None:
        """Excel saves "CSV UTF-8" with a BOM; the header must still read as `query`."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        csv_file = tmp_path / "golden.csv"
        csv_file.write_bytes(
            "query,expected_answer\nWhat is RAG?,Retrieval-Augmented Generation.\n".encode(
                "utf-8-sig"
            )
        )

        session = _session_with_default_set()
        count = await GoldenDatasetManager(session).import_from_csv(uuid.uuid4(), csv_file)

        assert count == 1
        assert session.add.call_args[0][0].query == "What is RAG?"

    @pytest.mark.asyncio
    async def test_import_names_the_row_and_value_of_a_bad_chunk_id(self, tmp_path: Path) -> None:
        """A malformed chunk id is the caller's mistake: a typed error naming where it is."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager, GoldenImportError

        csv_file = tmp_path / "golden.csv"
        csv_file.write_text(
            "query,expected_answer,expected_chunk_ids\n"
            "q1,a1,\n"
            'q2,a2,"550e8400-e29b-41d4-a716-446655440000, not-a-uuid"\n',
            encoding="utf-8",
        )

        session = AsyncMock()
        with pytest.raises(GoldenImportError) as exc_info:
            await GoldenDatasetManager(session).import_from_csv(uuid.uuid4(), csv_file)

        message = str(exc_info.value)
        assert "row 3" in message and "not-a-uuid" in message
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_import_skips_empty_rows(self, tmp_path: Path) -> None:
        """import_from_csv() skips rows with empty query or answer."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        csv_file = tmp_path / "golden.csv"
        csv_file.write_text(
            "query,expected_answer\n,some answer\nsome query,\nvalid query,valid answer\n",
            encoding="utf-8",
        )

        session = _session_with_default_set()
        manager = GoldenDatasetManager(session)
        count = await manager.import_from_csv(uuid.uuid4(), csv_file)

        assert count == 1

    @pytest.mark.asyncio
    async def test_export_to_json(self) -> None:
        """export_to_json() returns serializable dicts."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        corpus_id = uuid.uuid4()
        pair_id = uuid.uuid4()
        now = datetime.now(UTC)

        mock_pair = GoldenPair(
            id=pair_id,
            corpus_id=corpus_id,
            query="What is RAG?",
            expected_answer="Retrieval-Augmented Generation.",
            expected_chunk_ids=None,
            pair_metadata=None,
            source="manual",
        )
        mock_pair.created_at = now

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_pair]
        session.execute.return_value = mock_result

        manager = GoldenDatasetManager(session)
        result = await manager.export_to_json(corpus_id)

        assert len(result) == 1
        assert result[0]["id"] == str(pair_id)
        assert result[0]["query"] == "What is RAG?"
        assert result[0]["expected_chunk_ids"] is None
        assert result[0]["created_at"] == now.isoformat()


class TestGoldenDatasetManagerFromFeedback:
    @pytest.mark.asyncio
    async def test_create_from_feedback_success(self) -> None:
        """create_from_feedback() builds a golden pair from feedback correction."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = _session_with_default_set()
        feedback_id = uuid.uuid4()
        query_log_id = uuid.uuid4()
        corpus_id = uuid.uuid4()
        chunk_ids = [uuid.uuid4()]

        mock_feedback = Feedback(
            id=feedback_id,
            query_log_id=query_log_id,
            rating=2,
            correction="The correct answer is X.",
        )
        mock_query_log = QueryLog(
            id=query_log_id,
            query_text="What is X?",
            retrieved_chunk_ids=chunk_ids,
        )
        mock_query_log.corpus_id = corpus_id

        session.get.side_effect = lambda model, id_: {
            Feedback: mock_feedback,
            QueryLog: mock_query_log,
        }.get(model)

        manager = GoldenDatasetManager(session)
        await manager.create_from_feedback(feedback_id)

        added = session.add.call_args[0][0]
        assert added.query == "What is X?"
        assert added.expected_answer == "The correct answer is X."
        assert added.expected_chunk_ids == chunk_ids
        assert added.source == "feedback"
        assert added.pair_metadata["feedback_id"] == str(feedback_id)

    @pytest.mark.asyncio
    async def test_create_from_feedback_no_correction_raises(self) -> None:
        """create_from_feedback() raises if feedback has no correction."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        feedback_id = uuid.uuid4()

        mock_feedback = Feedback(
            id=feedback_id,
            query_log_id=uuid.uuid4(),
            rating=5,
            correction=None,
        )
        session.get.return_value = mock_feedback

        manager = GoldenDatasetManager(session)
        with pytest.raises(ValueError, match="no correction"):
            await manager.create_from_feedback(feedback_id)

    @pytest.mark.asyncio
    async def test_create_from_feedback_not_found_raises(self) -> None:
        """create_from_feedback() raises if feedback does not exist."""
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = AsyncMock()
        session.get.return_value = None

        manager = GoldenDatasetManager(session)
        with pytest.raises(ValueError, match="not found"):
            await manager.create_from_feedback(uuid.uuid4())


class TestGoldenRoutes:
    """Smoke tests — verify routes exist and return expected status codes."""

    def _make_client(self):
        """Create a test client with the golden router."""
        from contextlib import asynccontextmanager
        from unittest.mock import MagicMock

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from callobuzz_rag.api.deps import Scope, resolve_scope
        from callobuzz_rag.api.routes.golden import router

        app = FastAPI()
        app.include_router(router)

        # Bypass auth: these routes resolve a Scope, so override that. Admin scope
        # keeps these tests focused on behaviour rather than authorization;
        # authorization itself is covered by test_tenant_scoping.py.
        app.dependency_overrides[resolve_scope] = lambda: Scope(is_admin=True)

        mock_session = AsyncMock()
        mock_factory = MagicMock()

        @asynccontextmanager
        async def _session_ctx():  # type: ignore[no-untyped-def]
            yield mock_session

        mock_factory.side_effect = _session_ctx
        app.state.session_factory = mock_factory
        return TestClient(app)

    def test_create_route_exists(self) -> None:
        """POST /api/v1/golden returns 422 without body (route registered)."""
        client = self._make_client()
        resp = client.post("/api/v1/golden", json={})
        assert resp.status_code == 422

    def test_list_route_requires_corpus_id(self) -> None:
        """GET /api/v1/golden without corpus_id returns 422."""
        client = self._make_client()
        resp = client.get("/api/v1/golden")
        assert resp.status_code == 422

    def test_delete_route_exists(self) -> None:
        """DELETE /api/v1/golden/{id} route is registered."""
        client = self._make_client()
        resp = client.delete(f"/api/v1/golden/{uuid.uuid4()}")
        assert resp.status_code != 405


class TestGoldenSetLimit:
    @pytest.mark.asyncio
    async def test_create_refuses_a_full_set(self) -> None:
        from callobuzz_rag.api.errors import APIError
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        session = _session_with_default_set()
        session.execute.return_value.scalar.return_value = 200
        manager = GoldenDatasetManager(session, max_pairs=200)
        with pytest.raises(APIError) as caught:
            await manager.create(uuid.uuid4(), "q", "a")
        assert caught.value.code == "golden_set_limit"
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_import_refuses_rows_that_overfill_the_set(self, tmp_path: Path) -> None:
        from callobuzz_rag.api.errors import APIError
        from callobuzz_rag.evaluation.golden import GoldenDatasetManager

        csv_file = tmp_path / "g.csv"
        csv_file.write_text("query,expected_answer\nq1,a1\nq2,a2\n", encoding="utf-8")
        session = _session_with_default_set()
        session.execute.return_value.scalar.return_value = 199
        with pytest.raises(APIError) as caught:
            await GoldenDatasetManager(session, max_pairs=200).import_from_csv(
                uuid.uuid4(), csv_file
            )
        assert caught.value.code == "golden_set_limit"
        session.add.assert_not_called()
