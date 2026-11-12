"""A WhatsApp-ready world for the TV6 tests: one business, one knowledge base, one connected
number, on the in-memory database of `accounts_fakes.py`. Nothing here touches Meta.
"""

import hashlib
import hmac
import json
import uuid
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from cryptography.fernet import Fernet
from pydantic import SecretStr

from callobuzz_rag.channels.whatsapp.store import e164
from callobuzz_rag.conversation.leads.crypto import LeadCrypto
from callobuzz_rag.core.secrets import FernetEncryptor
from callobuzz_rag.db.models.channel import ChannelConnection, ChannelEvent, ChannelThread
from callobuzz_rag.db.models.corpus import Corpus
from tests.unit.test_auth_routes import ADMIN, World, auth_app, req

APP_SECRET = "app-secret-for-tests"
VERIFY = "verify-token-for-tests"
KEY = Fernet.generate_key().decode()
PHONE_ID = "1234567890"
WABA_ID = "5550001"
TOKEN = "EAAG-token-for-tests"
CUSTOMER = "919822012345"
URL = "/api/v1/public/whatsapp/webhook"


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def body_of(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload).encode()


def whatsapp_settings(**extra: Any) -> dict[str, Any]:
    return {
        "whatsapp_enabled": True,
        "whatsapp_app_id": "123456789",
        "whatsapp_app_secret": SecretStr(APP_SECRET),
        "whatsapp_verify_token": SecretStr(VERIFY),
        "whatsapp_config_id": "99999",
        "encryption_key": KEY,
        "public_url": "https://chat.sharmafurniture.in",
        **extra,
    }


class WhatsAppWorld(World):
    """`World` plus a knowledge base of Shree Labs with a live WhatsApp connection."""

    def __init__(self) -> None:
        super().__init__()
        self.corpus = self.db.add(
            Corpus(
                name="Shree Dental",
                tenant_id=self.shree.id,
                qdrant_collection="corpus_shree",
                api_key_hash="",
            )
        )
        self.encryptor = FernetEncryptor(KEY)
        self.crypto = LeadCrypto(self.encryptor)
        self.connection = self.connect()

    def connect(
        self,
        *,
        phone_number_id: str = PHONE_ID,
        corpus: Corpus | None = None,
        status: str = "active",
        meta_billing: str = "deployer",
        coexistence: bool = False,
        settings: dict[str, Any] | None = None,
    ) -> ChannelConnection:
        target = corpus or self.corpus
        return self.db.add(
            ChannelConnection(
                corpus_id=target.id,
                tenant_id=target.tenant_id,
                kind="whatsapp",
                status=status,
                phone_number_id=phone_number_id,
                waba_id=WABA_ID,
                display_phone="+91 98000 00000",
                verified_name="Shree Dental Clinic",
                access_token_encrypted=self.encryptor.encrypt(TOKEN),
                token_hint=TOKEN[-4:],
                setup="manual",
                coexistence=coexistence,
                meta_billing=meta_billing,
                settings=settings or {},
                created_by="ops@x.in",
            )
        )

    def thread(self, contact: str = CUSTOMER, **fields: Any) -> ChannelThread:
        number = e164(contact)
        return self.db.add(
            ChannelThread(
                connection_id=self.connection.id,
                corpus_id=self.corpus.id,
                tenant_id=self.shree.id,
                contact_hash=self.crypto.index_hash(number),
                contact_encrypted=self.encryptor.encrypt(number),
                **fields,
            )
        )

    def events(self, external_id: str | None = None) -> list[ChannelEvent]:
        rows = self.db.all(ChannelEvent)
        return [r for r in rows if external_id is None or r.external_id == external_id]

    def stored_payload(self, event: ChannelEvent) -> dict[str, Any]:
        return json.loads(self.encryptor.decrypt(event.payload_encrypted))


@contextmanager
def whatsapp_app(world: WhatsAppWorld, **settings: Any) -> Any:
    with auth_app(world, **whatsapp_settings(**settings)) as app:
        yield app


async def post_webhook(
    app: Any, payload: dict[str, Any] | bytes, *, signature: str | None = None, **kw: Any
) -> Any:
    body = payload if isinstance(payload, bytes) else body_of(payload)
    headers = {"Content-Type": "application/json", "X-Hub-Signature-256": signature or sign(body)}
    return await req(app, "POST", URL, content=body, headers=headers, **kw)


def spy_queue() -> Any:
    """Replace the worker wake-up with a recorder: `spy.calls` lists the chat ids woken."""
    calls: list[uuid.UUID] = []
    patcher = patch(
        "callobuzz_rag.channels.whatsapp.dispatch.queue_thread", side_effect=calls.append
    )
    patcher.calls = calls  # type: ignore[attr-defined]
    return patcher


__all__ = [
    "ADMIN",
    "APP_SECRET",
    "CUSTOMER",
    "KEY",
    "PHONE_ID",
    "TOKEN",
    "URL",
    "VERIFY",
    "WABA_ID",
    "WhatsAppWorld",
    "body_of",
    "post_webhook",
    "sign",
    "spy_queue",
    "whatsapp_app",
    "whatsapp_settings",
]


def seed_whatsapp(world: World) -> tuple[Corpus, ChannelConnection, ChannelThread]:
    """A knowledge base of `world.shree` with a live number and one open chat, on any `World`
    (the audit-coverage drivers use a plain one)."""
    from datetime import UTC, datetime, timedelta

    world.db.default_plan.channels = {"whatsapp": True}
    encryptor = FernetEncryptor(KEY)
    crypto = LeadCrypto(encryptor)
    corpus = world.db.add(
        Corpus(
            name="Shree Dental",
            tenant_id=world.shree.id,
            qdrant_collection="corpus_shree",
            api_key_hash="",
        )
    )
    connection = world.db.add(
        ChannelConnection(
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            kind="whatsapp",
            status="active",
            phone_number_id=PHONE_ID,
            waba_id=WABA_ID,
            display_phone="+91 98000 00000",
            access_token_encrypted=encryptor.encrypt(TOKEN),
            token_hint=TOKEN[-4:],
            setup="manual",
            meta_billing="deployer",
            created_by="ops@x.in",
        )
    )
    number = e164(CUSTOMER)
    thread = world.db.add(
        ChannelThread(
            connection_id=connection.id,
            corpus_id=corpus.id,
            tenant_id=world.shree.id,
            contact_hash=crypto.index_hash(number),
            contact_encrypted=encryptor.encrypt(number),
            last_inbound_at=datetime.now(UTC) - timedelta(minutes=5),
        )
    )
    return corpus, connection, thread
