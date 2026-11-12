"""Money correctness across the billing modules (BIL7).

Seeded random interleavings of the operations a wallet sees (metered requests with replays,
minute posting, top-ups with replays, credit packs, expiry, subscriptions) driven over the pure
reference model (`tests/unit/billing_model.py`) and the real price rule (`charge_amount`):
after every step the ledger adds up (I1), what a customer can still spend is the balance less
what is owed (I5), every charge is posted, carried or pending (I6), a request id is charged
exactly once however often it is replayed, no currency is ever mixed, and a statement built at
any month boundary adds up and chains into the next one."""

import random
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from callobuzz_rag.billing.charging import NAMESPACE_BILLING, UsageEvent, charge_amount
from callobuzz_rag.billing.ledger import EntryType, available_minor_of
from callobuzz_rag.billing.money import (
    MICRO_PER_MINOR,
    CurrencyMismatchError,
    Money,
    fx_ppm,
)
from callobuzz_rag.billing.pricing import PricingFields, PricingOverrides, combine
from callobuzz_rag.billing.statements import ChargeGroup, Names, build_statement
from tests.unit.billing_model import WalletModel

SEQUENCES = 300
KB = uuid.uuid4()
MONTHS = [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]


def pricing_for(rng: random.Random, currency: str):  # type: ignore[no-untyped-def]
    mode = rng.choice(["per_question", "cost_plus", "included"])
    fields = PricingFields(
        currency=currency,  # type: ignore[arg-type]
        usage_mode=mode,  # type: ignore[arg-type]
        per_question_minor=rng.randint(1, 500),
        markup_bp=rng.choice([0, 1000, 2500, 10_000]),
        overage="allow",
    )
    return combine(fields, PricingOverrides(), currency=currency, exempt=False)  # type: ignore[arg-type]


def event(rng: random.Random, request_id: uuid.UUID) -> UsageEvent:
    return UsageEvent(
        request_id=request_id,
        tenant_id=uuid.uuid4(),
        corpus_id=KB,
        source="api",
        payer="operator",
        model="m",
        questions=rng.choice([0, 1, 1, 1]),
        cost_usd_micro=rng.randint(0, 50_000),
        cost_known=rng.random() > 0.1,
        questions_before=0,
        at=datetime(2026, 10, 4, tzinfo=UTC),
    )


class Sim:
    """One wallet over several months: the model, the request ids it has charged, and what
    each month's statement needs."""

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.currency = self.rng.choice(["INR", "USD"])
        self.fx = fx_ppm(84.0, self.currency)  # type: ignore[arg-type]
        self.pricing = pricing_for(self.rng, self.currency)
        self.wallet = WalletModel(self.currency)
        self.wallet.top_up(5_000, key="seed-0")
        # every request id the test made up -> the price of that one request. Written by the test
        # alone, once per id, never by the wallet model: it is the independent total to compare
        # the wallet's charges with.
        self.requests: dict[uuid.UUID, int] = {}
        self.pending: list[tuple[int, str, int]] = []  # (micro, source, answers)
        self.month_index = 0
        self.entries: dict[date, list[Any]] = {m: [] for m in MONTHS}
        self.charges: dict[date, list[ChargeGroup]] = {m: [] for m in MONTHS}
        self.opening: dict[date, int] = {}
        self.residue_start: dict[date, int] = {}
        self.residue_end: dict[date, int] = {}
        self.closing: dict[date, int] = {}
        self._seen_entries = 0
        self.opening[MONTHS[0]] = 0
        self.residue_start[MONTHS[0]] = 0

    @property
    def month(self) -> date:
        return MONTHS[self.month_index]

    def _flush_entries(self) -> None:
        """Entries the model has appended since last time belong to the current month."""
        for e in self.wallet.entries[self._seen_entries :]:
            self.entries[self.month].append(
                type(
                    "E",
                    (),
                    {
                        "seq": e.seq,
                        "entry_type": e.type.value,
                        "amount_minor": e.amount,
                        "memo_public": None,
                        "created_at": datetime(self.month.year, self.month.month, 5, tzinfo=UTC),
                    },
                )()
            )
        self._seen_entries = len(self.wallet.entries)

    def next_month(self) -> None:
        self._flush_entries()
        self.closing[self.month] = self.wallet.balance
        self.residue_end[self.month] = self.wallet.residue
        self.month_index += 1
        self.opening[self.month] = self.wallet.balance
        self.residue_start[self.month] = self.wallet.residue

    def record(self) -> None:
        """One new request, sent 1-4 times at once; and, now and then, an earlier request retried
        later (after other operations, possibly after its month was posted). Every send goes to
        the wallet, which must charge the id the first time and ignore it afterwards."""
        request_id = uuid.uuid4()
        e = event(self.rng, request_id)
        amount, _ = charge_amount(self.pricing, e, fx=self.fx, monthly_limit=None)
        self.requests[request_id] = amount
        sends = [self.wallet.record(request_id, amount) for _ in range(1 + self.rng.randint(0, 3))]
        assert sends == ["charged"] + ["duplicate"] * (len(sends) - 1)
        self.pending.append((amount, "api", 1))
        if len(self.requests) > 1 and self.rng.random() < 0.3:
            old = self.rng.choice(list(self.requests)[:-1])
            assert self.wallet.record(old, self.requests[old]) == "duplicate"

    def record_fee(self) -> None:
        """TV6: a pass-through fee for one WhatsApp message (a usage charge with no question, keyed
        by the message), sent to the wallet once or replayed; it must be charged once."""
        wamid = f"wamid.OUT{len(self.requests)}-{self.rng.randint(0, 10**9)}"
        request_id = uuid.uuid5(NAMESPACE_BILLING, f"wa-fee:{wamid}")
        amount = self.rng.randint(1, 600) * MICRO_PER_MINOR
        self.requests[request_id] = amount
        sends = [self.wallet.record(request_id, amount) for _ in range(1 + self.rng.randint(0, 2))]
        assert sends == ["charged"] + ["duplicate"] * (len(sends) - 1)
        self.pending.append((amount, "whatsapp_fee", 0))

    def post(self) -> None:
        pending, self.pending = self.pending, []
        before = len(self.wallet.entries)
        self.wallet.post()
        self._flush_entries()
        if len(self.wallet.entries) == before and not pending:
            return
        for amount, source, answers in pending:
            self.charges[self.month].append(
                ChargeGroup(KB, source, "m" if source == "api" else "", answers, amount)
            )

    def top_up(self) -> None:
        amount = self.rng.randint(1, 10**6)
        key = f"top-{len(self.wallet.entries)}-{self.rng.randint(0, 10**9)}"
        for _ in range(1 + self.rng.randint(0, 3)):  # a replayed top-up changes nothing
            self.wallet.top_up(amount, key=key)

    def grant(self) -> None:
        """Credit the wallet: half the time a plain add-on purchase, half the time credit that
        expires (a lot), the only way an expiry can happen."""
        if self.rng.random() < 0.5:
            self.wallet.apply("included", self.rng)
            return
        amount = self.rng.randint(1, 10**6)
        self.wallet._append(EntryType.ADD_ON_PURCHASE, amount, self.wallet._key("g"))

    def step(self) -> None:
        op = self.rng.choice(
            [
                "record",
                "record",
                "record",
                "fee",
                "post",
                "top_up",
                "grant",
                "expire",
                "subscription",
            ]
        )
        if op == "record":
            self.record()
        elif op == "fee":
            self.record_fee()
        elif op == "post":
            self.post()
        elif op == "top_up":
            self.top_up()
        elif op == "grant":
            self.grant()
        else:
            self.wallet.apply(op, self.rng)
        self._flush_entries()


def check_invariants(sim: Sim) -> None:
    w = sim.wallet
    assert w.balance == sum(e.amount for e in w.entries)  # I1
    assert [e.seq for e in w.entries] == list(range(1, len(w.entries) + 1))
    running = 0
    for e in w.entries:
        running += e.amount
        assert e.balance_after == running
    owed = w.residue + w.unposted_micro
    assert available_minor_of(w.balance, owed) == (w.balance * MICRO_PER_MINOR - owed) // (
        MICRO_PER_MINOR
    )  # I5
    assert w.charged_micro == -w.posted_usage_minor * MICRO_PER_MINOR + owed  # I6
    assert 0 <= w.residue < MICRO_PER_MINOR
    # each distinct request id charged once: the wallet charged exactly the sum of their prices
    assert w.charged_micro == sum(sim.requests.values())


@pytest.mark.parametrize("seed", range(SEQUENCES))
def test_balance_is_the_ledger_and_retries_never_double_charge(seed: int) -> None:
    sim = Sim(seed)
    for _ in range(sim.rng.randint(5, 80)):
        sim.step()
        check_invariants(sim)
    sim.post()
    check_invariants(sim)
    assert sim.wallet.unposted_micro == 0


def test_the_sequences_do_reach_expiry_and_replays() -> None:
    """The properties above only mean something if the sequences get to the interesting
    operations: credit that expires and then really expires, and requests retried later."""
    expiries = 0
    for seed in range(SEQUENCES):
        sim = Sim(seed)
        for _ in range(sim.rng.randint(5, 80)):
            sim.step()
        expiries += sum(e.type is EntryType.EXPIRY for e in sim.wallet.entries)
    assert expiries > 0


@pytest.mark.parametrize("seed", range(SEQUENCES))
def test_a_statement_at_any_month_boundary_adds_up_and_chains(seed: int) -> None:
    sim = Sim(seed)
    boundaries = sorted(sim.rng.sample(range(5, 60), 2))
    for i in range(61):
        if i in boundaries:
            sim.post()  # a month never starts with charges still unposted from the last
            sim.next_month()
        sim.step()
    sim.post()
    sim._flush_entries()
    sim.closing[sim.month] = sim.wallet.balance
    sim.residue_end[sim.month] = sim.wallet.residue
    previous_closing = 0
    for month in MONTHS[: sim.month_index + 1]:
        s = build_statement(
            sim.entries[month],
            sim.charges[month],
            opening=sim.opening[month],
            residue_start=sim.residue_start[month],
            residue_end=sim.residue_end[month],
            names=Names(corpora={KB: "KB"}),
            month=month,
            currency=sim.currency,  # type: ignore[arg-type]
        )
        assert s.opening_minor == previous_closing
        assert s.opening_minor + sum(line.amount_minor for line in s.lines) == s.closing_minor
        assert s.closing_minor == sim.closing[month]
        usage_micro = sum(r.amount_micro for r in s.usage_breakdown) + s.rounding_carried_micro
        assert usage_micro == -s.totals["usage_charge"] * MICRO_PER_MINOR
        previous_closing = s.closing_minor
    assert previous_closing == sim.wallet.balance


@pytest.mark.parametrize("seed", range(SEQUENCES))
def test_no_currency_is_ever_mixed(seed: int) -> None:
    rng = random.Random(seed)
    inr = Money(rng.randint(-(10**9), 10**9), "INR")
    usd = Money(rng.randint(-(10**9), 10**9), "USD")
    with pytest.raises(CurrencyMismatchError):
        inr + usd
    with pytest.raises(CurrencyMismatchError):
        usd - inr
    assert (inr + Money(5, "INR")).currency == "INR"
    assert (inr + Money.zero("INR")) == inr


# TV3 notice
def _renewal_dates(anchor: date, billing_day: int, until: date, period: str) -> list[date]:
    """Every renewal from `anchor` up to `until`, built the way the schedule builds them."""
    from callobuzz_rag.billing.subscriptions import add_period

    out = [anchor]
    while out[-1] <= until:
        out.append(add_period(out[-1], period, billing_day))
    return out


@pytest.mark.parametrize("seed", range(SEQUENCES))
def test_nobody_pays_a_raise_inside_the_period_it_was_announced_in(seed: int) -> None:
    """Random versions announced at random times with 30+ days of notice, random billing days
    and usage days: every charge's price is the version in force at that workspace's period
    start, and a workspace is never on a raised price before its first renewal on or after the
    raise's start date."""
    from datetime import timedelta

    from callobuzz_rag.billing.notice import (
        applies_from,
        current_period_start,
        ist_midnight,
        version_in_force,
    )
    from callobuzz_rag.db.models.billing_account import BillingAccount
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    rng = random.Random(seed)
    period = rng.choice(["monthly", "monthly", "yearly"])
    billing_day = rng.randint(1, 28)
    anchor = date(2025, rng.randint(1, 12), billing_day)
    versions = [PlanPricing(version=1, effective_at=None, is_current=False)]
    announced = date(2026, 1, 1)
    for number in range(2, rng.randint(2, 5) + 1):
        announced += timedelta(days=rng.randint(1, 120))
        starts = ist_midnight(announced + timedelta(days=rng.randint(30, 90)))
        versions.append(PlanPricing(version=number, effective_at=starts, is_current=False))
    horizon = date(2028, 12, 31)
    renewals = _renewal_dates(anchor, billing_day, horizon, period)
    for _ in range(40):
        day = date(2026, 1, 1) + timedelta(days=rng.randint(0, 900))
        upcoming = next(r for r in renewals if r > day)
        started = current_period_start(upcoming, day, period, billing_day)
        # the schedule's own view of the period: the latest renewal not after `day`
        assert started == max(r for r in renewals if r <= day)
        paying = version_in_force(versions, ist_midnight(started))
        assert paying is not None
        assert paying.effective_at is None or paying.effective_at <= ist_midnight(started)
        for row in versions:
            if row.effective_at is None or row.effective_at <= ist_midnight(day):
                continue
            account = BillingAccount(next_charge_on=upcoming, billing_day=billing_day)
            first = applies_from(account, row.effective_at, period)
            assert ist_midnight(first) >= row.effective_at
            if day < first:
                # still inside a period that began before the raise took effect
                assert ist_midnight(started) < row.effective_at
                assert paying.version != row.version
            else:
                assert ist_midnight(started) >= row.effective_at


def test_a_workspace_pays_the_old_price_until_its_own_renewal_after_the_start() -> None:
    """The two businesses of the TV3 plan: a raise starting 5 Nov reaches the one that renews
    on the 12th at its November renewal and the one that renews on the 25th at its own."""
    from callobuzz_rag.billing.notice import current_period_start, ist_midnight, version_in_force
    from callobuzz_rag.db.models.plan_pricing import PlanPricing

    old = PlanPricing(version=1, effective_at=None, is_current=False)
    new = PlanPricing(version=2, effective_at=ist_midnight(date(2026, 11, 5)), is_current=True)
    for renews, billing_day, usage_day, expected in [
        (date(2026, 11, 12), 12, date(2026, 11, 6), 1),  # period began 12 Oct
        (date(2026, 12, 12), 12, date(2026, 11, 13), 2),  # period began 12 Nov
        (date(2026, 11, 25), 25, date(2026, 11, 13), 1),  # period began 25 Oct
        (date(2026, 12, 25), 25, date(2026, 11, 26), 2),  # period began 25 Nov
    ]:
        started = current_period_start(renews, usage_day, "monthly", billing_day)
        got = version_in_force([old, new], ist_midnight(started))
        assert got is not None and got.version == expected, (renews, usage_day)


# /TV3 notice


@pytest.mark.parametrize("seed", range(SEQUENCES))
def test_a_change_of_period_length_never_reaches_a_period_already_paid(seed: int) -> None:
    """Yearly workspaces under a version that switches to monthly: the period start
    is never in the future and is the one the schedule paid, whichever length the newest
    version says."""
    from datetime import timedelta
    from types import SimpleNamespace

    from callobuzz_rag.billing.notice import ist_midnight, period_start

    rng = random.Random(seed)
    old, new = "yearly", "monthly"  # a longer old period shows as a future start under the new
    billing_day = rng.randint(1, 28)
    anchor = date(2025, rng.randint(1, 12), billing_day)
    renewals = _renewal_dates(anchor, billing_day, date(2029, 12, 31), old)
    for _ in range(40):
        day = date(2026, 1, 1) + timedelta(days=rng.randint(0, 900))
        upcoming = next(r for r in renewals if r > day)
        account = SimpleNamespace(next_charge_on=upcoming, billing_day=billing_day)
        now = datetime(day.year, day.month, day.day, 6, tzinfo=UTC)
        start = period_start(account, now, billing_period=new, alternatives=[new, old])  # type: ignore[arg-type]
        assert start <= ist_midnight(day)
        paid = max(r for r in renewals if r <= day)
        if (upcoming.year, upcoming.month) > (day.year + (day.month == 12), day.month % 12 + 1):
            # more than a month to the next charge: the monthly reading is impossible
            assert start == ist_midnight(paid)
