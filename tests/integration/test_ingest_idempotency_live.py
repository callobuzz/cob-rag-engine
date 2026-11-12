"""Upload idempotency keys on real PostgreSQL: unique per corpus, a retry gets its job.

The key was unique across every corpus (a clash with another tenant's upload, and a 409
that revealed it) and a retry got a 409 instead of its original job (phase10_005).
Requires Docker.
"""

import asyncio
import hashlib
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from tests.integration.conftest import new_tenant_id

pytestmark = pytest.mark.integration


def _upload_request(corpus: Corpus) -> MagicMock:
    request = MagicMock()
    request.state.corpus = corpus
    request.state.is_admin = False
    request.state.tenant = None
    request.app.state.settings = MagicMock(
        s3_bucket="b", review_expiry_days=7, max_upload_bytes=50 * 1024 * 1024
    )
    return request


async def _upload(factory, corpus: Corpus, key: str, content: bytes):  # type: ignore[no-untyped-def]
    from callobuzz_rag.api.routes.ingest import upload_document

    file = MagicMock(content_type="text/plain", filename="a.txt")
    file.read = AsyncMock(return_value=content)
    async with factory() as s:
        return await upload_document(
            request=_upload_request(corpus),
            file=file,
            corpus_id=None,
            idempotency_key=key,
            mode="auto",
            target_document_id=None,
            auto_confirm=False,
            metadata=None,
            session=s,
        )


@pytest.fixture(autouse=True)
def _no_storage_or_worker() -> Iterator[None]:
    """Patched once per test, never per upload: concurrent uploads each entering and leaving
    their own `patch` restore the real storage while the others are still running."""
    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage") as storage,
        patch("callobuzz_rag.worker.tasks.ingest_document_task"),
    ):
        storage.return_value.upload = AsyncMock()
        yield


async def _corpora(factory, n: int) -> list[Corpus]:  # type: ignore[no-untyped-def]
    async with factory() as s:
        tenant_id = await new_tenant_id(s)
        corpora = [
            Corpus(name=f"idem-{uuid.uuid4().hex[:8]}", tenant_id=tenant_id) for _ in range(n)
        ]
        s.add_all(corpora)
        await s.commit()
        return corpora


async def _jobs(factory, key: str) -> list[IngestJob]:  # type: ignore[no-untyped-def]
    async with factory() as s:
        rows = await s.execute(select(IngestJob).where(IngestJob.idempotency_key == key))
        return list(rows.scalars())


async def test_idempotency_keys_are_scoped_to_their_corpus(db_engine) -> None:
    from fastapi import HTTPException

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    a, b = await _corpora(factory, 2)
    key = f"key-{uuid.uuid4()}"

    first = await _upload(factory, a, key, b"report v1")
    retry = await _upload(factory, a, key, b"report v1")
    other_corpus = await _upload(factory, b, key, b"someone else's file")
    with pytest.raises(HTTPException) as err:
        await _upload(factory, a, key, b"a different file")

    assert retry.job_id == first.job_id
    assert other_corpus.job_id != first.job_id
    assert err.value.status_code == 409
    jobs = await _jobs(factory, key)
    assert sorted(str(j.corpus_id) for j in jobs) == sorted([str(a.id), str(b.id)])
    stored = next(j for j in jobs if j.corpus_id == a.id).review_metadata["content_sha256"]
    assert stored == hashlib.sha256(b"report v1").hexdigest()
    async with factory() as s:  # the shared test database is walked back by other tests
        await s.execute(delete(Corpus).where(Corpus.id.in_([a.id, b.id])))
        await s.commit()


async def test_concurrent_uploads_with_one_key_make_one_job(db_engine) -> None:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    (corpus,) = await _corpora(factory, 1)
    key = f"key-{uuid.uuid4()}"

    responses = await asyncio.gather(*(_upload(factory, corpus, key, b"same") for _ in range(4)))

    assert len({r.job_id for r in responses}) == 1
    assert len(await _jobs(factory, key)) == 1


async def test_the_key_is_unique_per_corpus_only(db_engine) -> None:
    async with db_engine.connect() as conn:
        names = set(
            (
                await conn.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'ingest_jobs'")
                )
            ).scalars()
        )
    assert "uq_ingest_jobs_corpus_idempotency_key" in names
    assert "ingest_jobs_idempotency_key_key" not in names
