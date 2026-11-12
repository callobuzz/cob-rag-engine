"""Monthly statements (BIL6): they add up to the ledger, the usage breakdown plus the carried
rounding equals the posted usage exactly, names and notes read well, and the monthly snapshot
writes each closed month once. The SQL side runs on PostgreSQL in
`tests/integration/test_billing_statements_db.py`."""

import random
import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.billing import statements
from callobuzz_rag.billing.ledger import EntryType
from callobuzz_rag.billing.money import MICRO_PER_MINOR
from callobuzz_rag.billing.posting import post_batch
from callobuzz_rag.billing.statements import (
    ChargeGroup,
    Names,
    build_statement,
    month_range,
    next_month,
    previous_month,
)
from callobuzz_rag.db.models.billing_account import BillingAccount
from callobuzz_rag.db.models.billing_statement import BillingStatement
from tests.unit.accounts_fakes import FakeDB

KB_A, KB_B, KB_C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
WIDGET_ID = "11111111-2222-3333-4444-555555555555"
NAMES = Names(
    corpora={KB_A: "Clinic FAQ", KB_B: "Pricing", KB_C: "COB"},
    widget_keys={WIDGET_ID: "a1f2"},
)
SOURCES = ["api", "stream", "conversation", f"widget:{WIDGET_ID}", "widget:gone", "cob", "setup"]
MODELS = ["gpt-4o-mini", "claude-haiku", ""]


def at(month: date, day: int = 5) -> datetime:
    return datetime(month.year, month.month, day, 10, 0, tzinfo=UTC)


def entry(seq: int, kind: EntryType, amount: int, month: date, memo: str | None = None) -> Any:
    return SimpleNamespace(
        seq=seq,
        entry_type=kind.value,
        amount_minor=amount,
        memo_public=memo,
        created_at=at(month),
    )


class Wallet:
    """Three months of random wallet life, built the way the real posting builds it: usage
    charges are posted in batches with a carried remainder, a batch below one minor unit writes
    no entry."""

    def __init__(self, seed: int, months: int = 3) -> None:
        rng = random.Random(seed)
        self.months = month_range(date(2026, 8, 1), date(2026, 8 + months - 1, 1))
        self.entries: dict[date, list[Any]] = {m: [] for m in self.months}
        self.charges: dict[date, list[ChargeGroup]] = {m: [] for m in self.months}
        self.residue_start: dict[date, int] = {}
        self.residue_end: dict[date, int] = {}
        self.opening: dict[date, int] = {}
        self.closing: dict[date, int] = {}
        residue, balance, seq = 0, 0, 0
        for month in self.months:
            self.opening[month] = balance
            self.residue_start[month] = residue
            for _ in range(rng.randint(0, 14)):
                op = rng.choice(["top_up", "usage", "usage", "sub", "included", "adjust", "refund"])
                seq += 1
                if op == "usage":
                    amounts = [rng.randint(0, 9_000) for _ in range(rng.randint(1, 5))]
                    for amount in amounts:
                        self.charges[month].append(
                            ChargeGroup(
                                rng.choice([KB_A, KB_B, KB_C, uuid.uuid4(), None]),
                                rng.choice(SOURCES),
                                rng.choice(MODELS),
                                1,
                                amount,
                                unpriced=rng.choice([0, 0, 1]),
                                fx_ppm=rng.choice([0, 83_500_000]),
                            )
                        )
                    minor, residue = post_batch(amounts, residue)
                    if minor:
                        self.entries[month].append(
                            entry(seq, EntryType.USAGE_CHARGE, -minor, month)
                        )
                        balance -= minor
                    continue
                amount = rng.randint(1, 500_000)
                kind, signed = {
                    "top_up": (EntryType.TOP_UP, amount),
                    "sub": (EntryType.SUBSCRIPTION_CHARGE, -amount),
                    "included": (EntryType.INCLUDED_CREDIT, amount),
                    "adjust": (EntryType.ADJUSTMENT, rng.choice([-1, 1]) * amount),
                    "refund": (EntryType.REFUND, -amount),
                }[op]
                self.entries[month].append(entry(seq, kind, signed, month))
                balance += signed
            self.residue_end[month] = residue
            self.closing[month] = balance


@pytest.mark.parametrize("seed", range(200))
def test_statement_adds_up(seed: int) -> None:
    w = Wallet(seed)
    for month in w.months:
        s = build_statement(
            w.entries[month],
            w.charges[month],
            opening=w.opening[month],
            residue_start=w.residue_start[month],
            residue_end=w.residue_end[month],
            names=NAMES,
            month=month,
        )
        assert s.opening_minor + sum(line.amount_minor for line in s.lines) == s.closing_minor
        assert s.closing_minor == w.closing[month]
        assert sum(sec.total_minor for sec in s.sections) == sum(
            line.amount_minor for line in s.lines
        )
        usage_micro = sum(r.amount_micro for r in s.usage_breakdown) + s.rounding_carried_micro
        assert usage_micro == -s.totals["usage_charge"] * MICRO_PER_MINOR
        assert s.totals["usage_charge"] == sum(
            e.amount_minor for e in w.entries[month] if e.entry_type == "usage_charge"
        )
        # the many posted batches are one line, not one per minute
        assert sum(1 for line in s.lines if line.entry_type == "usage_charge") <= 1


def test_months_chain_into_each_other() -> None:
    w = Wallet(7)
    previous = None
    for month in w.months:
        s = build_statement(
            w.entries[month],
            w.charges[month],
            opening=w.opening[month],
            residue_start=w.residue_start[month],
            residue_end=w.residue_end[month],
            names=NAMES,
            month=month,
        )
        if previous is not None:
            assert s.opening_minor == previous.closing_minor
        previous = s


FIXED_ENTRIES = [
    entry(1, EntryType.SUBSCRIPTION_CHARGE, -50_000, date(2026, 10, 1), "Monthly plan"),
    entry(2, EntryType.INCLUDED_CREDIT, 20_000, date(2026, 10, 1), "Included credit"),
    entry(3, EntryType.TOP_UP, 100_000, date(2026, 10, 2), "Top-up (UPI)"),
    entry(4, EntryType.USAGE_CHARGE, -300, date(2026, 10, 3)),
    entry(5, EntryType.USAGE_CHARGE, -250, date(2026, 10, 4)),
    entry(6, EntryType.ADD_ON_PURCHASE, 10_000, date(2026, 10, 4), "Credit pack"),
    entry(7, EntryType.ADJUSTMENT, -500, date(2026, 10, 5), "Goodwill"),
    entry(8, EntryType.REFUND, -1_000, date(2026, 10, 6), "Refund"),
    entry(9, EntryType.EXPIRY, -2_000, date(2026, 10, 7), "Expired credit"),
]
FIXED_CHARGES = [
    ChargeGroup(KB_A, f"widget:{WIDGET_ID}", "gpt-4o-mini", 40, 3_000_000),
    ChargeGroup(KB_A, "api", "gpt-4o-mini", 20, 1_500_000),
    ChargeGroup(None, "api", "gpt-4o-mini", 2, 1_000),
    ChargeGroup(KB_C, "cob", "claude-haiku", 3, 0, unpriced=3),
]


def fixed() -> statements.StatementData:
    return build_statement(
        FIXED_ENTRIES,
        FIXED_CHARGES,
        opening=50_000,
        residue_start=0,
        residue_end=0,
        names=NAMES,
        month=date(2026, 10, 1),
        now=datetime(2026, 10, 8, tzinfo=UTC),
    )


def test_sections_and_labels() -> None:
    s = fixed()
    assert [sec.key for sec in s.sections] == [
        "subscription",
        "usage",
        "add_ons",
        "top_ups",
        "refunds",
        "adjustments",
        "included_credit",
        "expiry",
    ]
    assert s.usage_breakdown[0].label == "Clinic FAQ · gpt-4o-mini · Website widget (key …a1f2)"
    labels = [r.label for r in s.usage_breakdown]
    assert "Clinic FAQ · gpt-4o-mini · API" in labels
    assert "Deleted knowledge base · gpt-4o-mini · API" in labels
    assert "COB · claude-haiku" in labels  # the COB source label repeats the name: said once
    assert "price unknown" in s.notes[0]
    assert s.unpriced_count == 3
    usage = next(sec for sec in s.sections if sec.key == "usage")
    assert [line.description for line in usage.lines] == ["Usage: 65 answers"]
    assert usage.total_minor == -550
    assert s.answers == 65


def test_a_widget_key_that_is_gone_keeps_a_plain_name() -> None:
    s = build_statement(
        [],
        [ChargeGroup(KB_B, "widget:gone", "m", 1, 10)],
        opening=0,
        residue_start=0,
        residue_end=0,
        names=NAMES,
        month=date(2026, 10, 1),
    )
    assert s.usage_breakdown[0].label == "Pricing · m · Website widget"


def test_the_rate_note_names_the_fixed_rate_for_inr_only() -> None:
    groups = [ChargeGroup(KB_A, "api", "m", 1, 500, fx_ppm=83_500_000)]
    inr = build_statement(
        [], groups, opening=0, residue_start=0, residue_end=0, names=NAMES, month=date(2026, 10, 1)
    )
    usd = build_statement(
        [],
        groups,
        opening=0,
        residue_start=0,
        residue_end=0,
        names=NAMES,
        month=date(2026, 10, 1),
        currency="USD",
    )
    assert inr.notes == ["Usage priced at AI cost is converted at ₹83.50 per US$1."]
    assert usd.notes == []


def test_nothing_the_customer_must_not_see_is_in_the_document() -> None:
    body = fixed().model_dump_json().lower()
    assert "markup" not in body
    assert "cost_usd" not in body


def test_an_empty_month_is_a_statement_too() -> None:
    s = build_statement(
        [], [], opening=1_234, residue_start=7, residue_end=7, names=NAMES, month=date(2026, 9, 1)
    )
    assert s.opening_minor == s.closing_minor == 1_234
    assert s.lines == [] and s.sections == [] and s.rounding_carried_micro == 0


def test_the_document_survives_the_json_round_trip() -> None:
    s = fixed()
    again = statements.StatementData.model_validate(s.model_dump(mode="json"))
    assert again == s


def test_month_arithmetic() -> None:
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert previous_month(date(2027, 1, 1)) == date(2026, 12, 1)
    assert previous_month(date(2026, 10, 1)) == date(2026, 9, 1)
    assert month_range(date(2026, 11, 1), date(2027, 2, 1)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]


# ------------------------------------------------------------------ the monthly snapshot


async def test_snapshot_is_idempotent() -> None:
    db = FakeDB()
    account = db.add(
        BillingAccount(tenant_id=uuid.uuid4(), currency="INR", ledger_seq=3, balance_minor=0)
    )
    empty = db.add(BillingAccount(tenant_id=uuid.uuid4(), currency="INR", ledger_seq=0))
    built: list[tuple[uuid.UUID, date]] = []

    async def build(session: Any, acct: Any, month: date, **kw: Any) -> statements.StatementData:
        built.append((acct.id, month))
        return build_statement(
            [entry(1, EntryType.TOP_UP, 100, month)],
            [],
            opening=0,
            residue_start=0,
            residue_end=0,
            names=NAMES,
            month=month,
        )

    async def months_for(session: Any, acct: Any, now: datetime) -> list[date]:
        return month_range(date(2026, 8, 1), statements.month_of(now))

    run = {"build": build, "months_for": months_for}
    first = await statements.snapshot_closed_months(
        db.factory(), now=datetime(2026, 10, 15, tzinfo=UTC), **run
    )
    again = await statements.snapshot_closed_months(
        db.factory(), now=datetime(2026, 10, 15, tzinfo=UTC), **run
    )
    assert (first, again) == (2, 0)  # August and September; October is still open
    rows = db.all(BillingStatement)
    assert sorted(r.month for r in rows) == [date(2026, 8, 1), date(2026, 9, 1)]
    assert {r.account_id for r in rows} == {account.id}  # no entries ever: nothing to freeze
    assert empty.id not in {r.account_id for r in rows}
    assert len(built) == 2
    assert rows[0].data["opening_minor"] == 0 and rows[0].closing_minor == 100

    later = await statements.snapshot_closed_months(
        db.factory(), now=datetime(2026, 11, 2, 2, 30, tzinfo=UTC), **run
    )
    assert later == 1 and len(db.all(BillingStatement)) == 3  # October, once


async def test_a_failing_account_does_not_stop_the_others() -> None:
    db = FakeDB()
    bad = db.add(BillingAccount(tenant_id=uuid.uuid4(), currency="INR", ledger_seq=1))
    good = db.add(BillingAccount(tenant_id=uuid.uuid4(), currency="INR", ledger_seq=1))

    async def build(session: Any, acct: Any, month: date, **kw: Any) -> statements.StatementData:
        if acct.id == bad.id:
            raise RuntimeError("boom")
        return build_statement(
            [], [], opening=0, residue_start=0, residue_end=0, names=NAMES, month=month
        )

    async def months_for(session: Any, acct: Any, now: datetime) -> list[date]:
        return [date(2026, 9, 1)]

    written = await statements.snapshot_closed_months(
        db.factory(),
        now=datetime(2026, 10, 2, tzinfo=UTC),
        build=build,
        months_for=months_for,
    )
    assert written == 1
    assert [r.account_id for r in db.all(BillingStatement)] == [good.id]


def test_the_page_carries_its_own_content_security_policy() -> None:
    from callobuzz_rag.accounts.settings_store import Branding

    html = statements.render_html(fixed(), "Clinic", Branding())
    # the dashboard writes this page into a blank tab (dashboard origin): the policy must live in
    # the document, not only in the response header
    assert 'http-equiv="Content-Security-Policy"' in html
    assert "default-src 'none'" in html


async def test_snapshot_task_runs_the_schedule_before_posting_and_freezing() -> None:
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.worker import billing_tasks

    calls: list[str] = []

    async def step(name: str, value: Any = None) -> Any:
        calls.append(name)
        return value

    engine = SimpleNamespace(dispose=AsyncMock())
    with (
        patch("callobuzz_rag.billing.config.billing_enabled", return_value=True),
        patch("callobuzz_rag.db.engine.create_engine", return_value=engine),
        patch("callobuzz_rag.db.engine.create_session_factory", return_value=object()),
        patch("callobuzz_rag.billing.subscriptions.run_due", lambda *a, **k: step("schedule", [])),
        patch("callobuzz_rag.billing.posting.post_pending", lambda *a, **k: step("post")),
        patch(
            "callobuzz_rag.billing.statements.snapshot_closed_months",
            lambda *a, **k: step("freeze", 2),
        ),
    ):
        out = await billing_tasks._snapshot_statements()
    assert calls == ["schedule", "post", "freeze"] and out == {"written": 2}


async def test_a_catch_up_charge_for_a_frozen_month_is_booked_in_the_current_month() -> None:
    from callobuzz_rag.billing.subscriptions import _period_for

    db = FakeDB()
    account = db.add(BillingAccount(tenant_id=uuid.uuid4(), currency="INR"))
    db.add(
        BillingStatement(
            account_id=account.id,
            tenant_id=account.tenant_id,
            month=date(2026, 9, 1),
            currency="INR",
            opening_minor=0,
            closing_minor=0,
            data={},
        )
    )
    now = datetime(2026, 10, 1, 2, 30, tzinfo=UTC)
    async with db.factory()() as session:
        # September is frozen: its late charge lands in October, so September's closing still
        # equals October's opening
        assert await _period_for(session, account.id, date(2026, 9, 30), now) == date(2026, 10, 1)
        # October is open: the charge keeps its own month
        assert await _period_for(session, account.id, date(2026, 10, 1), now) == date(2026, 10, 1)
