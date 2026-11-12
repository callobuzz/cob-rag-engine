"""The business card becomes a searchable, citable system document."""

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from callobuzz_rag.accounts.caps import _active_documents
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.ingest import RecordInput
from callobuzz_rag.api.services.system_documents import (
    refuse_system_document as _refuse_system_document,
)
from callobuzz_rag.api.services.system_documents import refuse_system_target
from callobuzz_rag.conversation.business_card import (
    BUSINESS_CARD_RECORD_ID,
    PendingCard,
    card_markdown,
    card_record,
    dispatch_business_card,
    sync_business_card,
)
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.db.models.document import Document
from callobuzz_rag.pipeline.records import TextRecord
from tests.unit.test_ingest_records import _orchestrator


def _config(**overrides: Any) -> AssistantConfig:
    data: dict[str, Any] = {
        "identity": {
            "business_name": "Shree Labs",
            "card": {
                "hours": [{"day": "mon", "open": "07:00", "close": "20:00"}],
                "address": "12 Karve Road, Kothrud, Pune 411038",
                "phone": "+91 98220 12345",
                "whatsapp": "9822012345",
                "email": "care@shree-labs.in",
                "service_areas": ["Kothrud", "Warje"],
                "payment_info": "UPI, cards and cash accepted",
            },
        }
    }
    data.update(overrides)
    return AssistantConfig.model_validate(data)


def _session(card_document_exists: bool = True) -> MagicMock:
    session = MagicMock()
    result = MagicMock()
    result.first.return_value = (uuid.uuid4(),) if card_document_exists else None
    session.execute = AsyncMock(return_value=result)
    return session


CORPUS = SimpleNamespace(id=uuid.uuid4(), qdrant_collection="corpus_abcd1234")


def test_markdown_lists_every_fact() -> None:
    text = card_markdown(_config())
    assert text is not None
    assert text.startswith("Shree Labs - business card")
    for fact in (
        "Monday 07:00",
        "12 Karve Road",
        "+919822012345",
        "care@shree-labs.in",
        "Kothrud, Warje",
        "UPI",
    ):
        assert fact in text
    # One paragraph of facts, so a chunk holds the whole card.
    assert "\n\n" not in text.split("\n\n", 1)[1].strip()


def test_no_facts_means_no_card_even_with_a_business_name() -> None:
    assert card_markdown(AssistantConfig()) is None
    named = AssistantConfig.model_validate({"identity": {"business_name": "Shree Labs"}})
    assert card_markdown(named) is None
    assert card_record(named) is None


def test_record_shape() -> None:
    record = card_record(_config())
    assert record is not None
    assert record.id == BUSINESS_CARD_RECORD_ID == "system:business-card"
    assert record.title == "Business card"
    assert record.metadata == {"system": "business_card"}


async def test_sync_only_when_what_is_searchable_changes() -> None:
    session = _session(card_document_exists=True)
    assert await sync_business_card(session, CORPUS, _config(), previous=_config()) is None
    pending = await sync_business_card(session, CORPUS, _config(), previous=None)
    assert pending is not None
    job = session.add.call_args.args[0]
    assert (job.mode, job.pending_new_documents, job.status, job.total_documents) == (
        "records",
        0,
        "queued",
        1,
    )
    assert job.id == pending.job_id
    assert pending.record.text == card_markdown(_config())


async def test_a_missing_card_document_is_re_synced_even_when_the_text_is_unchanged() -> None:
    session = _session(card_document_exists=False)
    pending = await sync_business_card(session, CORPUS, _config(), previous=_config())
    assert pending is not None and pending.record.id == BUSINESS_CARD_RECORD_ID


async def test_an_emptied_card_is_removed_not_replaced() -> None:
    session = _session()
    emptied = AssistantConfig.model_validate({"identity": {"business_name": "Shree Labs"}})
    pending = await sync_business_card(session, CORPUS, emptied, previous=_config())
    assert pending is not None and pending.remove
    assert pending.record is None and pending.job_id is None
    session.add.assert_not_called()  # no job, no placeholder text indexed


async def test_switching_the_assistant_off_removes_the_card_and_on_restores_it() -> None:
    session = _session()
    on = _config()
    off = _config(enabled=False)
    gone = await sync_business_card(session, CORPUS, off, previous=on)
    assert gone is not None and gone.remove
    back = await sync_business_card(session, CORPUS, on, previous=off)
    assert back is not None and back.record is not None
    assert back.record.text == card_markdown(on)


async def test_dispatching_a_removal_deletes_the_card_document() -> None:
    doc = Document(external_id=BUSINESS_CARD_RECORD_ID)
    doc.id = uuid.uuid4()
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = doc
    session.execute = AsyncMock(return_value=result)
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    store = MagicMock()
    store.delete_by_filter = AsyncMock()
    with patch(
        "callobuzz_rag.api.services.document_removal.dual_write_collection",
        new=AsyncMock(return_value="migr"),
    ):
        await dispatch_business_card(session, CORPUS, PendingCard(remove=True), store)
    filters = [c.args for c in store.delete_by_filter.call_args_list]
    assert filters == [
        ("corpus_abcd1234", {"document_id": str(doc.id)}),
        ("migr", {"document_id": str(doc.id)}),
    ]
    session.delete.assert_awaited_once_with(doc)
    session.commit.assert_awaited_once()


async def test_ingest_routes_refuse_to_overwrite_the_card() -> None:
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = BUSINESS_CARD_RECORD_ID
    session.execute = AsyncMock(return_value=result)
    with pytest.raises(APIError) as exc:
        await refuse_system_target(session, uuid.uuid4(), uuid.uuid4())
    assert (exc.value.status_code, exc.value.code) == (409, "system_document")
    result.scalar_one_or_none.return_value = "crm-1"
    await refuse_system_target(session, uuid.uuid4(), uuid.uuid4())
    await refuse_system_target(session, uuid.uuid4(), None)


async def test_nothing_to_remove_means_nothing_queued() -> None:
    session = _session(card_document_exists=False)
    empty = AssistantConfig()
    assert await sync_business_card(session, CORPUS, empty, previous=None) is None
    assert await sync_business_card(session, CORPUS, empty, previous=empty) is None
    session.add.assert_not_called()


async def test_dispatch_queues_the_records_task() -> None:
    session = _session()
    pending = await sync_business_card(session, CORPUS, _config(), previous=None)
    assert pending is not None
    with patch("callobuzz_rag.worker.tasks.ingest_records_task.delay") as delay:
        await dispatch_business_card(session, CORPUS, pending)
    kwargs = delay.call_args.kwargs
    assert kwargs["records"][0]["id"] == "system:business-card"
    assert kwargs["qdrant_collection"] == "corpus_abcd1234"
    assert kwargs["job_id"] == str(pending.job_id)


async def test_a_refused_dispatch_fails_the_job_instead_of_leaving_it_queued() -> None:
    session = _session()
    pending = await sync_business_card(session, CORPUS, _config(), previous=None)
    assert pending is not None
    job = session.add.call_args.args[0]
    session.get = AsyncMock(return_value=job)
    with (
        patch(
            "callobuzz_rag.worker.tasks.ingest_records_task.delay",
            side_effect=ConnectionError("broker down"),
        ),
        patch(
            "callobuzz_rag.api.routes.ingest._fail_undispatched", new_callable=AsyncMock
        ) as failed,
    ):
        await dispatch_business_card(session, CORPUS, pending)
    failed.assert_awaited_once()
    assert failed.await_args.args[1] is job


def test_customer_records_cannot_use_the_system_prefix() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        RecordInput(id="system:business-card", text="fake card")
    assert RecordInput(id="faq-1", text="ok").id == "faq-1"


def test_document_cap_ignores_system_documents() -> None:
    sql = str(_active_documents(uuid.uuid4()).compile(compile_kwargs={"literal_binds": True}))
    assert "NOT LIKE 'system:%%'" in sql or "NOT LIKE 'system:%'" in sql


def test_document_routes_refuse_system_documents() -> None:
    card = Document(id=uuid.uuid4(), corpus_id=uuid.uuid4(), external_id="system:business-card")
    other = Document(id=uuid.uuid4(), corpus_id=uuid.uuid4(), external_id="faq-1")
    plain = Document(id=uuid.uuid4(), corpus_id=uuid.uuid4(), external_id=None)
    _refuse_system_document(other, plain)
    with pytest.raises(APIError) as exc:
        _refuse_system_document(other, card)
    assert (exc.value.status_code, exc.value.code) == (409, "system_document")


async def test_the_worker_counts_no_allowance_for_system_records_and_never_strips_them() -> None:
    corpus_id = uuid.uuid4()
    orch, _session_mock, _store = _orchestrator([])
    orch._ensure_document_room = AsyncMock()  # type: ignore[method-assign]
    orch._boilerplate_scope = AsyncMock(return_value=(True, None))  # type: ignore[method-assign]
    orch._strip_boilerplate = AsyncMock()  # type: ignore[method-assign]
    card = TextRecord(id=BUSINESS_CARD_RECORD_ID, text="Business card\n\n- Phone: +919822012345")
    out = await orch.ingest_records([card], corpus_id, uuid.uuid4())
    assert out.status == "completed", out.error
    assert orch._ensure_document_room.await_args.args[2] == 0
    orch._strip_boilerplate.assert_not_awaited()

    orch2, _s2, _st2 = _orchestrator([])
    orch2._ensure_document_room = AsyncMock()  # type: ignore[method-assign]
    await orch2.ingest_records(
        [card, TextRecord(id="faq-1", text="An ordinary record.")], corpus_id, uuid.uuid4()
    )
    assert orch2._ensure_document_room.await_args.args[2] == 1
