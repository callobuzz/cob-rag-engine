"""`business.value.fetch_inputs` on PostgreSQL (TV4). Needs Docker (skipped with the integration
suite when it is down).

Two knowledge bases of one workspace and one of another, seeded turns, leads and handoffs:
previews, spam and other workspaces never count; the month follows the business's time zone.
"""

import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

from callobuzz_rag.business.value import fetch_inputs, month_bounds
from tests.integration.conftest import _async_url
from tests.integration.test_accounts_flow import _alembic

pytestmark = pytest.mark.integration

OCT = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)


async def _tenant(s: AsyncSession, name: str) -> uuid.UUID:
    return (
        await s.execute(
            text(
                "INSERT INTO tenants (name, api_key_hash, status, plan_id) "
                "SELECT :n, '', 'active', id FROM plans WHERE is_default RETURNING id"
            ),
            {"n": name},
        )
    ).scalar_one()


async def _kb(s: AsyncSession, tenant: uuid.UUID) -> uuid.UUID:
    return (
        await s.execute(
            text("INSERT INTO corpora (name, tenant_id) VALUES ('KB', :t) RETURNING id"),
            {"t": tenant},
        )
    ).scalar_one()


async def _system_kb(s: AsyncSession, tenant: uuid.UUID) -> uuid.UUID:
    return (
        await s.execute(
            text(
                "INSERT INTO corpora (name, tenant_id, is_system) VALUES ('COB', :t, true) "
                "RETURNING id"
            ),
            {"t": tenant},
        )
    ).scalar_one()


async def _conversation(s: AsyncSession, kb: uuid.UUID) -> uuid.UUID:
    return (
        await s.execute(
            text("INSERT INTO conversations (corpus_id) VALUES (:c) RETURNING id"), {"c": kb}
        )
    ).scalar_one()


async def _turn(
    s: AsyncSession,
    kb: uuid.UUID,
    tenant: uuid.UUID,
    *,
    conversation: uuid.UUID | None = None,
    label: str = "business_question",
    route: str = "grounded",
    question: str = "Root canal ka kitna charge hai?",
    office_open: bool | None = None,
    channel: str = "widget",
    preview: bool = False,
    no_answer: bool = False,
    latency_ms: int = 1000,
    at: datetime = OCT,
) -> None:
    await s.execute(
        text(
            "INSERT INTO assistant_turns (corpus_id, tenant_id, conversation_id, channel, label, "
            "label_source, language, script, route, question_masked, is_preview, no_answer, "
            "office_open, latency_ms, created_at) VALUES (:kb, :t, :c, :channel, :label, 'rules', "
            "'en', 'latin', :route, :q, :preview, :na, :office, :lat, :at)"
        ),
        {
            "kb": kb,
            "t": tenant,
            "c": conversation,
            "label": label,
            "route": route,
            "q": question,
            "preview": preview,
            "na": no_answer,
            "office": office_open,
            "channel": channel,
            "lat": latency_ms,
            "at": at,
        },
    )


async def _lead(
    s: AsyncSession,
    kb: uuid.UUID,
    tenant: uuid.UUID,
    *,
    qualification: str | None,
    status: str = "new",
    callback: dict[str, Any] | None = None,
) -> uuid.UUID:
    return (
        await s.execute(
            text(
                "INSERT INTO leads (corpus_id, tenant_id, channel, fields_encrypted, trigger, "
                "status, qualification, callback_window, retention_until, created_at) VALUES "
                "(:kb, :t, 'widget', 'x', 'form', :status, :q, CAST(:cb AS jsonb), "
                "now() + interval '30 days', :at) RETURNING id"
            ),
            {
                "kb": kb,
                "t": tenant,
                "status": status,
                "q": qualification,
                "cb": json.dumps(callback) if callback is not None else None,
                "at": OCT,
            },
        )
    ).scalar_one()


async def _handoff(
    s: AsyncSession,
    kb: uuid.UUID,
    tenant: uuid.UUID,
    channel: str,
    conversation: uuid.UUID | None = None,
    lead: uuid.UUID | None = None,
) -> None:
    await s.execute(
        text(
            "INSERT INTO handoffs (corpus_id, tenant_id, conversation_id, channel, reason, "
            "lead_id, created_at) VALUES (:kb, :t, :c, :ch, 'asked', :lead, :at)"
        ),
        {"kb": kb, "t": tenant, "c": conversation, "ch": channel, "lead": lead, "at": OCT},
    )


def test_inputs_follow_the_definitions() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")

        async def go() -> None:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as s:
                    ours, theirs = (
                        await _tenant(s, "Shree Dental Clinic"),
                        await _tenant(s, "Other"),
                    )
                    kb1, kb2, other_kb = (
                        await _kb(s, ours),
                        await _kb(s, ours),
                        await _kb(s, theirs),
                    )
                    c1, c2 = await _conversation(s, kb1), await _conversation(s, kb2)
                    # answered questions, one after hours, across both knowledge bases
                    await _turn(s, kb1, ours, conversation=c1, office_open=True, latency_ms=900)
                    await _turn(s, kb1, ours, conversation=c1, office_open=False)
                    await _turn(s, kb2, ours, conversation=c2, route="verbatim", latency_ms=3000)
                    # not answered by the assistant on its own: not counted as answered
                    await _turn(
                        s,
                        kb1,
                        ours,
                        conversation=c1,
                        route="no_answer",
                        no_answer=True,
                        question="Do you take   CARDS",
                    )
                    await _turn(
                        s,
                        kb1,
                        ours,
                        conversation=c1,
                        route="no_answer",
                        no_answer=True,
                        question="do you take cards",
                    )
                    await _turn(
                        s,
                        kb1,
                        ours,
                        conversation=c1,
                        label="smalltalk",
                        route="smalltalk",
                        question="hello",
                    )
                    await _turn(s, kb1, ours, conversation=c1, route="handoff")
                    # previews and another workspace never count
                    await _turn(s, kb1, ours, conversation=c1, preview=True)
                    await _turn(s, other_kb, theirs)
                    # the workspace asking COB about the dashboard is not visitor traffic
                    cob_kb = await _system_kb(s, ours)
                    await _turn(s, cob_kb, ours, channel="cob", question="how do I add an article?")
                    # leads: spam out, qualified/partial split, callback = a booking
                    await _lead(s, kb1, ours, qualification="qualified")
                    await _lead(s, kb1, ours, qualification="qualified", status="spam")
                    await _lead(
                        s,
                        kb2,
                        ours,
                        qualification="partial",
                        callback={"day": "mon", "from": "10:00", "to": "12:00"},
                    )
                    await _lead(s, other_kb, theirs, qualification="qualified")
                    await _handoff(s, kb1, ours, "booking")
                    await _handoff(s, kb1, ours, "whatsapp")
                    # one visitor tapping WhatsApp then Call is one handoff
                    await _handoff(s, kb2, ours, "whatsapp", conversation=c2)
                    await _handoff(s, kb2, ours, "call", conversation=c2)
                    await _handoff(s, other_kb, theirs, "booking")
                    await s.commit()

                    start, end = month_bounds(date(2026, 10, 1), "Asia/Kolkata")
                    got = await fetch_inputs(s, ours, start, end)
            finally:
                await engine.dispose()

            assert got.conversations == 2
            assert got.questions == 6  # grounded x2, verbatim, no_answer x2, the handoff route
            assert got.answered == 3
            assert got.answered_after_hours == 1
            assert (got.leads_total, got.leads_qualified, got.leads_partial) == (2, 1, 1)
            assert got.handoffs == 3  # booking, whatsapp, and the two taps of one conversation
            assert got.bookings == 1 + 1  # a booking handoff + the lead with a call-back window
            assert got.top_questions[0] == ("root canal ka kitna charge hai?", 4)
            assert got.unanswered == [("do you take cards", 2)]  # normalised, counted together
            assert got.median_first_reply_ms is not None

        asyncio.run(go())


def test_the_month_follows_the_business_time_zone() -> None:
    """A turn at 2026-09-30 19:00 UTC is 00:30 IST on 1 October."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")

        async def go() -> tuple[int, int, int]:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as s:
                    tenant = await _tenant(s, "Shree Dental Clinic")
                    kb = await _kb(s, tenant)
                    # a visitor's turn always belongs to a conversation (they are what is counted)
                    conv = await _conversation(s, kb)
                    await _turn(
                        s,
                        kb,
                        tenant,
                        conversation=conv,
                        at=datetime(2026, 9, 30, 19, 0, tzinfo=UTC),
                    )
                    await s.commit()
                    out = []
                    for tz, month in (
                        ("Asia/Kolkata", date(2026, 10, 1)),
                        ("Asia/Kolkata", date(2026, 9, 1)),
                        ("UTC", date(2026, 9, 1)),
                    ):
                        start, end = month_bounds(month, tz)
                        out.append((await fetch_inputs(s, tenant, start, end)).conversations)
                    return out[0], out[1], out[2]
            finally:
                await engine.dispose()

        october_ist, september_ist, september_utc = asyncio.run(go())
        assert (october_ist, september_ist, september_utc) == (1, 0, 1)


def test_no_traffic_gives_zeros_and_no_median() -> None:
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")

        async def go() -> Any:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as s:
                    tenant = await _tenant(s, "Shree Dental Clinic")
                    await s.commit()
                    start, end = month_bounds(date(2026, 10, 1), "Asia/Kolkata")
                    return await fetch_inputs(s, tenant, start, end)
            finally:
                await engine.dispose()

        got = asyncio.run(go())
        assert got.conversations == 0 and got.median_first_reply_ms is None
        assert got.top_questions == [] and got.unanswered == []


def test_testing_traffic_unrated_leads_and_callback_pairs() -> None:
    """The owner's own tests are not visitors, a workspace without qualification rules still
    has leads that count, and one "call me back" form is valued once."""
    with PostgresContainer("postgres:16-alpine") as pg:
        url = _async_url(pg)
        _alembic(url, "head")

        async def go() -> Any:
            engine = create_async_engine(url)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with factory() as s:
                    tenant = await _tenant(s, "Shree Dental Clinic")
                    kb = await _kb(s, tenant)
                    conv = await _conversation(s, kb)
                    # the onboarding Try step and the Playground: 15 questions, no conversation
                    for channel in ("stream", "api", "playground"):
                        for _ in range(5):
                            await _turn(s, kb, tenant, channel=channel)
                    # one real visitor on the widget
                    await _turn(s, kb, tenant, conversation=conv, channel="widget")
                    # no rules: leads are not rated and still count; spam never does
                    await _lead(s, kb, tenant, qualification=None)
                    await _lead(s, kb, tenant, qualification=None, status="spam")
                    await _lead(s, kb, tenant, qualification="unqualified")
                    # a callback form: a lead (counts, not rated) and its handoff, valued once
                    cb = {"day": "mon", "from": "10:00", "to": "12:00"}
                    paired = await _lead(s, kb, tenant, qualification=None, callback=cb)
                    await _handoff(s, kb, tenant, "callback", conversation=conv, lead=paired)
                    # a callback whose lead does not count is still a request to talk to a person
                    weak = await _lead(s, kb, tenant, qualification="partial", callback=cb)
                    await _handoff(s, kb, tenant, "callback", lead=weak)
                    await s.commit()
                    start, end = month_bounds(date(2026, 10, 1), "Asia/Kolkata")
                    return await fetch_inputs(s, tenant, start, end)
            finally:
                await engine.dispose()

        got = asyncio.run(go())
        assert got.conversations == 1 and got.questions == 1 and got.answered == 1
        assert got.leads_total == 4
        assert (got.leads_qualified, got.leads_unrated) == (2, 2)  # two unrated, none qualified
        assert got.handoffs == 1  # the callback whose lead is only partial
        assert got.bookings == 2  # both callback windows
