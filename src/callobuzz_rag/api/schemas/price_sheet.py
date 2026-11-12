"""Bodies of the price list routes (TV3): what a prospect reads, and the link to share it."""

from pydantic import BaseModel, Field


class PriceSheetLimit(BaseModel):
    label: str
    value: str


class PriceSheetPlan(BaseModel):
    name: str
    description: str
    price: str = Field(description='For example "₹999 a month" or "Free"')
    credit: str | None = None
    usage: str
    beyond_limit: str | None = None
    limits: list[PriceSheetLimit]
    upcoming: str | None = Field(None, description='An announced price, "₹1,299 a month from ..."')


class PriceSheetAddOn(BaseModel):
    name: str
    description: str
    price: str
    gives: str


class PriceSheetOut(BaseModel):
    """The price list as a visitor reads it. Never carries a markup or the provider's AI cost."""

    product_name: str
    note: str
    generated_on: str
    plans: list[PriceSheetPlan]
    add_ons: list[PriceSheetAddOn]


class PriceSheetLinkOut(BaseModel):
    url: str = Field(description="The public link; creating a new one stops the old one")
