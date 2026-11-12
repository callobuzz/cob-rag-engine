"""The deployer's own business: who the legal pages, tax invoices and payments speak for.

One model, kept in the `company` engine setting (`accounts/settings_store.py`). Every field is
optional until a feature needs it (payments need a state code, the legal pages need a name).
"""

from typing import Final

from pydantic import ConfigDict, Field, field_validator

from callobuzz_rag.accounts.email_rules import ValidatedEmail
from callobuzz_rag.core.schemas import StrictRequest

# GST state codes, as on the GST portal. "97" is Other Territory; "25" and "28" are kept because
# older GSTINs still carry them (Daman and Diu before 2020, Andhra Pradesh before 2014).
STATES: Final[dict[str, str]] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (before division)",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}

# Place of supply for a buyer outside India (GST portal convention).
OTHER_COUNTRY_STATE: Final = "96"


def check_state_code(value: str | None) -> str | None:
    if value is not None and value not in STATES:
        raise ValueError("is not a GST state code")
    return value


class CompanyDetails(StrictRequest):
    model_config = ConfigDict(extra="forbid")

    legal_name: str | None = Field(None, max_length=200)
    trade_name: str | None = Field(None, max_length=200)
    address_line1: str | None = Field(None, max_length=200)
    address_line2: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    state_code: str | None = None
    pincode: str | None = Field(None, pattern=r"^[1-9][0-9]{5}$")
    country: str = Field("IN", pattern=r"^[A-Z]{2}$")
    email: ValidatedEmail | None = None
    phone: str | None = Field(None, max_length=20)

    @field_validator("state_code")
    @classmethod
    def _state(cls, value: str | None) -> str | None:
        return check_state_code(value)

    def address_lines(self) -> list[str]:
        """The postal address as printable lines (empty parts left out)."""
        city_line = " ".join(part for part in (self.city, self.pincode) if part)
        state = STATES.get(self.state_code or "")
        parts = [self.address_line1, self.address_line2, city_line, state]
        return [p for p in parts if p]
