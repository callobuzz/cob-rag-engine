"""pipeline/records_queue.queue_records: the shared add-path for text records."""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.ingest import IngestRecordsRequest
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.records import TextRecord
from callobuzz_rag.pipeline.records_queue import queue_records

pytestmark = pytest.mark.usefixtures("unlimited_plan")


def _session() -> tuple[AsyncMock, uuid.UUID]:
    session = AsyncMock()
    session.add = MagicMock()
    job_id = uuid.uuid4()
    session.refresh = AsyncMock(side_effect=lambda j: setattr(j, "id", job_id))
    return session, job_id


def _corpus() -> Any:
    return MagicMock(id=uuid.uuid4(), tenant_id=uuid.uuid4(), qdrant_collection="corpus_c1")


async def test_commits_job_and_dispatches_task() -> None:
    session, job_id = _session()
    corpus = _corpus()
    body = IngestRecordsRequest(records=[{"id": "faq-1", "title": "Hours", "text": "10 to 7"}])
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        job = await queue_records(session, corpus, body.records, mode="records")
    assert job.id == job_id
    added = session.add.call_args.args[0]
    assert isinstance(added, IngestJob) and added.mode == "records" and added.total_documents == 1
    session.commit.assert_awaited()
    kwargs = task.delay.call_args.kwargs
    assert kwargs["corpus_id"] == str(corpus.id) and kwargs["job_id"] == str(job_id)
    assert kwargs["qdrant_collection"] == "corpus_c1"
    assert kwargs["records"][0]["id"] == "faq-1"


async def test_accepts_text_record_dataclasses() -> None:
    session, _ = _session()
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        await queue_records(
            session, _corpus(), [TextRecord(id="article:1", text="Root canal", title="RCT")]
        )
    assert task.delay.call_args.kwargs["records"][0]["title"] == "RCT"


async def test_customer_cannot_use_a_system_id() -> None:
    session, _ = _session()
    with pytest.raises(APIError) as exc:
        await queue_records(session, _corpus(), [TextRecord(id="system:cob/x", text="t")])
    assert exc.value.code == "reserved_record_id"
    session.add.assert_not_called()


async def test_platform_write_may_use_a_system_id() -> None:
    session, _ = _session()
    with patch("callobuzz_rag.worker.tasks.ingest_records_task"):
        await queue_records(
            session, _corpus(), [TextRecord(id="system:cob/x", text="t")], allow_system=True
        )
    session.add.assert_called_once()


async def test_only_new_ids_count_against_the_document_limit() -> None:
    session, _ = _session()
    known = MagicMock()
    known.scalar.return_value = 1  # one of the two ids already is a document
    session.execute = AsyncMock(return_value=known)
    seen: list[int] = []

    async def fake_room(_s: Any, _tenant: Any, adds: Any) -> int:
        seen.append(await adds())
        return seen[-1]

    with (
        patch("callobuzz_rag.pipeline.records_queue.check_document_room", fake_room),
        patch("callobuzz_rag.worker.tasks.ingest_records_task"),
    ):
        await queue_records(
            session, _corpus(), [TextRecord(id="a", text="x"), TextRecord(id="b", text="y")]
        )
    assert seen == [1]
    assert session.add.call_args.args[0].pending_new_documents == 1


async def test_queue_down_is_a_503_and_the_job_is_failed() -> None:
    session, _ = _session()
    with patch("callobuzz_rag.worker.tasks.ingest_records_task") as task:
        task.delay.side_effect = RuntimeError("broker gone")
        with pytest.raises(HTTPException) as exc:
            await queue_records(session, _corpus(), [TextRecord(id="a", text="x")])
    assert exc.value.status_code == 503
    job = session.add.call_args.args[0]
    assert job.status == "failed"


@pytest.mark.parametrize("prefix", ["article:", "faq:", "system:"])
def test_api_records_cannot_use_the_dashboards_or_the_engines_ids(prefix: str) -> None:
    from pydantic import ValidationError

    from callobuzz_rag.api.schemas.ingest import RecordInput

    with pytest.raises(ValidationError) as exc:
        RecordInput(id=f"{prefix}0b1c", text="Root canal starts at 4,500 rupees.")
    assert "reserved" in str(exc.value)
    assert RecordInput(id="product-12", text="t").id == "product-12"
