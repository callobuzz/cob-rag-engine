"""P5 T6: monthly add-ons renew from the balance.

The ledger stand-in records the entries and refuses a key it has seen (I4); the real SQL runs on
PostgreSQL in `tests/integration/test_saas_plans_db.py`.
"""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from callobuzz_rag.billing import addon_renewals, addons, ledger
from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.db.models.add_on import AddOnGrant
from tests.unit.billing_fakes import fake_accounts, stored_pricing
from tests.unit.test_auth_routes import World

NOW = datetime(2026, 11, 1, 0, 30, tzinfo=UTC)
PRICE = 49_900


@pytest.fixture
def world() -> World:
    w = World()
    w.db.add(stored_pricing(w.db.default_plan, currency="INR"))
    return w


class Books:
    """The ledger as the renewal sees it: entries by key, the balance a test sets."""

    def __init__(self) -> None:
        self.entries: dict[str, ledger.NewEntry] = {}
        self.available = 1_000_00
        self.hook = AsyncMock()

    async def append(self, session: Any, account_id: Any, new: Any, **kw: Any) -> Any:
        results = []
        for entry in new:
            created = entry.idempotency_key not in self.entries
            self.entries.setdefault(entry.idempotency_key, entry)
            results.append(ledger.AppendResult(uuid.uuid4(), len(self.entries), 0, created))
        return results

    async def available_minor(self, session: Any, account: Any) -> int:
        return self.available


@contextmanager
def books_for(world: World, *, billing: bool = True) -> Iterator[Books]:
    books = Books()
    env = {"RAG_BILLING_ENABLED": "true" if billing else "false"}
    with (
        patch.dict(os.environ, env),
        fake_accounts(world.db),
        patch.object(ledger, "append_entries", books.append),
        patch.object(ledger, "available_minor", books.available_minor),
        patch("callobuzz_rag.billing.hooks.after_balance_change", books.hook),
    ):
        billing_config.reset_billing_config_cache()
        try:
            yield books
        finally:
            billing_config.reset_billing_config_cache()


def grant_for(world: World, **fields: Any) -> AddOnGrant:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "tenant_id": world.shree.id,
        "add_on_id": uuid.uuid4(),
        "kind": "feature_unlock",
        "name": "Use your own AI key",
        "currency": "INR",
        "price_minor": PRICE,
        "feature": "byok",
        "renews": True,
        "payment": "wallet",
        "granted_by": "asha@shree.example",
        "starts_at": datetime(2026, 10, 1, tzinfo=UTC),
        "ends_at": datetime(2026, 11, 1, tzinfo=UTC),
    }
    return world.db.add(AddOnGrant(**(values | fields)))


async def run(world: World, now: datetime = NOW) -> addon_renewals.RenewalResult:
    return await addon_renewals.renew_due(world.db.factory(), now=now)


# ---------------------------------------------------------------------------- the charge


async def test_a_due_grant_is_charged_once_and_moves_one_period_on(world: World) -> None:
    grant = grant_for(world)
    with books_for(world) as books:
        first = await run(world)
        again = await run(world)
    key = f"addon:{grant.id}:2026-11-01"
    assert list(books.entries) == [key]
    entry = books.entries[key]
    assert entry.amount_minor == -PRICE and entry.entry_type is ledger.EntryType.ADD_ON_PURCHASE
    assert entry.reference_type == "add_on_grant" and entry.reference_id == str(grant.id)
    assert grant.ends_at == datetime(2026, 12, 1, tzinfo=UTC)
    assert grant.renewed_through == date(2026, 11, 1) and grant.cancelled_at is None
    assert (first.renewed, again.renewed) == (1, 0)  # the second run finds nothing due
    assert first.ended == [] and len(first.charged_accounts) == 1
    books.hook.assert_awaited_once()


async def test_a_grant_not_yet_due_is_left_alone(world: World) -> None:
    grant = grant_for(world, ends_at=datetime(2026, 11, 20, tzinfo=UTC))
    with books_for(world) as books:
        result = await run(world)
    assert books.entries == {} and result.renewed == 0
    assert grant.ends_at == datetime(2026, 11, 20, tzinfo=UTC)


async def test_missed_periods_are_each_charged_once_in_order(world: World) -> None:
    grant = grant_for(world, ends_at=datetime(2026, 9, 1, tzinfo=UTC))  # the beat was down
    with books_for(world) as books:
        result = await run(world)
    assert list(books.entries) == [
        f"addon:{grant.id}:2026-09-01",
        f"addon:{grant.id}:2026-10-01",
        f"addon:{grant.id}:2026-11-01",
    ]
    assert grant.ends_at == datetime(2026, 12, 1, tzinfo=UTC) and result.renewed == 3


async def test_an_account_on_a_schedule_still_renews_one_month_at_a_time(world: World) -> None:
    from callobuzz_rag.db.models.billing_account import BillingAccount

    grant = grant_for(world, ends_at=datetime(2026, 11, 15, tzinfo=UTC))
    with books_for(world) as books:
        world.db.add(
            BillingAccount(
                tenant_id=world.shree.id,
                currency="INR",
                ledger_seq=0,
                billing_day=15,
                next_charge_on=date(2026, 12, 15),
            )
        )
        await run(world, now=datetime(2026, 11, 15, 6, tzinfo=UTC))
    assert list(books.entries) == [f"addon:{grant.id}:2026-11-15"]
    assert grant.ends_at == datetime(2026, 12, 15, tzinfo=UTC)


# ---------------------------------------------------------------------- not renewed, never a pause


async def test_a_balance_that_cannot_cover_it_ends_the_grant_and_tells_the_owners_once(
    world: World,
) -> None:
    grant = grant_for(world)
    with books_for(world) as books:
        books.available = 10_000  # prepaid, grace 0: 100 rupees cannot cover 499
        first = await run(world)
        second = await run(world)
    assert books.entries == {}  # nothing charged, nothing paused
    assert grant.cancelled_at == NOW and grant.ends_at == datetime(2026, 11, 1, tzinfo=UTC)
    assert [(e.add_on_name, e.reason) for e in first.ended] == [
        ("Use your own AI key", addon_renewals.LOW_BALANCE)
    ]
    assert second.ended == [] and second.renewed == 0  # marked: never tried (or mailed) again
    books.hook.assert_not_awaited()


async def test_a_balance_that_would_land_on_the_pause_line_does_not_renew(world: World) -> None:
    grant_for(world)
    with books_for(world) as books:
        books.available = PRICE  # landing on zero already pauses a prepaid workspace
        result = await run(world)
    assert books.entries == {} and len(result.ended) == 1


async def test_the_ended_notice_goes_to_the_owners(world: World) -> None:
    world.member(world.user("asha@shree.example"), world.shree, "owner")
    world.member(world.user("ravi@shree.example"), world.shree, "member")
    sent: list[tuple[str, str, dict[str, Any]]] = []

    def enqueue(template: str, to: str, context: dict[str, Any], *, expires_s: float) -> None:
        sent.append((template, to, context))

    ended = [
        addon_renewals.Ended(world.shree.id, "Use your own AI key", addon_renewals.LOW_BALANCE)
    ]
    count = await addon_renewals.send_ended_notices(
        world.db.factory(), ended, enqueue=enqueue, dashboard_url="https://app.xyz.in"
    )
    assert count == 1
    [(template, to, context)] = sent
    assert (template, to) == ("add_on_ended", "asha@shree.example")
    assert context["reason"] == addon_renewals.LOW_BALANCE and context["add_on_name"]
    assert context["shop_url"] == "https://app.xyz.in/plan"


@pytest.mark.parametrize("field", ["cancelled_at", "revoked_at"])
async def test_a_cancelled_or_revoked_grant_is_never_renewed(world: World, field: str) -> None:
    grant = grant_for(world, **{field: datetime(2026, 10, 20, tzinfo=UTC)})
    with books_for(world) as books:
        result = await run(world)
    assert books.entries == {} and result.renewed == 0 and result.ended == []
    assert grant.ends_at == datetime(2026, 11, 1, tzinfo=UTC)


async def test_a_grant_that_does_not_renew_is_ignored(world: World) -> None:
    grant_for(world, renews=False)
    with books_for(world) as books:
        result = await run(world)
    assert books.entries == {} and result.renewed == 0


async def test_with_billing_off_the_grant_is_extended_without_a_charge(world: World) -> None:
    grant = grant_for(world)
    with books_for(world, billing=False) as books:
        books.available = 0  # even an empty balance: nothing is billed
        result = await run(world)
    assert books.entries == {} and result.ended == [] and result.renewed == 1
    assert grant.ends_at == datetime(2026, 12, 1, tzinfo=UTC)


async def test_a_free_renewal_is_extended_with_no_entry(world: World) -> None:
    grant = grant_for(world, price_minor=0)
    with books_for(world) as books:
        await run(world)
    assert books.entries == {} and grant.ends_at == datetime(2026, 12, 1, tzinfo=UTC)


def test_the_next_period_is_always_one_month_even_on_a_yearly_account() -> None:
    # a yearly account bought on 10 Oct: the add-on ends 10 Nov and renews monthly after that
    ends = addons.period_end(datetime(2026, 10, 10, 9, tzinfo=UTC))
    assert ends == datetime(2026, 11, 10, tzinfo=UTC)
    assert addon_renewals.next_end(ends) == datetime(2026, 12, 10, tzinfo=UTC)
    assert addon_renewals.next_end(datetime(2026, 11, 1, tzinfo=UTC)) == datetime(
        2026, 12, 1, tzinfo=UTC
    )
    assert addon_renewals.next_end(datetime(2026, 12, 31, tzinfo=UTC)) == datetime(
        2027, 1, 28, tzinfo=UTC
    )
    assert addons is not None  # the rule it shares with the shop lives there
