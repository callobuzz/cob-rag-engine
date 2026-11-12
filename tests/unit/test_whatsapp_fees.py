"""Meta's per-message fees as a pass-through usage charge (TV6)."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    WhatsAppEngineSettings,
    WhatsAppRates,
    invalidate_engine_settings_cache,
)
from callobuzz_rag.billing import charging
from callobuzz_rag.billing.charging import NAMESPACE_BILLING, charge_pass_through
from callobuzz_rag.billing.statements import ChargeGroup, Names, build_statement, source_label
from callobuzz_rag.channels.whatsapp import fees
from callobuzz_rag.channels.whatsapp.outbound import SendContext
from callobuzz_rag.db.models.engine_settings import EngineSettingsRow
from tests.unit.accounts_fakes import FakeDB
from tests.unit.whatsapp_fixtures import inbound_text
from tests.unit.whatsapp_rig import NOW, rig_for
from tests.unit.whatsapp_world import WhatsAppWorld

NOW_UTC = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


# ------------------------------------------------------------------ the pure rules


@pytest.mark.parametrize(
    ("meta_billing", "fees_mode", "applies"),
    [
        ("deployer", "pass_through", True),
        ("business", "pass_through", False),
        ("deployer", "included", False),
        ("business", "included", False),
    ],
)
def test_pass_through_only_when_the_deployer_pays_and_the_plan_says_so(
    meta_billing: str, fees_mode: str, applies: bool
) -> None:
    assert fees.fee_applies(meta_billing, fees_mode) is applies


def test_the_fee_id_is_one_per_message() -> None:
    a = fees.fee_request_id("wamid.OUT1")
    assert a == fees.fee_request_id("wamid.OUT1") != fees.fee_request_id("wamid.OUT2")
    assert a == uuid.uuid5(NAMESPACE_BILLING, "wa-fee:wamid.OUT1")


@pytest.mark.parametrize(
    ("rate", "category", "used", "free", "fee"),
    [
        (12, "service", 0, 0, 12),
        (12, "service", 5, 1000, 0),  # inside the free allowance
        (12, "service", 1000, 1000, 12),  # the allowance is used up
        (12, "utility", 0, 1000, 12),  # the allowance is for service replies only
        (0, "service", 0, 0, 0),  # a zero rate: nothing
        (-1, "service", 0, 0, 0),
    ],
)
def test_the_fee_for_one_more_message(
    rate: int, category: str, used: int, free: int, fee: int
) -> None:
    assert (
        fees.fee_minor(rate=rate, category=category, used_this_month=used, free_service=free) == fee
    )


def test_rates_are_what_the_deployer_set_and_nothing_else() -> None:
    engine = WhatsAppEngineSettings(
        rates_minor={"INR": WhatsAppRates(service=12, utility=15), "USD": WhatsAppRates(utility=2)}
    )
    assert fees.rate_minor(engine, "INR", "service") == 12
    assert fees.rate_minor(engine, "INR", "marketing") == 0
    assert fees.rate_minor(engine, "USD", "utility") == 2
    assert fees.rate_minor(WhatsAppEngineSettings(), "INR", "service") == 0  # no price is built in


# ------------------------------------------------------------------ the hook


class FeeRig:
    """A world with a plan, a deployer's rates, and `charge_pass_through` recorded."""

    def __init__(
        self, world: WhatsAppWorld, *, meta_billing: str, fees_mode: str, rates: dict | None
    ):
        self.world = world
        world.connection.meta_billing = meta_billing
        world.db.default_plan.channels = {"whatsapp": True, "whatsapp_fees": fees_mode}
        if rates is not None:
            world.db.add(EngineSettingsRow(id=1, value={"whatsapp": rates}))
        invalidate_engine_settings_cache()
        self.charges: list[dict] = []
        self.prices: list[int | None] = []
        self.used = 0

    async def fake_charge(self, session, **kw):
        price = await kw["price_micro"]("INR")
        self.prices.append(price)
        if price is not None:
            self.charges.append({**kw, "amount_micro": price})
        return SimpleNamespace(status="charged" if price is not None else "unbilled")

    def context(self) -> SendContext:
        w = self.world
        return SendContext(
            graph=None,  # type: ignore[arg-type]
            session_factory=w.db.factory(),
            connection_id=w.connection.id,
            corpus_id=w.corpus.id,
            tenant_id=w.shree.id,
            phone_number_id="1234567890",
            token="t",
            thread_id=uuid.uuid4(),
            to="919822012345",
            meta_billing=w.connection.meta_billing,
            clock=lambda: NOW_UTC,
        )

    async def send(self, wamid: str = "wamid.OUT1", category: str = "service") -> None:
        async def used(session, corpus_id, moment):
            return self.used

        with (
            patch.object(fees, "charge_pass_through", self.fake_charge),
            patch.object(fees, "used_this_month", used),
        ):
            await fees.charge_send(self.world.db.session(), self.context(), wamid, category)  # type: ignore[arg-type]


RATES = {"rates_minor": {"INR": {"service": 12, "utility": 15}}, "free_service_per_month": 0}


@pytest.mark.parametrize(
    ("meta_billing", "fees_mode", "charged"),
    [
        ("deployer", "pass_through", True),
        ("business", "pass_through", False),
        ("deployer", "included", False),
    ],
)
async def test_a_message_is_charged_only_when_the_deployer_pays_and_the_plan_says_so(
    meta_billing: str, fees_mode: str, charged: bool
) -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing=meta_billing, fees_mode=fees_mode, rates=RATES)
    await rig.send()
    assert (len(rig.charges) == 1) is charged
    if charged:
        (row,) = rig.charges
        assert row["amount_micro"] == 12 * 10_000 and row["source"] == "whatsapp_fee"
        assert row["request_id"] == uuid.uuid5(NAMESPACE_BILLING, "wa-fee:wamid.OUT1")
        assert row["tenant_id"] == rig.world.shree.id and row["corpus_id"] == rig.world.corpus.id


async def test_the_utility_rate_is_used_for_a_template() -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing="deployer", fees_mode="pass_through", rates=RATES)
    await rig.send(category="utility")
    assert rig.charges[0]["amount_micro"] == 15 * 10_000


async def test_a_fee_charge_is_keyed_by_the_message_so_a_repeat_is_one_charge() -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing="deployer", fees_mode="pass_through", rates=RATES)
    await rig.send("wamid.OUT1")
    await rig.send("wamid.OUT1")
    assert {c["request_id"] for c in rig.charges} == {fees.fee_request_id("wamid.OUT1")}


async def test_a_zero_rate_writes_no_row() -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing="deployer", fees_mode="pass_through", rates=None)
    await rig.send()
    assert rig.charges == [] and rig.prices == [None]


async def test_the_free_service_allowance_is_per_number_per_month() -> None:
    rig = FeeRig(
        WhatsAppWorld(),
        meta_billing="deployer",
        fees_mode="pass_through",
        rates={"rates_minor": {"INR": {"service": 12}}, "free_service_per_month": 2},
    )
    amounts = []
    for used in (0, 1, 2, 3):
        rig.used = used
        rig.charges.clear()
        await rig.send(f"wamid.OUT{used}")
        amounts.append(rig.charges[0]["amount_micro"])
    assert amounts == [0, 0, 120_000, 120_000]  # the first two are free (and counted)


async def test_a_failing_fee_never_breaks_the_send() -> None:
    rig = FeeRig(WhatsAppWorld(), meta_billing="deployer", fees_mode="pass_through", rates=RATES)

    async def boom(*a, **k):
        raise RuntimeError("database trouble")

    with patch.object(fees, "charge_pass_through", boom):
        await fees.charge_send(rig.world.db.session(), rig.context(), "wamid.X", "service")  # type: ignore[arg-type]


async def test_an_answer_on_a_deployer_number_is_charged_end_to_end() -> None:
    world = WhatsAppWorld()
    rig_fees = FeeRig(world, meta_billing="deployer", fees_mode="pass_through", rates=RATES)
    with (
        rig_for(world) as rig,
        patch.object(fees, "charge_pass_through", rig_fees.fake_charge),
        patch.object(fees, "used_this_month", AsyncMock(return_value=0)),
    ):
        rig.after_send = fees.charge_send
        (thread_id,) = await rig.deliver(inbound_text(text="How much is home collection?"))
        await rig.run(thread_id)
    assert len(rig_fees.charges) == 1
    assert rig_fees.charges[0]["request_id"] == fees.fee_request_id("wamid.OUT")


# ------------------------------------------------------------------ the charge itself


def _session(inserted: bool = True):
    session = MagicMock()
    result = MagicMock()
    result.first.return_value = (uuid.uuid4(),) if inserted else None
    session.execute = AsyncMock(return_value=result)

    class Nested:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return None

    session.begin_nested = MagicMock(return_value=Nested())
    return session


def _pricing(exempt: bool = False):
    return SimpleNamespace(exempt=exempt, currency="INR", pricing_id=uuid.uuid4())


async def _charge(session, *, price, pricing=None, enabled=True):
    account = SimpleNamespace(id=uuid.uuid4(), currency="INR", ledger_seq=3)
    with (
        patch.object(charging, "billing_enabled", lambda: enabled),
        patch.object(charging, "effective_pricing", AsyncMock(return_value=pricing or _pricing())),
        patch.object(charging, "ensure_account", AsyncMock(return_value=account)),
        patch.object(charging, "usd_inr_rate", lambda: 84.0),
    ):
        return await charge_pass_through(
            session,
            request_id=uuid.uuid4(),
            tenant_id=uuid.uuid4(),
            corpus_id=uuid.uuid4(),
            price_micro=price,
            at=NOW_UTC,
        )


async def test_the_charge_row_is_a_usage_charge_with_no_question() -> None:
    session = _session()

    async def price(currency):
        assert currency == "INR"
        return 120_000

    result = await _charge(session, price=price)
    assert (result.status, result.amount_micro) == ("charged", 120_000)
    stmt = session.execute.await_args.args[0]
    params = stmt.compile().params
    assert params["questions"] == 0 and params["usage_mode"] == "pass_through"
    assert params["amount_micro"] == 120_000 and params["source"] == "whatsapp_fee"
    assert params["payer"] == "operator" and params["cost_usd_micro"] == 0
    assert params["waived"] is None and params["currency"] == "INR"
    assert "ON CONFLICT (request_id) DO NOTHING" in str(stmt.compile(dialect=_pg()))


def _pg():
    from sqlalchemy.dialects import postgresql

    return postgresql.dialect()


async def test_a_repeated_request_id_is_a_duplicate() -> None:
    async def price(currency):
        return 120_000

    assert (await _charge(_session(inserted=False), price=price)).status == "duplicate"


@pytest.mark.parametrize(
    ("kwargs", "answer"),
    [
        ({"enabled": False}, 120_000),
        ({"pricing": _pricing(exempt=True)}, 120_000),  # the operator's own workspace
        ({}, None),  # no rate set
        ({}, -5),
    ],
)
async def test_nothing_is_written_when_nothing_is_billed(kwargs: dict, answer: int | None) -> None:
    session = _session()

    async def price(currency):
        return answer

    assert (await _charge(session, price=price, **kwargs)).status == "unbilled"
    session.execute.assert_not_awaited()


async def test_a_free_allowance_message_is_a_zero_amount_row() -> None:
    session = _session()

    async def price(currency):
        return 0

    result = await _charge(session, price=price)
    assert result.status == "charged" and result.amount_micro == 0
    assert session.execute.await_args.args[0].compile().params["amount_micro"] == 0


# ------------------------------------------------------------------ the statement


def test_the_statement_groups_fees_as_whatsapp_messages() -> None:
    kb = uuid.uuid4()
    assert source_label("whatsapp_fee", Names(corpora={kb: "Dental"})) == "WhatsApp messages"
    assert source_label("whatsapp", Names(corpora={kb: "Dental"})) == "WhatsApp"
    statement = build_statement(
        [],
        [
            ChargeGroup(kb, "api", "gpt", 4, 400_000),
            ChargeGroup(kb, "whatsapp_fee", "", 0, 360_000),
        ],
        opening=0,
        residue_start=0,
        residue_end=760_000,
        names=Names(corpora={kb: "Dental"}),
        month=datetime(2026, 10, 1).date(),
        currency="INR",
    )
    labels = {row.label: row.amount_micro for row in statement.usage_breakdown}
    assert labels["Dental · WhatsApp messages"] == 360_000
    assert any(label.startswith("Dental · gpt") for label in labels)


def test_engine_settings_default_has_no_rates() -> None:
    assert EngineSettings().whatsapp.rates_minor == {}
    assert FakeDB is not None and NOW is not None
