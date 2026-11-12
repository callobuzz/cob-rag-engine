import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from callobuzz_rag.conversation.leads.retention import purge_expired_leads

C1, C2 = uuid.uuid4(), uuid.uuid4()
T1, T2 = uuid.uuid4(), uuid.uuid4()
V1 = uuid.uuid4()


def _factory(session: MagicMock) -> MagicMock:
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=None)
    return factory


async def test_purge_deletes_expired_and_old_consents_and_audits() -> None:
    session = MagicMock()
    deleted = MagicMock()
    deleted.all.return_value = [(C1, T1, V1, None), (C1, T1, None, None), (C2, T2, None, None)]
    consents = MagicMock(rowcount=4)
    # handoff scrubs (with the lead, aged), erased_at stamp, leads, the WhatsApp chat lookup (by
    # conversation link, by chat), the turns' questions (leave the frozen value reports), turns,
    # widget chats, consents, ip scrub, captures
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(),
            MagicMock(),
            MagicMock(),
            deleted,
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            consents,
            MagicMock(rowcount=1),
            MagicMock(rowcount=2),
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    result = await purge_expired_leads(_factory(session), now=datetime(2026, 10, 14, tzinfo=UTC))
    assert result == {"leads": 3, "consents": 4, "captures": 2}
    sql = [str(c.args[0]) for c in session.execute.await_args_list]
    # summaries go before the lead delete (which only NULLs the links), by lead and by age
    assert "UPDATE handoffs SET summary_encrypted" in sql[0] and "retention_until" in sql[0]
    assert "UPDATE handoffs h SET summary_encrypted = NULL, page_url = NULL" in sql[1]
    assert "retention_days" in sql[1]
    assert "UPDATE lead_consents SET erased_at" in sql[2] and "retention_until" in sql[2]
    assert "DELETE FROM leads" in sql[3] and "retention_until" in sql[3]
    # the WhatsApp chat is looked up while the conversation links still exist
    assert "conversations.channel_thread_id" in sql[4] and "channel_threads" in sql[5]
    assert "assistant_turns.question_masked" in sql[6] and "SELECT" in sql[6]  # TV4
    assert "DELETE FROM assistant_turns" in sql[7]
    assert "DELETE FROM conversations" in sql[8] and "channel" in sql[8]
    # a year from the lead going (erased_at), the consent date only as a fallback
    assert "DELETE FROM lead_consents" in sql[9]
    assert "coalesce(lead_consents.erased_at, lead_consents.consented_at)" in sql[9]
    assert "UPDATE conversations SET assistant_state" in sql[11] and "lead_capture" in sql[11]
    audits = [c.args[0] for c in session.add.call_args_list]
    assert {a.details["count"] for a in audits} == {2, 1}
    assert all(a.action == "lead.retention_purged" for a in audits)
    assert all(a.actor_kind == "system" and a.actor_label == "lead retention" for a in audits)
    assert {a.tenant_id for a in audits} == {T1, T2}
    session.commit.assert_awaited_once()


async def test_nothing_expired_touches_no_chat_and_writes_no_audit() -> None:
    session = MagicMock()
    deleted = MagicMock()
    deleted.all.return_value = []
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(),
            MagicMock(),
            MagicMock(),
            deleted,
            MagicMock(rowcount=0),
            MagicMock(),
            MagicMock(rowcount=0),
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    result = await purge_expired_leads(_factory(session), now=datetime(2026, 10, 14, tzinfo=UTC))
    assert result == {"leads": 0, "consents": 0, "captures": 0}
    session.add.assert_not_called()
    # two handoff scrubs, stamp, leads, consents, ip scrub, captures: no chat deletes
    assert session.execute.await_count == 7


async def test_purge_stamps_consents_before_deleting_the_lead_of_730_days() -> None:
    session = MagicMock()
    deleted = MagicMock()
    deleted.all.return_value = []
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(),
            MagicMock(),
            MagicMock(),
            deleted,
            MagicMock(rowcount=0),
            MagicMock(),
            MagicMock(rowcount=0),
        ]
    )
    session.add = MagicMock()
    session.commit = AsyncMock()
    now = datetime(2026, 10, 14, tzinfo=UTC)
    await purge_expired_leads(_factory(session), now=now)
    stamp = session.execute.await_args_list[2].args[0]
    compiled = stamp.compile(dialect=postgresql.dialect())
    assert "UPDATE lead_consents SET erased_at" in str(compiled)
    assert now in compiled.params.values()
