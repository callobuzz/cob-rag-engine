"""The WhatsApp channel's database behaviour on real PostgreSQL (TV6). Needs Docker.

The answer loop itself (the director, its gates, the Graph requests, takeover, STOP and the
follow-up) is covered end to end on the in-memory database by `tests/unit/test_whatsapp_*.py`,
with a recording stand-in for Meta. What only a real database can prove is here:

- a delivery Meta repeats is stored once, and a number we do not serve stores nothing;
- two workers asking for the same chat at once get one event between them (the thread row lock
  and the `processing` mark), so a chat is never answered twice;
- Meta's per-message fee is charged once however often the same message id is reported, a zero
  rate writes nothing, and the books reconcile clean afterwards (the money invariants).

Nothing here contacts Meta.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from callobuzz_rag.accounts.settings_store import WhatsAppEngineSettings
from callobuzz_rag.billing import reconcile
from callobuzz_rag.billing.accounts import ensure_account
from callobuzz_rag.billing.ledger import SYSTEM_ACTOR, EntryType, NewEntry, append_entries
from callobuzz_rag.billing.posting import post_pending
from callobuzz_rag.channels.whatsapp import fees, inbound
from callobuzz_rag.channels.whatsapp.store import store_events
from callobuzz_rag.channels.whatsapp.webhook import parse
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelEvent, ChannelThread
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.plan_pricing import PlanPricing
from callobuzz_rag.db.models.tenant import Tenant
from callobuzz_rag.db.models.usage_charge import UsageCharge
from tests.integration.conftest import _async_url
from tests.unit.whatsapp_fixtures import inbound_text

pytestmark = pytest.mark.integration


class Setup:
    def __init__(
        self,
        factory: Any,
        crypto: LeadCrypto,
        tenant_id: Any,
        corpus_id: Any,
        connection_id: Any,
        phone_id: str,
        account_id: Any,
    ):
        self.phone_id = phone_id
        self.account_id = account_id
        self.factory = factory
        self.crypto = crypto
        self.tenant_id = tenant_id
        self.corpus_id = corpus_id
        self.connection_id = connection_id

    async def deliver(self, payload: dict[str, Any]) -> list[uuid.UUID]:
        async with self.factory() as session:
            woken = await store_events(session, self.crypto, parse(payload))
            await session.commit()
        return woken

    async def count(self, model: Any) -> int:
        async with self.factory() as session:
            return int(
                (await session.execute(select(func.count()).select_from(model))).scalar_one()
            )

    def send_context(self, session_factory: Any) -> Any:
        return SimpleNamespace(
            meta_billing="deployer",
            tenant_id=self.tenant_id,
            corpus_id=self.corpus_id,
            session_factory=session_factory,
            clock=lambda: datetime.now(UTC),
        )


@pytest_asyncio.fixture
async def setup(migrated_postgres):  # type: ignore[no-untyped-def]
    engine = create_async_engine(_async_url(migrated_postgres), pool_size=10, max_overflow=10)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    encryptor = FernetEncryptor(Fernet.generate_key().decode())
    crypto = LeadCrypto(encryptor)
    phone_id = str(uuid.uuid4().int)[:12]  # a number is unique across the shared test database
    async with factory() as s:
        plan_id = uuid.uuid4()
        await s.execute(
            text("INSERT INTO plans (id, name, channels) VALUES (:i, :n, CAST(:c AS jsonb))"),
            {
                "i": plan_id,
                "n": f"wa-{plan_id.hex[:8]}",
                "c": '{"whatsapp": true, "whatsapp_fees": "pass_through"}',
            },
        )
        s.add(
            PlanPricing(
                plan_id=plan_id,
                version=1,
                is_current=True,
                currency="INR",
                usage_mode="per_question",
                per_question_minor=200,
            )
        )
        tenant = Tenant(name=f"shree-{uuid.uuid4().hex[:8]}", plan_id=plan_id)
        s.add(tenant)
        await s.flush()
        corpus = Corpus(name="Shree Dental", tenant_id=tenant.id)
        s.add(corpus)
        await s.flush()
        connection = ChannelConnection(
            corpus_id=corpus.id,
            tenant_id=tenant.id,
            kind="whatsapp",
            status="active",
            phone_number_id=phone_id,
            waba_id="5550001",
            display_phone="+91 98200 11111",
            verified_name="Shree Dental Clinic",
            access_token_encrypted=encryptor.encrypt("EAAG-token-for-tests"),
            token_hint="ests",
            setup="manual",
            meta_billing="deployer",
            settings={},
            created_by="ops@x.in",
        )
        s.add(connection)
        account = await ensure_account(s, tenant.id)
        await append_entries(
            s, account.id, [NewEntry(EntryType.TOP_UP, 100_000, "wa:topup")], actor=SYSTEM_ACTOR
        )
        await s.commit()
    out = Setup(factory, crypto, tenant.id, corpus.id, connection.id, phone_id, account.id)
    yield out
    await engine.dispose()


async def test_a_repeated_delivery_is_stored_once(setup: Setup) -> None:
    first = await setup.deliver(inbound_text(wamid="wamid.DB1", phone_number_id=setup.phone_id))
    assert len(first) == 1  # one chat to wake
    again = await setup.deliver(
        inbound_text(wamid="wamid.DB1", phone_number_id=setup.phone_id)
    )  # Meta retries
    assert again == []
    async with setup.factory() as s:
        events = (
            (
                await s.execute(
                    select(ChannelEvent).where(ChannelEvent.connection_id == setup.connection_id)
                )
            )
            .scalars()
            .all()
        )
        threads = (
            (
                await s.execute(
                    select(ChannelThread).where(ChannelThread.connection_id == setup.connection_id)
                )
            )
            .scalars()
            .all()
        )
    assert [e.external_id for e in events] == ["wamid.DB1"]
    assert events[0].status == "received"
    assert len(threads) == 1 and threads[0].last_inbound_at is not None
    assert "Root canal" not in (events[0].payload_encrypted or "")  # encrypted at rest


async def test_a_number_we_do_not_serve_stores_nothing(setup: Setup) -> None:
    before = await setup.count(ChannelEvent)
    woken = await setup.deliver(inbound_text(wamid="wamid.OTHER", phone_number_id="9999999999"))
    assert woken == []
    assert await setup.count(ChannelEvent) == before


async def test_two_workers_for_one_chat_get_one_event(setup: Setup) -> None:
    (thread_id,) = await setup.deliver(
        inbound_text(wamid="wamid.DB2", phone_number_id=setup.phone_id)
    )
    deps = inbound.InboundDeps(
        session_factory=setup.factory,
        settings=SimpleNamespace(),
        crypto=setup.crypto,
        graph=None,  # type: ignore[arg-type]
        director_factory=lambda *_: None,
    )
    claims = await asyncio.gather(
        inbound.claim_next(deps, thread_id), inbound.claim_next(deps, thread_id)
    )
    got = [c for c in claims if c is not None]
    assert len(got) == 1 and got[0].external_id == "wamid.DB2"
    async with setup.factory() as s:
        status = (
            await s.execute(
                select(ChannelEvent.status).where(ChannelEvent.external_id == "wamid.DB2")
            )
        ).scalar_one()
    assert status == "processing"
    assert await inbound.claim_next(deps, thread_id) is None  # nothing left, one in flight


async def test_a_fee_is_charged_once_and_the_books_reconcile(setup: Setup) -> None:
    engine_settings = SimpleNamespace(
        whatsapp=WhatsAppEngineSettings(
            rates_minor={"INR": {"service": 0, "utility": 35, "marketing": 0, "authentication": 0}}
        )
    )
    ctx = setup.send_context(setup.factory)
    with patch.object(fees, "get_engine_settings", AsyncMock(return_value=engine_settings)):
        for _ in range(2):  # the same message id reported twice
            async with setup.factory() as s:
                await fees.charge_send(s, ctx, "wamid.OUT-DB1", "utility")
                await s.commit()
        async with setup.factory() as s:  # a service reply has no rate: nothing is written
            await fees.charge_send(s, ctx, "wamid.OUT-DB2", "service")
            await s.commit()
    async with setup.factory() as s:
        rows = (
            (await s.execute(select(UsageCharge).where(UsageCharge.corpus_id == setup.corpus_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].amount_micro == 35 * 10_000  # Rs 0.35 in micro units
    await post_pending(setup.factory)
    report = await reconcile.run(setup.factory, day=datetime.now(UTC).date())
    assert report is not None
    mine = [f for f in report.findings if f.account_id == setup.account_id and not f.informational]
    assert (
        mine == []
    )  # this business's books are clean (others in the shared database are not ours)
