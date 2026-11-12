"""The super admin's provider balances (billing BIL1): what OpenRouter, OpenAI, Anthropic and any
manual provider have left, without opening their websites.

`GET /api/v1/admin/billing/providers`, `PUT .../{provider}` (settings, a manual balance, the
optional admin key), `POST .../{provider}/refresh`. `{provider}` is `openrouter`, `openai`,
`anthropic`, `manual:<uuid>` for a stored manual balance, or `manual:new` to add one. The admin
key is write-only: it is stored Fernet-encrypted (`core/secrets.py`) and no response, log line or
audit row ever carries it or its ciphertext.
"""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.audit import actor_from_request, record_audit
from callobuzz_rag.analytics import provider_balances as pb
from callobuzz_rag.api.deps import get_session, require_admin
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.provider_balances import (
    ProviderBalanceOut,
    ProviderListResponse,
    ProviderSettingsRequest,
    ProviderStatusOut,
    ProviderSummaryOut,
)
from callobuzz_rag.core.config import Settings
from callobuzz_rag.core.secrets import encryptor_from_settings
from callobuzz_rag.db.models.provider_balance_setting import ProviderBalanceSetting

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/admin/billing/providers",
    tags=["billing"],
    dependencies=[Depends(require_admin)],
)

REFRESH_INTERVAL_S: Final = 60.0
SUMMARY_TIMEOUT_S: Final = 5.0  # the header and Costs page never wait longer on one provider
_API_PROVIDERS: Final = ("openrouter", "openai", "anthropic")
_last_refresh: dict[str, float] = {}
_DATE_SKEW: Final = timedelta(minutes=5)  # a clock a little ahead is not a future date


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def _micro(text: str) -> int:
    """ "50.00" -> 50_000_000, exactly (the body pattern allows at most six decimals)."""
    return int(Decimal(text) * 1_000_000)


def _unknown() -> APIError:
    return APIError(
        404,
        "That provider is not one of OpenRouter, OpenAI, Anthropic or a saved manual balance.",
        "provider_unknown",
    )


async def _row_for(
    session: AsyncSession, provider: str
) -> tuple[str, ProviderBalanceSetting | None]:
    """(provider id, its stored row or None): None for `manual:new` and for an API provider
    nothing was saved for yet."""
    if provider in _API_PROVIDERS:
        row = (
            await session.execute(
                select(ProviderBalanceSetting).where(ProviderBalanceSetting.provider == provider)
            )
        ).scalar_one_or_none()
        return provider, row
    if provider.startswith("manual:"):
        ident = provider.split(":", 1)[1]
        if ident == "new":
            return "manual", None
        try:
            key = uuid.UUID(ident)
        except ValueError:
            raise _unknown() from None
        row = (
            await session.execute(
                select(ProviderBalanceSetting).where(
                    ProviderBalanceSetting.id == key, ProviderBalanceSetting.provider == "manual"
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise _unknown()
        return "manual", row
    raise _unknown()


def _invalid(detail: str) -> APIError:
    return APIError(422, detail, "invalid_provider_settings")


async def _one(
    request: Request, session: AsyncSession, only: str, *, refresh: bool = False
) -> ProviderBalanceOut:
    items = await pb.provider_balances(_settings(request), session, refresh=refresh, only=only)
    if not items:
        raise _unknown()
    return ProviderBalanceOut.of(items[0])


@router.get("", response_model=ProviderListResponse)
async def list_provider_balances(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ProviderListResponse:
    """Every provider: configured ones with their figures, the rest `not_configured`."""
    balances = await pb.provider_balances(_settings(request), session)
    w = pb.worst(balances)
    return ProviderListResponse(
        items=[ProviderBalanceOut.of(b) for b in balances],
        worst=ProviderStatusOut.of(w) if w is not None else None,
    )


@router.put("/{provider}", response_model=ProviderBalanceOut)
async def update_provider(
    provider: str,
    body: ProviderSettingsRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> ProviderBalanceOut:
    """Save a manual balance, a label, an alert threshold or the provider's admin key. Send
    only what changes. The key is never returned."""
    provider_id, row = await _row_for(session, provider)
    sent = body.model_fields_set
    changed: list[str] = []
    key_change = "unchanged"

    if body.admin_key is not None and body.clear_admin_key:
        raise _invalid("Send a new key or ask to remove the saved one, not both.")
    if provider_id == "manual" and (body.admin_key is not None or body.clear_admin_key):
        raise _invalid("A manual balance has no key. Only OpenRouter, OpenAI and Anthropic do.")
    if body.manual_balance_usd is not None and body.clear_manual:
        raise _invalid("Send a balance or ask to remove it, not both.")

    if body.manual_balance_at is not None and body.manual_balance_at > (
        datetime.now(UTC) + _DATE_SKEW
    ):
        raise _invalid("The balance date cannot be in the future.")

    new_secret = body.admin_key.get_secret_value() if body.admin_key is not None else None
    if new_secret is not None and not new_secret.strip():
        raise _invalid("The key is empty.")
    encryptor = encryptor_from_settings(_settings(request))
    if new_secret is not None and encryptor is None:
        raise APIError(
            503,
            "Set RAG_ENCRYPTION_KEY on the engine before saving an admin key here, or put the "
            "key in the engine's .env file instead.",
            "encryption_not_configured",
        )

    if row is None:
        label = body.label or pb.PROVIDER_LABELS.get(provider_id)
        if not label:
            raise _invalid("Give the balance a name, for example Groq.")
        if provider_id == "manual":
            row = ProviderBalanceSetting(provider=provider_id, label=label, alert_state="ok")
            session.add(row)
            changed.append("label")
        else:
            # Never a bare INSERT: the beat check may be creating the same row right now.
            row, created = await pb.ensure_row(session, provider_id, label)
            if created:
                changed.append("label")
    if body.label is not None and body.label != row.label:
        row.label = body.label
        changed.append("label")

    now = datetime.now(UTC)
    if body.clear_manual:
        if row.manual_balance_usd_micro is not None:
            changed.append("manual_balance")
        row.manual_balance_usd_micro = None
        row.manual_balance_at = None
    elif body.manual_balance_usd is not None:
        row.manual_balance_usd_micro = _micro(body.manual_balance_usd)
        row.manual_balance_at = body.manual_balance_at or now
        changed.append("manual_balance")
    elif body.manual_balance_at is not None:
        if row.manual_balance_usd_micro is None:
            raise _invalid("Send the balance together with the date it was true.")
        row.manual_balance_at = body.manual_balance_at
        changed.append("manual_balance")

    if "manual_note" in sent and body.manual_note != row.manual_note:
        row.manual_note = body.manual_note
        changed.append("manual_note")
    if "low_threshold_usd" in sent:
        new = _micro(body.low_threshold_usd) if body.low_threshold_usd is not None else None
        if new != row.low_threshold_usd_micro:
            row.low_threshold_usd_micro = new
            changed.append("low_threshold")

    if new_secret is not None and encryptor is not None:
        row.admin_key_encrypted = encryptor.encrypt(new_secret.strip())
        key_change = "set"
    elif body.clear_admin_key and row.admin_key_encrypted is not None:
        row.admin_key_encrypted = None
        key_change = "cleared"

    row.updated_at = now
    row.updated_by = actor_from_request(request)[2]
    await session.flush()  # the row's id for the audit entry and the response

    await record_audit(
        session,
        request,
        "billing.provider_updated",
        target_type="provider",
        target_id=provider_id if provider_id != "manual" else str(row.id),
        details={"provider": provider_id, "changed": changed, "key_change": key_change},
    )
    await session.commit()
    pb.forget(provider_id)  # a changed key or balance must not be served the old figure
    return await _one(request, session, str(row.id) if provider_id == "manual" else provider_id)


@router.post("/{provider}/refresh", response_model=ProviderBalanceOut)
async def refresh_provider(
    provider: str, request: Request, session: AsyncSession = Depends(get_session)
) -> ProviderBalanceOut:
    """Ask the provider again now, past the five-minute cache. Once a minute per provider."""
    provider_id, row = await _row_for(session, provider)
    if provider_id == "manual" and row is None:
        raise _unknown()
    throttle_key = str(row.id) if provider_id == "manual" and row is not None else provider_id
    now = time.monotonic()
    last = _last_refresh.get(throttle_key)
    if last is not None and now - last < REFRESH_INTERVAL_S:
        wait = max(1, int(REFRESH_INTERVAL_S - (now - last)) + 1)
        raise APIError(
            429,
            f"Providers can be refreshed once a minute. Try again in {wait} seconds.",
            "provider_refresh_too_soon",
            headers={"Retry-After": str(wait)},
        )
    _last_refresh[throttle_key] = now
    only = str(row.id) if provider_id == "manual" and row is not None else provider_id
    return await _one(request, session, only, refresh=True)


async def provider_summary(settings: Settings, session: AsyncSession) -> ProviderSummaryOut | None:
    """The one-line-per-provider summary for the Costs report and the header, or None when it
    cannot be built: those pages never fail because a provider did."""
    try:
        return ProviderSummaryOut.of(
            await pb.provider_balances(settings, session, timeout=SUMMARY_TIMEOUT_S)
        )
    except Exception as exc:
        logger.warning("Provider summary unavailable (%s)", type(exc).__name__)
        return None
