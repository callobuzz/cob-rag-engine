# tests/unit/test_conv_sessions.py
"""C1: one opaque token per widget visitor chat; the engine stores only its hash."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from callobuzz_rag.conversation.sessions import (
    hash_session,
    new_session_token,
    resolve_widget_session,
    widget_conversation,
)
from callobuzz_rag.conversation.turns import record_turn
from callobuzz_rag.conversation.types import Channel
from callobuzz_rag.db.models import AssistantTurn, Conversation

NOW = datetime(2026, 10, 5, 6, 0, tzinfo=UTC)
CORPUS, KEY = uuid.uuid4(), uuid.uuid4()


def test_token_shape_and_hash() -> None:
    token, digest = new_session_token()
    assert token.startswith("ws_") and len(token) > 40
    assert digest == hashlib.sha256(token.encode()).hexdigest() == hash_session(token)
    assert new_session_token()[0] != token


def _session(found: object) -> AsyncMock:
    s = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = found
    s.execute = AsyncMock(return_value=result)
    s.add = MagicMock()
    return s


async def test_no_token_creates_a_widget_conversation() -> None:
    s = _session(None)
    conv, token = await widget_conversation(
        s,
        corpus_id=CORPUS,
        widget_key_id=KEY,
        token=None,
        ttl_hours=24,
        page_url="https://shree-labs.in/tests",
        utm={"utm_source": "google"},
        now=NOW,
    )
    assert isinstance(conv, Conversation) and token and token.startswith("ws_")
    assert conv.channel == "widget" and conv.widget_key_id == KEY
    assert conv.widget_session_hash == hash_session(token) and conv.utm == {"utm_source": "google"}
    s.add.assert_called_once_with(conv)


async def test_live_token_continues() -> None:
    existing = SimpleNamespace(
        updated_at=NOW - timedelta(hours=2), corpus_id=CORPUS, widget_key_id=KEY, page_url=None
    )
    conv, token = await widget_conversation(
        _session(existing),
        corpus_id=CORPUS,
        widget_key_id=KEY,
        token="ws_" + "a" * 43,
        ttl_hours=24,
        page_url="https://x.in/p",
        utm=None,
        now=NOW,
    )
    assert conv is existing and token is None and conv.page_url == "https://x.in/p"


async def test_expired_or_foreign_token_starts_over() -> None:
    stale = SimpleNamespace(
        updated_at=NOW - timedelta(hours=25), corpus_id=CORPUS, widget_key_id=KEY, page_url=None
    )
    conv, token = await widget_conversation(
        _session(stale),
        corpus_id=CORPUS,
        widget_key_id=KEY,
        token="ws_" + "a" * 43,
        ttl_hours=24,
        page_url=None,
        utm=None,
        now=NOW,
    )
    assert conv is not stale and token is not None
    other_key = SimpleNamespace(
        updated_at=NOW, corpus_id=CORPUS, widget_key_id=uuid.uuid4(), page_url=None
    )
    conv, token = await widget_conversation(
        _session(other_key),
        corpus_id=CORPUS,
        widget_key_id=KEY,
        token="ws_" + "b" * 43,
        ttl_hours=24,
        page_url=None,
        utm=None,
        now=NOW,
    )
    assert conv is not other_key and token is not None


async def test_resolve_finds_a_live_session_and_never_creates() -> None:
    live = SimpleNamespace(
        updated_at=NOW - timedelta(hours=1), corpus_id=CORPUS, widget_key_id=KEY, page_url=None
    )
    s = _session(live)
    found = await resolve_widget_session(
        s, widget_key_id=KEY, token="ws_" + "a" * 43, ttl_hours=24, now=NOW
    )
    assert found is live
    s.add.assert_not_called()


async def test_resolve_refuses_missing_expired_or_foreign() -> None:
    assert (
        await resolve_widget_session(
            _session(None), widget_key_id=KEY, token=None, ttl_hours=24, now=NOW
        )
        is None
    )
    stale = SimpleNamespace(
        updated_at=NOW - timedelta(hours=30), corpus_id=CORPUS, widget_key_id=KEY, page_url=None
    )
    assert (
        await resolve_widget_session(
            _session(stale), widget_key_id=KEY, token="ws_" + "a" * 43, ttl_hours=24, now=NOW
        )
        is None
    )
    foreign = SimpleNamespace(
        updated_at=NOW, corpus_id=CORPUS, widget_key_id=uuid.uuid4(), page_url=None
    )
    assert (
        await resolve_widget_session(
            _session(foreign), widget_key_id=KEY, token="ws_" + "a" * 43, ttl_hours=24, now=NOW
        )
        is None
    )


async def test_record_turn_adds_row() -> None:
    s = AsyncMock()
    s.add = MagicMock()
    row = await record_turn(
        s,
        corpus_id=CORPUS,
        tenant_id=uuid.uuid4(),
        conversation_id=None,
        message_id=None,
        query_id=uuid.uuid4(),
        channel=Channel.WIDGET,
        assistant_version=2,
        label="smalltalk",
        label_source="rules",
        label_confidence=1.0,
        language="en",
        script="latin",
        route="smalltalk",
        topic_id=None,
        top_score=None,
        gate_passed=None,
        claims_removed=0,
        claims_flagged=0,
        rules_fired=[],
        offers=[],
        no_answer=False,
        question_masked="hi",
        is_preview=False,
        latency_ms=3,
        timings={"total": 3},
    )
    assert isinstance(row, AssistantTurn) and row.channel == "widget"
    s.add.assert_called_once_with(row)
    s.flush.assert_awaited()
