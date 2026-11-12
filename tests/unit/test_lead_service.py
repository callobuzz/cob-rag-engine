import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from callobuzz_rag.api.schemas.leads import (
    LeadUpdateRequest,
    WidgetLeadRequest,
    WithdrawRequest,
)
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.conversation.leads.service import (
    LeadFilters,
    NewLead,
    _filter_clauses,
    create_lead,
    erase_lead,
    lead_fields,
    update_lead,
)
from callobuzz_rag.conversation.leads.validation import CleanLead, LeadValidationError
from callobuzz_rag.conversation.schema import AssistantConfig
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.lead import Lead
from callobuzz_rag.db.models.lead_consent import LeadConsent

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)
KEY = Fernet.generate_key().decode()
OLD = Fernet.generate_key().decode()
CRYPTO = LeadCrypto(FernetEncryptor(KEY))


def _new(**kw: object) -> NewLead:
    base: dict[str, Any] = {
        "corpus_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "widget_key_id": uuid.uuid4(),
        "channel": "widget",
        "clean": CleanLead(
            name="Priya Kulkarni",
            phone="+919876543210",
            email=None,
            custom={"test_name": "Lipid profile"},
        ),
        "language": "en",
        "page_url": "https://shree-labs.in/tests",
        "utm": {"utm_source": "google"},
        "trigger": "buying_intent",
        "source_label": "buying_intent",
        "tags": [],
        "callback_window": None,
        "sources_consulted": ["test-prep-guide.pdf"],
        "notice_text": "notice",
        "ip": "203.0.113.9",
    }
    base.update(kw)
    return NewLead(**base)


async def test_create_encrypts_hashes_and_records_consent() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    added: list[object] = []
    session.add = added.append
    raw = AssistantConfig().model_dump()
    raw["leads"]["consent"]["retention_days"] = 90
    config = AssistantConfig.model_validate(raw)
    lead = await create_lead(session, CRYPTO, config, _new(withdraw_hash="ab" * 32), now=NOW)
    assert isinstance(added[0], Lead) and isinstance(added[1], LeadConsent)
    assert "9876543210" not in lead.fields_encrypted
    assert lead_fields(CRYPTO, lead)["phone"] == "+919876543210"
    assert lead.phone_hash == CRYPTO.index_hash("+919876543210") and lead.email_hash is None
    assert lead.retention_until == NOW + timedelta(days=90)
    assert added[1].lead_id == lead.id and added[1].ip == "203.0.113.9"
    assert lead.withdraw_hash == "ab" * 32


def test_missing_keys_in_the_payload_read_as_none() -> None:
    lead = Lead(fields_encrypted=CRYPTO.encrypt_json({"phone": "+919876543210"}))
    assert lead_fields(CRYPTO, lead) == {
        "name": None,
        "phone": "+919876543210",
        "email": None,
        "custom": {},
    }


def test_filters_use_hashes_for_contact_search() -> None:
    clauses = _filter_clauses(
        CRYPTO, LeadFilters(q="98765 43210", status="new", date_from=date(2026, 10, 1))
    )
    rendered = " ".join(str(c) for c in clauses)
    assert "phone_hash" in rendered and "status" in rendered and "created_at" in rendered
    clauses = _filter_clauses(CRYPTO, LeadFilters(q="Priya@Shree-Labs.in"))
    assert "email_hash" in " ".join(str(c) for c in clauses)


def test_search_matches_rows_written_under_the_old_key_during_rotation() -> None:
    rotating = LeadCrypto(FernetEncryptor(f"{KEY},{OLD}"))
    old_hash = LeadCrypto(FernetEncryptor(OLD)).index_hash("+919876543210")
    (clause,) = _filter_clauses(rotating, LeadFilters(q="9876543210"))
    values = clause.right.value  # type: ignore[attr-defined]
    assert old_hash in values and rotating.index_hash("+919876543210") in values


def test_unparseable_search_matches_nothing_and_page_url_wildcards_are_escaped() -> None:
    (clause,) = _filter_clauses(CRYPTO, LeadFilters(q="not a number"))
    assert clause.right.value == ["-"]  # type: ignore[attr-defined]
    (clause,) = _filter_clauses(CRYPTO, LeadFilters(page_url="100%_off"))
    assert clause.right.value == "%100\\%\\_off%"  # type: ignore[attr-defined]
    assert clause.modifiers["escape"] == "\\"  # type: ignore[attr-defined]


async def test_update_status_notes_and_assignee_membership() -> None:
    lead = Lead(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status="new", notes_encrypted=None)
    member = uuid.uuid4()
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(user_id=member)
    session.execute = AsyncMock(return_value=result)
    await update_lead(
        session,
        CRYPTO,
        lead,
        {"status": "contacted", "notes": "Call at 6", "assignee_user_id": member},
        now=NOW,
    )
    assert lead.status == "contacted" and lead.contacted_at == NOW
    assert CRYPTO.decrypt_text(lead.notes_encrypted) == "Call at 6"
    result.scalar_one_or_none.return_value = None
    with pytest.raises(LeadValidationError) as exc:
        await update_lead(session, CRYPTO, lead, {"assignee_user_id": uuid.uuid4()}, now=NOW)
    assert exc.value.reason == "not_member"


async def test_contacted_at_is_set_once() -> None:
    lead = Lead(id=uuid.uuid4(), tenant_id=uuid.uuid4(), status="new")
    session = MagicMock()
    await update_lead(session, CRYPTO, lead, {"status": "contacted"}, now=NOW)
    await update_lead(session, CRYPTO, lead, {"status": "won"}, now=NOW + timedelta(days=1))
    assert lead.contacted_at == NOW and lead.status == "won"


async def test_erase_marks_consents_withdrawn_and_removes_the_widget_chat() -> None:
    lead = Lead(id=uuid.uuid4(), conversation_id=uuid.uuid4())
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.delete = AsyncMock()
    await erase_lead(session, lead, withdrawn=True, now=NOW)
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert "lead_consents" in statements[0] and "withdrawn_at" in statements[0]
    assert "erased_at" in statements[0]
    handoff = next(s for s in statements if "UPDATE handoffs" in s)
    assert "summary_encrypted" in handoff and "lead_id" in handoff and "conversation_id" in handoff
    assert any("DELETE FROM assistant_turns" in s for s in statements)
    assert any("DELETE FROM conversations" in s and "channel" in s for s in statements)
    session.delete.assert_awaited_once_with(lead)


async def test_erase_without_a_conversation_only_stamps_and_deletes_the_lead() -> None:
    lead = Lead(id=uuid.uuid4(), conversation_id=None)
    session = MagicMock()
    session.execute = AsyncMock()
    session.delete = AsyncMock()
    await erase_lead(session, lead, withdrawn=False, now=NOW)
    # the consent stamp (erased_at, no withdrawn_at), the handoff summary scrub by lead only,
    # and the delete: no chat statements
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert len(statements) == 2 and "erased_at" in statements[0]
    assert "UPDATE handoffs SET summary_encrypted" in statements[1]
    assert "handoffs.conversation_id" not in statements[1]
    assert "withdrawn_at" not in statements[0]
    session.delete.assert_awaited_once_with(lead)


def test_request_bodies_are_strict() -> None:
    form_id = "lf_" + "0" * 16
    ok = WidgetLeadRequest(session="ws_" + "a" * 20, form_id=form_id, fields={"phone": "1"})
    assert ok.consent is False and ok.website == ""
    for model, body in (
        (WidgetLeadRequest, {"session": "ws_" + "a" * 20, "form_id": form_id, "extra": 1}),
        (LeadUpdateRequest, {"status": "won", "phone": "x"}),
        (WithdrawRequest, {"token": "t" * 30, "x": 1}),
    ):
        with pytest.raises(ValidationError):
            model(**body)
    with pytest.raises(ValidationError):
        WidgetLeadRequest(session="ws_" + "a" * 20, form_id=form_id, fields={"a": "x" * 501})
    with pytest.raises(ValidationError):
        WidgetLeadRequest(session="ws_" + "a" * 20, form_id="bad", fields={})


# TV2 reference
async def test_a_lead_in_a_conversation_gives_it_a_reference() -> None:
    from unittest.mock import patch

    session = MagicMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    config = AssistantConfig.model_validate({"identity": {"business_name": "Shree Labs"}})
    new = _new()
    assign = AsyncMock(return_value="SL-4F7K")
    with patch("callobuzz_rag.conversation.handoff.reference.assign_ref", assign):
        await create_lead(session, CRYPTO, config, new, now=NOW)
    assign.assert_awaited_once_with(session, new.conversation_id, "SL")


async def test_no_conversation_no_reference_and_a_failure_never_blocks_the_lead() -> None:
    from unittest.mock import patch

    session = MagicMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    assign = AsyncMock(side_effect=RuntimeError("db"))
    with patch("callobuzz_rag.conversation.handoff.reference.assign_ref", assign):
        await create_lead(session, CRYPTO, AssistantConfig(), _new(conversation_id=None), now=NOW)
        assign.assert_not_awaited()
        lead = await create_lead(session, CRYPTO, AssistantConfig(), _new(), now=NOW)
    assert lead.id is not None


async def test_a_database_error_in_the_reference_never_loses_the_lead() -> None:
    from unittest.mock import patch

    from sqlalchemy.exc import DBAPIError

    nested = MagicMock()
    nested.__aenter__ = AsyncMock(return_value=None)
    nested.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.begin_nested = MagicMock(return_value=nested)
    boom = AsyncMock(side_effect=DBAPIError("select", {}, Exception("db")))
    with patch("callobuzz_rag.conversation.handoff.reference.assign_ref", boom):
        lead = await create_lead(session, CRYPTO, AssistantConfig(), _new(), now=NOW)
    assert lead.id is not None
    session.begin_nested.assert_called_once()
    nested.__aexit__.assert_awaited_once()
    assert nested.__aexit__.await_args.args[0] is DBAPIError


# /TV2 reference
