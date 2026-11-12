"""Tests for Memory CRUD API routes."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _mock_corpus(corpus_id: uuid.UUID) -> MagicMock:
    c = MagicMock()
    c.id = corpus_id
    return c


class TestListMemories:
    @pytest.mark.asyncio
    async def test_list_empty(self):
        from callobuzz_rag.api.routes.memories import list_memories

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=MagicMock(return_value=0)),
                MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
                ),
            ]
        )

        # Keyword args on purpose. This call was positional and silently went stale
        # when `user_id` was inserted into the signature: every later argument shifted
        # one place, `corpus` fell back to its unresolved `Depends(...)` default, and
        # the test died on `'Depends' object has no attribute 'id'`.
        result = await list_memories(
            corpus_id=corpus_id,
            memory_type=None,
            user_id=None,
            limit=50,
            offset=0,
            session=session,
            corpus=_mock_corpus(corpus_id),
        )
        assert result.total == 0
        assert result.items == []


class TestDeleteMemory:
    @pytest.mark.asyncio
    async def test_delete_not_found(self):
        from callobuzz_rag.api.routes.memories import delete_memory

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await delete_memory(corpus_id, uuid.uuid4(), session, _mock_corpus(corpus_id))
        assert exc_info.value.status_code == 404


class TestBulkDelete:
    @pytest.mark.asyncio
    async def test_bulk_delete_by_type(self):
        from callobuzz_rag.api.routes.memories import bulk_delete_memories

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(rowcount=3))
        session.commit = AsyncMock()

        result = await bulk_delete_memories(
            corpus_id, "fact", None, session, _mock_corpus(corpus_id)
        )
        assert result.deleted == 3

    @pytest.mark.asyncio
    async def test_bulk_delete_no_filter(self):
        from callobuzz_rag.api.routes.memories import bulk_delete_memories

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(rowcount=10))
        session.commit = AsyncMock()

        result = await bulk_delete_memories(corpus_id, None, None, session, _mock_corpus(corpus_id))
        assert result.deleted == 10


class TestMemoryTypeIsValidated:
    """`memory_type` is one of the kinds the memory module defines; anything else is 422."""

    def test_the_allowed_kinds_are_the_ones_extraction_produces(self) -> None:
        from callobuzz_rag.db.models.memory import MEMORY_TYPES

        assert MEMORY_TYPES == ("fact", "preference", "entity")

    @pytest.mark.parametrize(
        ("method", "suffix", "kwargs"),
        [
            ("POST", "", {"json": {"content": "x", "user_id": "u", "memory_type": "opinion"}}),
            (
                "POST",
                "/bulk",
                {"json": {"memories": [{"content": "x", "user_id": "u", "memory_type": "?"}]}},
            ),
            ("GET", "?memory_type=opinion", {}),
            ("DELETE", "?memory_type=opinion", {}),
        ],
    )
    async def test_an_unknown_kind_is_422(self, method: str, suffix: str, kwargs: dict) -> None:
        from tests.unit.scope_harness import call, scoped_app

        with scoped_app() as (app, world):
            path = f"/api/v1/corpora/{world.corpus_a.id}/memories{suffix}"
            res = await call(app, "owner_a", method, path, **kwargs)
            world.session.execute.assert_not_awaited()
            world.session.commit.assert_not_awaited()
        assert res.status_code == 422, res.text
        assert "memory_type" in res.text

    async def test_a_known_kind_still_filters(self) -> None:
        from tests.unit.scope_harness import call, scoped_app

        with scoped_app() as (app, world):
            path = f"/api/v1/corpora/{world.corpus_a.id}/memories?memory_type=preference"
            res = await call(app, "owner_a", "DELETE", path)
        assert res.status_code == 200, res.text


class TestGetMemory:
    @pytest.mark.asyncio
    async def test_get_not_found(self):
        from callobuzz_rag.api.routes.memories import get_memory

        corpus_id = uuid.uuid4()
        session = AsyncMock()
        session.get = AsyncMock(return_value=None)

        with pytest.raises(HTTPException) as exc_info:
            await get_memory(corpus_id, uuid.uuid4(), session, _mock_corpus(corpus_id))
        assert exc_info.value.status_code == 404
