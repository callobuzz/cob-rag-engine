"""Request and response bodies for the provider balances (billing BIL1).

Money is an integer number of USD micro-units (1e-6 dollar) in every response, never a float;
a request carries amounts as decimal strings ("50.00"). No response ever carries an admin key or
its ciphertext: `admin_key_set` says whether one is stored.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field, SecretStr

from callobuzz_rag.analytics.provider_balances import ProviderBalance, SpendPoint, worst
from callobuzz_rag.core.schemas import StrictRequest

Status = Literal["ok", "low", "empty", "unknown", "not_configured"]
_AMOUNT = r"^\d{1,9}(\.\d{1,6})?$"


class ProviderSettingsRequest(StrictRequest):
    label: str | None = Field(None, min_length=1, max_length=100)
    manual_balance_usd: str | None = Field(
        None,
        pattern=_AMOUNT,
        description='What is left on the provider, in US dollars ("50.00"); with '
        "`clear_manual: true` it removes the manual balance instead.",
    )
    manual_balance_at: AwareDatetime | None = Field(
        None,
        description="When that balance was true, with a time zone (not in the future); now "
        "when a balance is given without it",
    )
    clear_manual: bool = False
    manual_note: str | None = Field(None, max_length=500, description="null clears the note")
    low_threshold_usd: str | None = Field(
        None, pattern=_AMOUNT, description="Alert below this many dollars; null clears it"
    )
    admin_key: SecretStr | None = Field(None, max_length=500, description="Write-only")
    clear_admin_key: bool = False


class SpendPointOut(BaseModel):
    day: date
    model: str | None
    usd_micro: int

    @classmethod
    def of(cls, p: SpendPoint) -> "SpendPointOut":
        return cls(day=p.day, model=p.model, usd_micro=p.usd_micro)


class ProviderStatusOut(BaseModel):
    """The one-line state of a provider: what the header chip shows."""

    provider: Literal["openrouter", "openai", "anthropic", "manual"]
    label: str
    status: Status
    remaining_usd_micro: int | None = Field(description="Null when it is not known")
    low_threshold_usd_micro: int | None

    @classmethod
    def of(cls, b: ProviderBalance) -> "ProviderStatusOut":
        return cls(
            provider=b.provider,
            label=b.label,
            status=b.status,
            remaining_usd_micro=b.remaining_usd_micro,
            low_threshold_usd_micro=b.low_threshold_usd_micro,
        )


class ProviderSummaryOut(BaseModel):
    """Every configured provider in one line each, and the one that needs attention first."""

    worst: ProviderStatusOut | None
    items: list[ProviderStatusOut]

    @classmethod
    def of(cls, balances: list[ProviderBalance]) -> "ProviderSummaryOut":
        w = worst(balances)
        return cls(
            worst=ProviderStatusOut.of(w) if w is not None else None,
            items=[ProviderStatusOut.of(b) for b in balances if b.status != "not_configured"],
        )


class ProviderBalanceOut(BaseModel):
    id: str | None = Field(description="The stored settings row; null until something is saved")
    provider: Literal["openrouter", "openai", "anthropic", "manual"]
    label: str
    status: Status
    remaining_usd_micro: int | None = Field(
        description="The figure the status is judged on (the provider's own, else the estimate)"
    )
    limit_usd_micro: int | None
    limit_reset: Literal["daily", "weekly", "monthly"] | None
    resets_at: datetime | None
    spend_today_usd_micro: int | None
    spend_week_usd_micro: int | None
    spend_month_usd_micro: int | None
    official_spend: list[SpendPointOut] = Field(description="The provider's own spend by day")
    account_credits_usd_micro: int | None
    manual_balance_usd_micro: int | None
    manual_balance_at: datetime | None
    manual_remaining_usd_micro: int | None = Field(
        description="Manual balance less what the engine recorded since that day"
    )
    manual_since: date | None
    manual_note: str | None
    recorded_month_usd_micro: int = Field(description="The engine's own spend this month")
    unpriced_requests: int = Field(description="Requests since the balance with an unknown price")
    spend_tracked: bool = Field(
        description="False: the engine's own spend is not attributed to this provider"
    )
    low_threshold_usd_micro: int | None
    admin_key_set: bool
    admin_key_source: Literal["env", "stored"] | None = Field(
        description="Where the key in use comes from: the engine's environment (it wins over a "
        "key saved here and cannot be removed here) or one saved here; null when there is none"
    )
    alert_state: Literal["ok", "low", "empty"]
    checked_at: datetime
    error: str | None = Field(description="What went wrong asking the provider (a class name)")

    @classmethod
    def of(cls, b: ProviderBalance) -> "ProviderBalanceOut":
        r = b.reading
        return cls(
            id=b.id,
            provider=b.provider,
            label=b.label,
            status=b.status,
            remaining_usd_micro=b.remaining_usd_micro,
            limit_usd_micro=r.limit_usd_micro if r else None,
            limit_reset=r.limit_reset if r else None,
            resets_at=r.resets_at if r else None,
            spend_today_usd_micro=r.spend_today_usd_micro if r else None,
            spend_week_usd_micro=r.spend_week_usd_micro if r else None,
            spend_month_usd_micro=r.spend_month_usd_micro if r else None,
            official_spend=[SpendPointOut.of(p) for p in r.official_spend] if r else [],
            account_credits_usd_micro=r.account_credits_usd_micro if r else None,
            manual_balance_usd_micro=b.manual_balance_usd_micro,
            manual_balance_at=b.manual_balance_at,
            manual_remaining_usd_micro=b.manual_remaining_usd_micro,
            manual_since=b.manual_since,
            manual_note=b.manual_note,
            recorded_month_usd_micro=b.recorded_month_usd_micro,
            unpriced_requests=b.unpriced_requests,
            spend_tracked=b.spend_tracked,
            low_threshold_usd_micro=b.low_threshold_usd_micro,
            admin_key_set=b.admin_key_set,
            admin_key_source=b.admin_key_source,
            alert_state=b.alert_state,
            checked_at=b.checked_at,
            error=b.error,
        )


class ProviderListResponse(BaseModel):
    items: list[ProviderBalanceOut]
    worst: ProviderStatusOut | None
