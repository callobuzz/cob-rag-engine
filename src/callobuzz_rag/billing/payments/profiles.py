"""A workspace's tax details (who a tax invoice is made out to) and the seller's side of a supply.

The workspace edits its own row (owner only); the GST split needs the state of the buyer and the
state of the seller (`core/company.py`, `invoicing` engine setting). Place of supply follows
CBIC Circular 242/2024: the GSTIN's state when there is one, else the recorded state; a buyer
outside India is an export.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from callobuzz_rag.accounts.settings_store import EngineSettings
from callobuzz_rag.api.errors import APIError
from callobuzz_rag.api.schemas.payments import TaxDetailsOut, TaxDetailsRequest
from callobuzz_rag.billing.invoicing import gst
from callobuzz_rag.billing.invoicing.gstin import GstinError, check, state_of
from callobuzz_rag.core.company import OTHER_COUNTRY_STATE, STATES
from callobuzz_rag.db.models.payment import BillingProfile

_FIELDS = (
    "legal_name",
    "gstin",
    "address_line1",
    "address_line2",
    "city",
    "state_code",
    "pincode",
    "country",
    "email_for_invoices",
)


async def get_profile(session: AsyncSession, tenant_id: uuid.UUID) -> BillingProfile | None:
    return (
        await session.execute(select(BillingProfile).where(BillingProfile.tenant_id == tenant_id))
    ).scalar_one_or_none()


def is_complete(row: BillingProfile | None) -> bool:
    """A name, an address and (in India) a state are what an invoice needs."""
    if row is None:
        return False
    if not (row.legal_name and row.address_line1 and row.city):
        return False
    return row.country != "IN" or row.state_code is not None


def to_out(row: BillingProfile | None) -> TaxDetailsOut:
    if row is None:
        return TaxDetailsOut()
    return TaxDetailsOut(
        legal_name=row.legal_name,
        gstin=row.gstin,
        address_line1=row.address_line1,
        address_line2=row.address_line2,
        city=row.city,
        state_code=row.state_code,
        state_name=STATES.get(row.state_code or ""),
        pincode=row.pincode,
        country=row.country,
        email_for_invoices=row.email_for_invoices,
        complete=is_complete(row),
    )


def normalise(body: TaxDetailsRequest) -> dict[str, str | None]:
    """The values to store: a valid GSTIN's state overrides the typed state.

    Raises:
        APIError: 422 `invalid_gstin` (extra `reason`: format, state, checksum) or a GSTIN given
            for a country other than India.
    """
    values: dict[str, str | None] = {name: getattr(body, name) for name in _FIELDS}
    if body.gstin is not None:
        try:
            values["gstin"] = check(body.gstin)
        except GstinError as exc:
            raise APIError(
                422,
                "That GST number doesn't look right. Please check it and try again.",
                "invalid_gstin",
                reason=exc.reason,
            ) from exc
        if body.country != "IN":
            raise APIError(
                422,
                "A GST number can only be given for an address in India.",
                "invalid_gstin",
                reason="country",
            )
        values["state_code"] = state_of(str(values["gstin"]))
    return values


async def save_profile(
    session: AsyncSession, tenant_id: uuid.UUID, body: TaxDetailsRequest
) -> tuple[BillingProfile, list[str]]:
    """Insert or update the workspace's row; returns it and the names of the fields that changed."""
    values = normalise(body)
    row = await get_profile(session, tenant_id)
    now = datetime.now(UTC)
    if row is None:
        row = BillingProfile(tenant_id=tenant_id, **values)
        row.created_at = now
        row.updated_at = now
        session.add(row)
        await session.flush()
        return row, [name for name in _FIELDS if values[name] is not None]
    changed = [name for name in _FIELDS if getattr(row, name) != values[name]]
    for name in changed:
        setattr(row, name, values[name])
    if changed:
        row.updated_at = now
        await session.flush()
    return row, changed


@dataclass(frozen=True, slots=True)
class Supply:
    """What the tax of one sale depends on."""

    kind: gst.Kind
    place_of_supply: str


def supply_for(
    profile: BillingProfile, settings: EngineSettings, *, today: date | None = None
) -> Supply:
    """Kind of supply and place of supply for a sale to `profile` by the deployer."""
    seller = settings.invoicing
    company = settings.company
    seller_state = state_of(seller.gstin) if seller.gstin else (company.state_code or "")
    on = today or datetime.now(UTC).date()
    lut_valid = bool(seller.lut_number) and (
        seller.lut_valid_until is None or seller.lut_valid_until >= on
    )
    buyer_state = state_of(profile.gstin) if profile.gstin else profile.state_code
    kind = gst.supply_kind(
        seller_registered=bool(seller.gstin),
        seller_state=seller_state,
        buyer_state=buyer_state,
        buyer_country=profile.country,
        lut_valid=lut_valid,
    )
    place = OTHER_COUNTRY_STATE if profile.country != "IN" else (buyer_state or "")
    return Supply(kind, place)
