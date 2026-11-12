"""The platform P&L (BIL6): what counts as billed and as collected, the AI cost estimate, the
currency conversion at the fixed rate, exempt customers, range rules, and who may ask. The SQL
(and the operator's own workspace being left out) runs on PostgreSQL in
`tests/integration/test_billing_statements_db.py`."""

import uuid
from datetime import date
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.billing import pnl
from callobuzz_rag.billing.pnl import Customer, LedgerGroup, build_rows, fold_groups
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

RATE = 83.5
A, B, C = uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)


def group(
    account: uuid.UUID, kind: str, amount: int, *, cash: int = 0, positive: bool | None = None
) -> LedgerGroup:
    return LedgerGroup(account, kind, amount > 0 if positive is None else positive, amount, cash)


def test_what_is_revenue_and_what_is_cash() -> None:
    groups = [
        group(A, "top_up", 100_000),  # cash in
        group(A, "refund", -5_000),  # cash out
        group(A, "subscription_charge", -10_000),  # billed
        group(A, "usage_charge", -30_000),  # billed
        group(A, "add_on_purchase", -4_000),  # an add-on paid from the balance: billed
        group(A, "add_on_purchase", 20_000, cash=0),  # a free credit pack: neither
        group(A, "add_on_purchase", 55_000, cash=50_000),  # paid outside: the price is cash
        group(A, "included_credit", 10_000),  # free credit
        group(A, "expiry", -2_000),  # credit that lapsed
        group(A, "adjustment", -300),  # shown on its own
        group(A, "adjustment", 100),
    ]
    assert fold_groups(groups) == {A: (44_000, 145_000, -200)}


def test_a_refund_is_not_negative_revenue_and_an_adjustment_is_not_revenue() -> None:
    folded = fold_groups([group(A, "refund", -700), group(A, "adjustment", 900)])
    assert folded == {A: (0, -700, 900)}


CUSTOMERS = [
    Customer(
        A,
        "Shree Labs",
        "INR",
        exempt=False,
        billed_minor=44_000,
        collected_minor=145_000,
        adjustments_minor=-200,
        ai_cost_usd_micro=2_000_000,  # $2.00
    ),
    Customer(
        B,
        "Pune Agency",
        "USD",
        exempt=False,
        billed_minor=1_250,  # $12.50
        collected_minor=0,  # postpaid: nothing paid yet
        ai_cost_usd_micro=3_000_000,  # $3.00
        unpriced_calls=4,
    ),
    Customer(
        C,
        "Internal Pilot",
        "INR",
        exempt=True,
        billed_minor=0,
        ai_cost_usd_micro=1_000_000,  # a customer on the Internal plan still costs money
    ),
]


def test_the_table_in_rupees() -> None:
    rows, totals = build_rows(CUSTOMERS, currency="INR", rate=RATE)
    by_name = {r.name: r for r in rows}
    shree, pune, pilot = by_name["Shree Labs"], by_name["Pune Agency"], by_name["Internal Pilot"]
    assert (shree.billed_minor, shree.collected_minor, shree.adjustments_minor) == (
        44_000,
        145_000,
        -200,
    )
    assert shree.ai_cost_minor == 16_700  # $2.00 at 83.5 = Rs 167.00
    assert shree.margin_minor == 27_300 and shree.margin_pct == 62.0
    assert shree.ai_cost_known is True
    # dollars to rupees: $12.50 = Rs 1,043.75 (104375 paise), $3.00 = Rs 250.50
    assert pune.billed_minor == 104_375
    assert pune.ai_cost_minor == 25_050 and pune.margin_minor == 79_325
    assert pune.ai_cost_known is False and pune.unpriced_calls == 4
    # the Internal plan is listed with billed 0, so its cost is a loss
    assert pilot.exempt and pilot.billed_minor == 0 and pilot.margin_minor == -8_350
    assert pilot.margin_pct is None
    assert totals.billed_minor == 44_000 + 104_375
    assert totals.ai_cost_minor == 16_700 + 25_050 + 8_350
    assert totals.margin_minor == totals.billed_minor - totals.ai_cost_minor
    assert totals.collected_minor == 145_000 and totals.adjustments_minor == -200
    assert totals.ai_cost_known is False and totals.unpriced_calls == 4
    assert [r.name for r in rows] == ["Pune Agency", "Shree Labs", "Internal Pilot"]  # by margin


def test_the_table_in_dollars() -> None:
    rows, totals = build_rows(CUSTOMERS, currency="USD", rate=RATE)
    by_name = {r.name: r for r in rows}
    # rupees to dollars: Rs 440.00 = $5.27 (5.2695...), Rs 1,450.00 = $17.37
    assert by_name["Shree Labs"].billed_minor == 527
    assert by_name["Shree Labs"].collected_minor == 1_737
    assert by_name["Shree Labs"].ai_cost_minor == 200
    assert (
        by_name["Pune Agency"].billed_minor == 1_250 and by_name["Pune Agency"].ai_cost_minor == 300
    )
    assert (
        by_name["Internal Pilot"].ai_cost_minor == 100
        and by_name["Internal Pilot"].billed_minor == 0
    )
    assert totals.billed_minor == 527 + 1_250
    assert totals.margin_minor == totals.billed_minor - totals.ai_cost_minor


def test_the_conversion_rounds_half_away_from_zero_and_is_symmetric() -> None:
    assert pnl.convert_minor(1_000, "INR", "INR", RATE) == 1_000
    assert pnl.convert_minor(-1_250, "USD", "INR", RATE) == -104_375
    assert pnl.convert_minor(-44_000, "INR", "USD", RATE) == -527
    assert pnl.convert_minor(0, "INR", "USD", RATE) == 0


def test_an_empty_range_is_an_empty_table() -> None:
    rows, totals = build_rows([], currency="INR", rate=RATE)
    assert rows == [] and totals.billed_minor == totals.margin_minor == 0
    assert totals.margin_pct is None and totals.ai_cost_known is True


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (None, None, (date(2026, 10, 1), date(2026, 10, 1))),
        ("2026-08", "2026-10", (date(2026, 8, 1), date(2026, 10, 1))),
        ("2026-08", None, (date(2026, 8, 1), date(2026, 10, 1))),
        (None, "2026-09", (date(2026, 9, 1), date(2026, 9, 1))),
        ("2024-11", "2026-10", (date(2024, 11, 1), date(2026, 10, 1))),  # exactly 24 months
    ],
)
def test_the_range_defaults_and_limits(start: str | None, end: str | None, expected: Any) -> None:
    assert pnl.parse_range(start, end, date(2026, 10, 4)) == expected


@pytest.mark.parametrize(
    ("start", "end", "code"),
    [
        ("2026-10", "2026-09", "invalid_range"),
        ("2024-10", "2026-10", "range_too_long"),  # 25 months
        ("2020-01", None, "range_too_long"),
    ],
)
def test_a_bad_range_is_refused(start: str, end: str | None, code: str) -> None:
    with pytest.raises(Exception) as err:
        pnl.parse_range(start, end, date(2026, 10, 4))
    assert err.value.code == code  # type: ignore[attr-defined]
    assert err.value.status_code == 422  # type: ignore[attr-defined]


# ------------------------------------------------------------------ the route


@pytest.fixture
def world() -> World:
    return World()


async def fake_gather(session: Any, first: date, last: date) -> list[Customer]:
    fake_gather.seen = (first, last)  # type: ignore[attr-defined]
    return CUSTOMERS


async def test_the_admin_reads_the_pnl(world: World) -> None:
    with auth_app(world) as app, patch.object(pnl, "gather", fake_gather):
        res = await req(
            app,
            "GET",
            "/api/v1/admin/billing/pnl?start=2026-08&end=2026-10&currency=USD",
            key=ADMIN,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["currency"] == "USD" and body["months"] == ["2026-08", "2026-09", "2026-10"]
    assert body["start"] == "2026-08" and body["end"] == "2026-10"
    assert body["rate"] == app.state.settings.usd_inr_rate
    assert {r["name"] for r in body["rows"]} == {"Shree Labs", "Pune Agency", "Internal Pilot"}
    rate = app.state.settings.usd_inr_rate
    assert body["totals"]["billed_minor"] == pnl.convert_minor(44_000, "INR", "USD", rate) + 1_250
    assert fake_gather.seen == (date(2026, 8, 1), date(2026, 10, 1))  # type: ignore[attr-defined]


async def test_the_currency_defaults_to_the_engines(world: World) -> None:
    with auth_app(world) as app, patch.object(pnl, "gather", fake_gather):
        res = await req(app, "GET", "/api/v1/admin/billing/pnl", key=ADMIN)
    assert res.status_code == 200 and res.json()["currency"] == "INR"
    assert len(res.json()["months"]) == 1


@pytest.mark.parametrize(
    ("query", "code"),
    [
        ("start=2026-10&end=2026-09", "invalid_range"),
        ("start=2024-01&end=2026-10", "range_too_long"),
    ],
)
async def test_a_bad_range_is_422(world: World, query: str, code: str) -> None:
    with auth_app(world) as app, patch.object(pnl, "gather", fake_gather):
        res = await req(app, "GET", f"/api/v1/admin/billing/pnl?{query}", key=ADMIN)
    assert res.status_code == 422 and res.json()["code"] == code


@pytest.mark.parametrize("query", ["start=2026-13", "end=oct", "currency=EUR"])
async def test_a_malformed_query_is_422(world: World, query: str) -> None:
    with auth_app(world) as app, patch.object(pnl, "gather", fake_gather):
        res = await req(app, "GET", f"/api/v1/admin/billing/pnl?{query}", key=ADMIN)
    assert res.status_code == 422


async def test_the_pnl_is_for_the_admin_only(world: World) -> None:
    with auth_app(world) as app, patch.object(pnl, "gather", fake_gather):
        anon = await req(app, "GET", "/api/v1/admin/billing/pnl")
    assert anon.status_code == 401


def test_an_exempt_customer_keeps_the_charges_the_ledger_recorded() -> None:
    # moved to the Internal plan after being charged: the past revenue is real
    moved = Customer(C, "Moved", "INR", exempt=True, billed_minor=5_000, ai_cost_usd_micro=0)
    rows, totals = build_rows([moved], currency="INR", rate=RATE)
    assert rows[0].exempt and rows[0].billed_minor == 5_000
    assert totals.billed_minor == 5_000


# P1 payments
def test_the_gateway_fee_is_a_cost_the_owner_never_sees_and_its_gst_is_shown_apart() -> None:
    customer = Customer(
        A,
        "Sharma Furniture",
        "INR",
        exempt=False,
        billed_minor=100_000,
        collected_minor=118_000,
        gateway_fee_minor=2_400,
        gateway_fee_tax_minor=432,
    )
    rows, totals = build_rows([customer], currency="INR", rate=RATE)
    [row] = rows
    assert (row.gateway_fee_minor, row.gateway_fee_tax_minor) == (2_400, 432)
    assert row.margin_minor == 100_000 - 2_400  # the fee is a cost; its GST is input credit
    assert (totals.gateway_fee_minor, totals.margin_minor) == (2_400, 97_600)
    usd, _ = build_rows([customer], currency="USD", rate=RATE)
    assert usd[0].gateway_fee_minor == pnl.convert_minor(2_400, "INR", "USD", RATE)


def test_a_customer_without_payments_has_no_fee() -> None:
    rows, totals = build_rows(
        [Customer(A, "Free", "INR", exempt=False, billed_minor=500)], currency="INR", rate=RATE
    )
    assert rows[0].gateway_fee_minor == 0 and totals.gateway_fee_tax_minor == 0
    assert rows[0].margin_minor == 500


# /P1 payments
