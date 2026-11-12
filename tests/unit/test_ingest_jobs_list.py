"""Ingest jobs: corpus, file, mode, similar documents; filters, paging, scope (G-8)."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from callobuzz_rag.db.models.ingest_job import IngestJob
from tests.unit.scope_harness import MATRIX, call, check, scoped_app

NOW = datetime(2026, 9, 30, tzinfo=UTC)


def _job(corpus_id: uuid.UUID, **review: object) -> IngestJob:
    return IngestJob(
        id=uuid.uuid4(),
        status="pending_review",
        corpus_id=corpus_id,
        mode="auto",
        total_documents=1,
        processed_documents=0,
        created_at=NOW,
        expires_at=NOW + timedelta(days=7),
        review_metadata={"file_name": "price-list.pdf", "mime_type": "application/pdf", **review},
    )


def _result(*, rows=None, scalar=None, scalars=None) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows or []
    result.scalar.return_value = scalar
    result.scalars.return_value.all.return_value = scalars or []
    return result


@pytest.mark.parametrize(("key", "which", "outcome"), MATRIX)
async def test_list_scoping(key, which, outcome) -> None:
    with scoped_app() as (app, world):
        res = await call(app, key, "GET", f"/api/v1/ingest/jobs?corpus_id={world.corpus(which).id}")
    check(outcome, res.status_code, res.text)


@pytest.mark.parametrize(
    ("key", "status"), [("admin", 200), ("tk_b", 200), ("ck_b", 200), ("tk_a", 403), ("ck_a", 403)]
)
async def test_detail_scoping(key, status) -> None:
    with scoped_app() as (app, world):
        job = _job(world.corpus_b.id)
        world.add_row(IngestJob, job)
        # A tenant's scope is the ids of the corpora it owns.
        owned = [world.corpus_b.id] if key == "tk_b" else [world.corpus_a.id]
        world.session.execute = AsyncMock(
            side_effect=[_result(scalars=owned), _result(rows=[])]
            if key.startswith("tk_")
            else [_result(rows=[])]
        )
        res = await call(app, key, "GET", f"/api/v1/ingest/jobs/{job.id}")
    assert res.status_code == status, res.text


async def test_job_carries_corpus_file_mode_and_named_similar_documents() -> None:
    with scoped_app() as (app, world):
        similar = uuid.uuid4()
        job = _job(
            world.corpus_a.id,
            similar_documents=[{"document_id": str(similar), "similarity": 0.93, "match_count": 5}],
        )
        world.add_row(IngestJob, job)
        world.session.execute = AsyncMock(return_value=_result(rows=[(similar, "prices-2025.pdf")]))
        res = await call(app, "ck_a", "GET", f"/api/v1/ingest/jobs/{job.id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["corpus_id"] == str(world.corpus_a.id)
    assert (body["file_name"], body["mode"], body["status"]) == (
        "price-list.pdf",
        "auto",
        "pending_review",
    )
    assert body["expires_at"] is not None
    assert body["similar_documents"] == [
        {
            "document_id": str(similar),
            "similarity": 0.93,
            "match_count": 5,
            "file_name": "prices-2025.pdf",
        }
    ]


async def test_list_filters_pages_and_totals() -> None:
    with scoped_app() as (app, world):
        jobs = [_job(world.corpus_a.id)]
        world.session.execute = AsyncMock(
            side_effect=[_result(scalar=41), _result(scalars=jobs), _result(rows=[])]
        )
        res = await call(
            app, "ck_a", "GET", "/api/v1/ingest/jobs?status=pending_review&offset=40&limit=1"
        )
        page_sql = str(world.session.execute.await_args_list[1].args[0])
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["count"], body["total"], body["offset"], body["limit"]) == (1, 41, 40, 1)
    assert body["items"][0]["file_name"] == "price-list.pdf"
    assert "ingest_jobs.status" in page_sql and "ingest_jobs.corpus_id IN" in page_sql


async def test_unknown_status_is_422() -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", "/api/v1/ingest/jobs?status=bogus")
    assert res.status_code == 422


@pytest.mark.parametrize("limit", [0, 101])
async def test_limit_bounds(limit) -> None:
    with scoped_app() as (app, _):
        res = await call(app, "admin", "GET", f"/api/v1/ingest/jobs?limit={limit}")
    assert res.status_code == 422
