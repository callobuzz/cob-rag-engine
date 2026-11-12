"""Statement routes (BIL6): list and get for the super admin, the three formats, names that are
defused in a spreadsheet and escaped on a page, the not-found cases, and no markup anywhere.
The numbers are built by `tests/unit/test_billing_statements.py`; here the builder is a stand-in
that returns a fixed statement so the routes and the formats are what is under test. The owner's
scoping is in `tests/unit/test_scope_billing_routes.py`."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.billing import statements
from callobuzz_rag.billing.statements import ChargeGroup, Names, build_statement
from callobuzz_rag.db.models.billing_account import BillingAccount
from tests.unit.billing_fakes import fake_accounts
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req
from tests.unit.test_billing_statements import FIXED_ENTRIES

KB_NAME = '=HYPERLINK("http://evil.example","click")'
SCRIPT_NAME = "<script>alert(1)</script>"
FIRST = date(2026, 8, 1)
CURRENT = date(2026, 10, 1)


@pytest.fixture
def world() -> World:
    return World()


def canned(month: date, *, kb: str = "Clinic FAQ") -> statements.StatementData:
    names = Names(corpora={uuid.UUID(int=1): kb})
    return build_statement(
        [e for e in FIXED_ENTRIES],
        [ChargeGroup(uuid.UUID(int=1), "api", "gpt-4o-mini", 4, 3_000_000)],
        opening=50_000,
        residue_start=0,
        residue_end=0,
        names=names,
        month=month,
        live=month == CURRENT,
        now=datetime(2026, 10, 8, tzinfo=UTC),
    )


class Fakes:
    """What the routes call in place of the SQL half of `billing/statements.py`."""

    def __init__(self, *, kb: str = "Clinic FAQ") -> None:
        self.kb = kb
        self.asked: list[tuple[uuid.UUID, date]] = []

    async def statement_for(
        self, session: Any, account: BillingAccount, month: date, *, now: Any = None
    ) -> statements.StatementData:
        self.asked.append((account.tenant_id, month))
        if month > CURRENT or month < FIRST:
            raise statements.not_found()
        return canned(month, kb=self.kb)

    async def summaries(
        self, session: Any, account: BillingAccount, *, now: Any = None, limit: Any = None
    ) -> list[statements.MonthSummary]:
        return [
            statements.MonthSummary(CURRENT, 1_000, 2_000, True),
            statements.MonthSummary(date(2026, 9, 1), 500, 1_000, False),
        ]


@contextmanager
def app_for(world: World, fakes: Fakes | None = None, *, account: bool = True) -> Iterator[Any]:
    fakes = fakes or Fakes()
    if account:
        world.db.add(BillingAccount(tenant_id=world.shree.id, currency="INR", ledger_seq=3))
    with (
        auth_app(world) as app,
        fake_accounts(world.db),
        patch.object(statements, "statement_for", fakes.statement_for),
        patch.object(statements, "summaries", fakes.summaries),
    ):
        app.state.fakes = fakes
        yield app


def url(world: World, month: str = "", **query: str) -> str:
    base = f"/api/v1/admin/billing/accounts/{world.shree.id}/statements"
    path = f"{base}/{month}" if month else base
    return path + (("?" + "&".join(f"{k}={v}" for k, v in query.items())) if query else "")


async def test_the_admin_lists_a_customers_statements_newest_first(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world), key=ADMIN)
    assert res.status_code == 200, res.text
    assert res.json() == {
        "items": [
            {
                "month": "2026-10",
                "currency": "INR",
                "opening_minor": 1_000,
                "closing_minor": 2_000,
                "live": True,
            },
            {
                "month": "2026-09",
                "currency": "INR",
                "opening_minor": 500,
                "closing_minor": 1_000,
                "live": False,
            },
        ]
    }


async def test_a_customer_without_an_account_has_no_statements(world: World) -> None:
    with app_for(world, account=False) as app:
        listed = await req(app, "GET", url(world), key=ADMIN)
        one = await req(app, "GET", url(world, "2026-09"), key=ADMIN)
    assert listed.status_code == 200 and listed.json() == {"items": []}
    assert one.status_code == 404 and one.json()["code"] == "statement_not_found"


async def test_the_admin_reads_one_month_as_json(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-09"), key=ADMIN)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["workspace"] == "Shree Labs"
    assert body["month"] == "2026-09-01" and body["live"] is False
    assert body["opening_minor"] == 50_000
    assert [s["key"] for s in body["sections"]][:2] == ["subscription", "usage"]
    assert body["usage_breakdown"][0]["label"] == "Clinic FAQ · gpt-4o-mini · API"
    assert app.state.fakes.asked == [(world.shree.id, date(2026, 9, 1))]


async def test_the_current_month_is_live(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10"), key=ADMIN)
    assert res.json()["live"] is True


@pytest.mark.parametrize("month", ["2026-07", "2027-01"])
async def test_a_month_before_the_account_or_in_the_future_is_404(world: World, month: str) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, month), key=ADMIN)
    assert res.status_code == 404 and res.json()["code"] == "statement_not_found"


@pytest.mark.parametrize("month", ["2026-13", "2026-1", "last-month", "2026-00"])
async def test_a_malformed_month_is_422(world: World, month: str) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, month), key=ADMIN)
    assert res.status_code == 422


async def test_an_unknown_workspace_is_404(world: World) -> None:
    with app_for(world) as app:
        listed = await req(
            app, "GET", f"/api/v1/admin/billing/accounts/{uuid.uuid4()}/statements", key=ADMIN
        )
    assert listed.status_code == 404 and listed.json()["code"] == "tenant_not_found"


async def test_csv_is_a_download_with_the_workspace_in_its_name(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10", format="csv"), key=ADMIN)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"] == (
        'attachment; filename="statement-shree-labs-2026-10.csv"'
    )
    assert res.headers["cache-control"] == "no-store"
    lines = res.text.splitlines()
    assert lines[0] == "section,date,description,answers,amount,currency"
    assert "opening,2026-10-01,Opening balance,,500.00,INR" in lines
    assert any(line.startswith("closing,") and line.endswith("INR") for line in lines)
    # amounts are decimal strings in the currency's major unit, not floats
    assert "subscription,2026-10-05,Monthly plan,,-500.00,INR" in lines
    assert "usage_detail,,Clinic FAQ · gpt-4o-mini · API,4,3.000000,INR" in lines


async def test_a_name_that_is_a_spreadsheet_formula_is_defused(world: World) -> None:
    with app_for(world, Fakes(kb=KB_NAME)) as app:
        res = await req(app, "GET", url(world, "2026-10", format="csv"), key=ADMIN)
    assert "'=HYPERLINK" in res.text
    for line in res.text.splitlines():
        cells = line.split(",")
        assert not any(c.startswith("=") for c in cells), line


async def test_a_workspace_name_that_is_a_formula_is_defused_in_the_csv_too(
    world: World,
) -> None:
    world.shree.name = "=cmd|' /C calc'!A0"
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10", format="csv"), key=ADMIN)
    assert res.text.splitlines()[1].split(",")[2].startswith("'=")
    assert 'filename="statement-cmd-c-calc-a0-2026-10.csv"' in res.headers["content-disposition"]


async def test_html_is_a_locked_down_printable_page(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10", format="html"), key=ADMIN)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:"
    )
    body = res.text
    assert "Statement for October 2026" in body and "so far" in body
    assert "Opening balance" in body and "Closing balance" in body
    assert "@page" in body and "A4" in body  # the print stylesheet that makes the PDF
    assert "cob-rag-engine by Callobuzz" in body  # the footer is not optional
    assert "<script" not in body


async def test_html_escapes_names(world: World) -> None:
    world.shree.name = SCRIPT_NAME
    with app_for(world, Fakes(kb=SCRIPT_NAME)) as app:
        res = await req(app, "GET", url(world, "2026-09", format="html"), key=ADMIN)
    assert "<script>alert(1)</script>" not in res.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in res.text
    assert "so far" not in res.text  # a closed month is not "so far"


@pytest.mark.parametrize("fmt", ["json", "csv", "html"])
async def test_the_markup_and_the_operators_cost_never_appear(world: World, fmt: str) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10", format=fmt), key=ADMIN)
    body = res.text.lower()
    assert "markup" not in body
    assert "cost_usd" not in body and "ai cost" not in body.replace("priced at ai cost", "")


async def test_an_unknown_format_is_422(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", url(world, "2026-10", format="pdf"), key=ADMIN)
    assert res.status_code == 422


async def test_the_routes_need_the_admin(world: World) -> None:
    with app_for(world) as app:
        anon = await req(app, "GET", url(world))
        anon_one = await req(app, "GET", url(world, "2026-10"))
    assert anon.status_code == 401 and anon_one.status_code == 401


async def test_with_billing_off_the_routes_answer_404(world: World) -> None:
    with patch.dict(os.environ, {"RAG_BILLING_ENABLED": "false"}):
        billing_config.reset_billing_config_cache()
        try:
            with app_for(world) as app:
                res = await req(app, "GET", url(world), key=ADMIN)
        finally:
            billing_config.reset_billing_config_cache()
    assert res.status_code == 404 and res.json()["code"] == "billing_disabled"
