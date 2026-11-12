# ruff: noqa: F811, F401
"""P2 T7: the price list shows the GST-inclusive total next to every price (no drip pricing)."""

from callobuzz_rag.accounts.settings_store import PriceSheetSettings, SellerTaxProfile
from callobuzz_rag.billing.price_sheet import as_dict, build_sheet, render_html
from callobuzz_rag.db.models.add_on import AddOn
from tests.unit.test_price_sheet import BRANDING, NOW, World, world

REGISTERED = SellerTaxProfile(gstin="27AAPFU0939F1ZV")


async def build(world: World, invoicing: SellerTaxProfile | None, **settings: object):  # type: ignore[no-untyped-def]
    return await build_sheet(
        world.db.session(),
        PriceSheetSettings(**settings),  # type: ignore[arg-type]
        BRANDING,
        now=NOW,
        invoicing=invoicing,
    )


async def test_a_registered_seller_shows_the_total_with_gst_next_to_each_fee(world: World) -> None:
    [starter, growth] = (await build(world, REGISTERED)).plans
    assert growth.price == "₹999 a month" and growth.price_incl == "₹1,178.82 a month incl. GST"
    assert starter.price_incl == "₹588.82 a month incl. GST"


async def test_without_a_gstin_nothing_is_added(world: World) -> None:
    sheet = await build(world, SellerTaxProfile())
    assert all(p.price_incl is None for p in sheet.plans)
    assert all(p.price_incl is None for p in (await build(world, None)).plans)


async def test_a_tax_inclusive_seller_labels_the_listed_price_as_the_total(world: World) -> None:
    sheet = await build(world, SellerTaxProfile(gstin="27AAPFU0939F1ZV", prices_include_gst=True))
    assert sheet.plans[1].price_incl == "₹999 a month incl. GST"


async def test_add_ons_get_the_inclusive_total_too_and_free_plans_do_not(world: World) -> None:
    world.db.add(
        AddOn(
            kind="question_pack",
            name="500 extra questions",
            currency="INR",
            price_minor=50_000,
            questions=500,
            is_active=True,
        )
    )
    sheet = await build(world, REGISTERED, show_add_ons=True)
    assert sheet.add_ons[0].price == "₹500" and sheet.add_ons[0].price_incl == "₹590 incl. GST"
    free = await build(world, REGISTERED, plan_ids=[world.db.default_plan.id])
    assert all(p.price_incl is None for p in free.plans)


async def test_the_json_and_the_page_carry_it(world: World) -> None:
    sheet = await build(world, REGISTERED, show_add_ons=True)
    assert as_dict(sheet)["plans"][1]["price_incl"] == "₹1,178.82 a month incl. GST"
    assert "₹1,178.82 a month incl. GST" in render_html(sheet)
