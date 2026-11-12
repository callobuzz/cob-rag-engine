"""Erasing a visitor's chat also erases their question from frozen value reports (TV4)."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.business.value_privacy import (
    delete_assistant_turns,
    normalise_question,
)
from callobuzz_rag.business.value_report import WorkspaceContext
from callobuzz_rag.db.models.assistant_turn import AssistantTurn
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.value import ValueReport
from tests.unit.accounts_fakes import FakeDB

CTX = WorkspaceContext("clinic", "Asia/Kolkata", "INR")
QUESTION = "my daughter has a cavity, can you see her saturday"
OCT = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
SEP = datetime(2026, 9, 12, 6, 0, tzinfo=UTC)


def _report(db: FakeDB, tenant: Tenant, month: str, **inputs: Any) -> ValueReport:
    from datetime import date

    data = {
        "inputs": {
            "top_questions": inputs.get("top", []),
            "unanswered": inputs.get("unanswered", []),
        }
    }
    return db.add(ValueReport(tenant_id=tenant.id, month=date.fromisoformat(month), data=data))


def _turn(db: FakeDB, tenant: Tenant, conversation: uuid.UUID, text: str, at: datetime, **kw: Any):
    defaults: dict[str, Any] = {
        "corpus_id": uuid.uuid4(),
        "channel": "whatsapp",
        "label": "business_question",
        "label_source": "rules",
        "language": "en",
        "script": "latin",
        "route": "grounded",
        "no_answer": False,
        "is_preview": False,
    }
    return db.add(
        AssistantTurn(
            tenant_id=tenant.id,
            conversation_id=conversation,
            question_masked=text,
            created_at=at,
            **{**defaults, **kw},
        )
    )


@pytest.fixture
def db() -> Any:
    with patch(
        "callobuzz_rag.business.value_privacy.workspace_context", AsyncMock(return_value=CTX)
    ):
        yield FakeDB()


def test_the_key_is_the_one_the_report_groups_by() -> None:
    assert normalise_question("  Do you take   CARDS ") == "do you take cards"


async def test_an_erased_question_leaves_the_frozen_report_of_its_month(db: FakeDB) -> None:
    tenant = db.add(Tenant(name="Clinic", status="active", api_key_hash=""))
    chat = uuid.uuid4()
    _turn(db, tenant, chat, QUESTION, OCT)
    _turn(db, tenant, chat, QUESTION, OCT, no_answer=True, route="no_answer")
    october = _report(
        db,
        tenant,
        "2026-10-01",
        top=[
            {"question": QUESTION, "count": 1},
            {"question": "root canal ka kitna charge hai?", "count": 17},
        ],
        unanswered=[{"question": QUESTION, "count": 1}],
    )
    september = _report(db, tenant, "2026-09-01", top=[{"question": QUESTION, "count": 3}])
    await delete_assistant_turns(db.session(), AssistantTurn.conversation_id.in_([chat]))
    assert [q["question"] for q in october.data["inputs"]["top_questions"]] == [
        "root canal ka kitna charge hai?"
    ]
    assert october.data["inputs"]["unanswered"] == []
    # another month's report is another visitor's business
    assert september.data["inputs"]["top_questions"] == [{"question": QUESTION, "count": 3}]
    assert db.all(AssistantTurn) == []


async def test_a_question_other_visitors_asked_too_only_loses_one_count(db: FakeDB) -> None:
    tenant = db.add(Tenant(name="Clinic", status="active", api_key_hash=""))
    chat = uuid.uuid4()
    _turn(db, tenant, chat, "Do you take  CARDS", OCT)
    report = _report(db, tenant, "2026-10-01", top=[{"question": "do you take cards", "count": 4}])
    await delete_assistant_turns(db.session(), AssistantTurn.conversation_id.in_([chat]))
    assert report.data["inputs"]["top_questions"] == [{"question": "do you take cards", "count": 3}]


async def test_other_workspaces_and_other_chats_are_untouched(db: FakeDB) -> None:
    ours = db.add(Tenant(name="Clinic", status="active", api_key_hash=""))
    theirs = db.add(Tenant(name="Shop", status="active", api_key_hash=""))
    chat, other_chat = uuid.uuid4(), uuid.uuid4()
    _turn(db, ours, chat, QUESTION, OCT)
    keep = _turn(db, ours, other_chat, "something else", OCT)
    _report(db, ours, "2026-10-01", top=[{"question": "something else", "count": 1}])
    others = _report(db, theirs, "2026-10-01", top=[{"question": QUESTION, "count": 2}])
    await delete_assistant_turns(db.session(), AssistantTurn.conversation_id.in_([chat]))
    assert others.data["inputs"]["top_questions"] == [{"question": QUESTION, "count": 2}]
    assert db.all(AssistantTurn) == [keep]


async def test_a_preview_or_non_question_turn_was_never_counted(db: FakeDB) -> None:
    tenant = db.add(Tenant(name="Clinic", status="active", api_key_hash=""))
    chat = uuid.uuid4()
    _turn(db, tenant, chat, "hello", OCT, label="smalltalk")
    report = _report(db, tenant, "2026-10-01", top=[{"question": "hello", "count": 5}])
    await delete_assistant_turns(db.session(), AssistantTurn.conversation_id.in_([chat]))
    assert report.data["inputs"]["top_questions"] == [{"question": "hello", "count": 5}]


def test_no_erasure_path_deletes_turns_without_forgetting_their_questions() -> None:
    """`delete_assistant_turns` is the only place that deletes assistant turns."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag"
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path.name != "value_privacy.py" and "delete(AssistantTurn)" in path.read_text("utf-8")
    ]
    assert offenders == []


async def test_the_daily_cleanup_deletes_reports_past_the_retention(db: FakeDB) -> None:
    from callobuzz_rag.accounts.cleanup import cleanup

    tenant = db.add(Tenant(name="Clinic", status="active", api_key_hash=""))
    old = _report(db, tenant, "2024-08-01", top=[{"question": QUESTION, "count": 1}])
    edge = _report(db, tenant, "2024-10-01")  # 24 months back: still listed and frozen
    recent = _report(db, tenant, "2026-09-01")
    counts = await cleanup(db.session(), now=datetime(2026, 10, 6, 4, 0, tzinfo=UTC))
    assert counts["value_reports"] == 1
    assert db.all(ValueReport) == [edge, recent] and old not in db.all(ValueReport)
