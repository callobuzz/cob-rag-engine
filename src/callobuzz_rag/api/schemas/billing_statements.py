"""Bodies for statements, the platform P&L and top-up requests (BIL6).

Money is an integer in minor units next to a `currency`; usage lines are in micro units (a
millionth of the major unit) so a fraction of a paisa is not rounded away. A statement never
carries the markup or what the operator's AI cost.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from callobuzz_rag.billing.money import Currency
from callobuzz_rag.billing.statements import StatementData
from callobuzz_rag.core.schemas import StrictRequest

StatementFormat = Literal["json", "csv", "html"]


class StatementSummary(BaseModel):
    """One month in the list: `month` is `YYYY-MM`, the same text the statement URL takes."""

    month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    currency: Currency
    opening_minor: int
    closing_minor: int
    live: bool = Field(..., description="The current month: still changing, shown as so far.")


class StatementListResponse(BaseModel):
    items: list[StatementSummary]


class StatementOut(StatementData):
    """A statement with the name of the workspace it belongs to."""

    workspace: str


class TopUpRequest(StrictRequest):
    """A customer asking the operator to add money (until a payment gateway exists)."""

    amount_minor: int | None = Field(
        None, ge=1, le=10**12, description="The amount they would like to add, if they say."
    )
    note: str | None = Field(None, max_length=500)


class TopUpRequestOut(BaseModel):
    sent: bool = Field(
        ..., description="False when the engine has no operator e-mail to send it to."
    )


class PnlRow(BaseModel):
    tenant_id: uuid.UUID
    name: str
    billed_minor: int
    collected_minor: int
    adjustments_minor: int
    ai_cost_minor: int = Field(..., description="Estimate, in the P&L's currency.")
    ai_cost_known: bool = Field(..., description="False when some AI calls had no known price.")
    unpriced_calls: int = 0
    # P1 payments: what the payment gateway charged (operator only; owners never see it)
    gateway_fee_minor: int = Field(0, description="The gateway's fee before its GST.")
    gateway_fee_tax_minor: int = Field(0, description="The GST on that fee (input credit).")
    # /P1 payments
    margin_minor: int
    margin_pct: float | None = Field(None, description="Margin over billed; null when billed is 0.")
    exempt: bool = False


class PnlTotals(BaseModel):
    billed_minor: int
    collected_minor: int
    adjustments_minor: int
    ai_cost_minor: int
    ai_cost_known: bool
    unpriced_calls: int = 0
    gateway_fee_minor: int = 0  # P1 payments
    gateway_fee_tax_minor: int = 0  # P1 payments
    margin_minor: int
    margin_pct: float | None


class PnlOut(BaseModel):
    """Billed and collected against the AI cost, per customer, in one currency."""

    currency: Currency
    rate: float = Field(..., description="Rupees per US dollar used to convert (today's rate).")
    start: str
    end: str
    months: list[str]
    rows: list[PnlRow]
    totals: PnlTotals
