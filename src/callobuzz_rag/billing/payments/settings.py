"""The operator's payment settings: which provider, its keys (encrypted) and where they come from.

The key id is public in checkout and is stored plain; the key secret and the webhook secret are
Fernet tokens in `payment_provider_settings` (never in `engine_settings`, whose GET returns every
value). `RAG_PAYMENTS_PROVIDER` and `RAG_RAZORPAY_*` win over the stored row when set. Nothing here
returns a secret: callers get `*_set` flags.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import EncryptionError, FernetEncryptor, encryptor_from_settings
from callobuzz_rag.db.models.payment import PaymentProviderSetting

ROW_ID: Final = 1
KEY_ID_PATTERN: Final = re.compile(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$")

# What to tick in Razorpay's webhook settings.
WEBHOOK_EVENTS: Final[tuple[str, ...]] = (
    "payment.captured",
    "payment.failed",
    "order.paid",
    "refund.processed",
    "refund.failed",
    "payment.dispute.created",
    "payment.dispute.lost",
    "payment.dispute.won",
    "payment_link.paid",
)
WEBHOOK_PATH: Final = "/api/v1/public/payments/razorpay/webhook"


@dataclass(frozen=True, slots=True)
class ResolvedSettings:
    """What is in force: the stored row with the environment on top. Secrets are plain here and
    must never leave the process."""

    provider: Literal["none", "razorpay"]
    key_id: str
    key_secret: str
    webhook_secret: str
    source: Literal["env", "stored"]

    @property
    def configured(self) -> bool:
        return self.provider == "razorpay" and bool(self.key_id and self.key_secret)


async def read_row(session: AsyncSession) -> PaymentProviderSetting | None:
    return (
        await session.execute(
            select(PaymentProviderSetting).where(PaymentProviderSetting.id == ROW_ID)
        )
    ).scalar_one_or_none()


def _decrypt(encryptor: FernetEncryptor | None, token: str | None) -> str:
    if not token or encryptor is None:
        return ""
    try:
        return encryptor.decrypt(token)
    except EncryptionError:
        return ""


def resolve(
    row: PaymentProviderSetting | None, settings: Settings, encryptor: FernetEncryptor | None
) -> ResolvedSettings:
    """Stored values with the environment's on top; each secret is overridden on its own."""
    env_provider = settings.payments_provider.strip()
    provider = env_provider or (row.provider if row is not None else "none")
    key_id = settings.razorpay_key_id.strip() or (row.key_id if row and row.key_id else "")
    key_secret = settings.razorpay_key_secret.get_secret_value() or _decrypt(
        encryptor, row.key_secret_encrypted if row else None
    )
    webhook_secret = settings.razorpay_webhook_secret.get_secret_value() or _decrypt(
        encryptor, row.webhook_secret_encrypted if row else None
    )
    from_env = bool(
        env_provider
        or settings.razorpay_key_id.strip()
        or settings.razorpay_key_secret.get_secret_value()
        or settings.razorpay_webhook_secret.get_secret_value()
    )
    return ResolvedSettings(
        provider="razorpay" if provider == "razorpay" else "none",
        key_id=key_id,
        key_secret=key_secret,
        webhook_secret=webhook_secret,
        source="env" if from_env else "stored",
    )


async def load(session: AsyncSession, settings: Settings) -> ResolvedSettings:
    return resolve(await read_row(session), settings, encryptor_from_settings(settings))


def webhook_url(settings: Settings, request_base: str) -> str:
    """Where Razorpay should send events: the public URL when set, else this request's address."""
    base = (settings.public_url or request_base).rstrip("/")
    return base + WEBHOOK_PATH


async def save(
    session: AsyncSession,
    settings: Settings,
    *,
    provider: Literal["none", "razorpay"],
    key_id: str | None,
    key_secret: str | None,
    webhook_secret: str | None,
    actor_label: str,
) -> tuple[PaymentProviderSetting, dict[str, str]]:
    """Write the stored row. For each of `key_secret` / `webhook_secret`: None leaves it, an empty
    string clears it, anything else replaces it. Returns the row and what happened to each
    (`set`, `cleared`, `unchanged`).

    Raises:
        APIError: 422 `invalid_key_id`; 503 `encryption_unavailable` when a secret is to be stored
            and the engine has no RAG_ENCRYPTION_KEY.
    """
    encryptor = encryptor_from_settings(settings)
    if key_id is not None and key_id.strip() and not KEY_ID_PATTERN.match(key_id.strip()):
        raise APIError(
            422,
            "That doesn't look like a Razorpay key id. It starts with rzp_test_ or rzp_live_.",
            "invalid_key_id",
        )
    if encryptor is None and (key_secret or webhook_secret):
        raise APIError(
            503,
            "Saving payment keys needs the engine's encryption key (RAG_ENCRYPTION_KEY).",
            "encryption_unavailable",
        )
    row = await read_row(session)
    if row is None:
        row = PaymentProviderSetting(id=ROW_ID, provider="none")
        session.add(row)
    outcome = {"key_secret": "unchanged", "webhook_secret": "unchanged"}
    row.provider = provider
    if key_id is not None:
        row.key_id = key_id.strip() or None
    for name, value, column in (
        ("key_secret", key_secret, "key_secret_encrypted"),
        ("webhook_secret", webhook_secret, "webhook_secret_encrypted"),
    ):
        if value is None:
            continue
        if value == "":
            setattr(row, column, None)
            outcome[name] = "cleared"
        else:
            assert encryptor is not None
            setattr(row, column, encryptor.encrypt(value))
            outcome[name] = "set"
    row.updated_at = datetime.now(UTC)
    row.updated_by = actor_label[:320]
    await session.flush()
    return row, outcome
