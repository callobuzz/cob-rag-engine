"""Tax details (a workspace's) and the deployer's company and seller tax profile (P1 T3)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from callobuzz_rag.accounts.settings_store import invalidate_engine_settings_cache
from callobuzz_rag.db.models.payment import BillingProfile
from tests.unit.test_auth_routes import ADMIN, World, auth_app, login, req

URL = "/api/v1/billing/tax-details"
GOOD = {
    "legal_name": "Sharma Furniture",
    "address_line1": "Shop 4, Paud Road",
    "city": "Pune",
    "state_code": "27",
    "pincode": "411038",
}


@pytest.fixture
def world() -> World:
    invalidate_engine_settings_cache()
    w = World()
    w.member(w.user("asha@shree.example"), w.shree, "owner")
    w.member(w.user("ravi@shree.example"), w.shree, "member")
    w.member(w.user("meena@pune.example"), w.pune, "owner")
    return w


@contextmanager
def app_for(world: World) -> Iterator[Any]:
    with auth_app(world) as app:
        yield app


async def call(app: Any, email: str, method: str, body: Any = None, url: str = URL) -> Any:
    token = (await login(app, email)).json()["access_token"]
    return await req(app, method, url, token=token, json=body)


async def test_owner_saves_and_reads_back(world: World) -> None:
    with app_for(world) as app:
        empty = await call(app, "asha@shree.example", "GET")
        saved = await call(app, "asha@shree.example", "PUT", GOOD)
        again = await call(app, "asha@shree.example", "GET")
    assert empty.json()["complete"] is False and empty.json()["legal_name"] == ""
    assert saved.status_code == 200, saved.text
    assert again.json()["complete"] is True
    assert again.json()["state_name"] == "Maharashtra"
    [audit] = world.audits("billing.tax_details_updated")
    assert audit.details["gstin_set"] is False and "legal_name" in audit.details["changed"]
    assert audit.tenant_id == world.shree.id


async def test_saving_the_same_details_writes_no_second_audit_row(world: World) -> None:
    with app_for(world) as app:
        await call(app, "asha@shree.example", "PUT", GOOD)
        await call(app, "asha@shree.example", "PUT", GOOD)
    assert len(world.audits("billing.tax_details_updated")) == 1


async def test_gstin_state_wins_over_the_typed_state(world: World) -> None:
    with app_for(world) as app:
        res = await call(
            app,
            "asha@shree.example",
            "PUT",
            {**GOOD, "state_code": "29", "gstin": " 27aapfu0939f1zv "},
        )
    assert res.status_code == 200, res.text
    assert res.json()["gstin"] == "27AAPFU0939F1ZV" and res.json()["state_code"] == "27"


async def test_a_wrong_gstin_is_a_coded_422(world: World) -> None:
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "PUT", {**GOOD, "gstin": "27ABCDE1234F1Z5"})
    assert res.status_code == 422
    assert res.json()["code"] == "invalid_gstin" and res.json()["reason"] == "checksum"
    assert not world.db.all(BillingProfile)


@pytest.mark.parametrize(
    "patch",
    [
        {"legal_name": "शर्मा फर्नीचर"},  # not Latin script
        {"city": ""},
        {"state_code": None},  # India needs a state
        {"state_code": "99"},
        {"pincode": "012345"},
        {"surprise": 1},
    ],
)
async def test_bad_details_are_422(world: World, patch: dict[str, Any]) -> None:
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "PUT", {**GOOD, **patch})
    assert res.status_code == 422, res.text


async def test_a_buyer_outside_india_needs_no_state(world: World) -> None:
    body = {**GOOD, "country": "AE", "state_code": None, "city": "Dubai"}
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "PUT", body)
    assert res.status_code == 200 and res.json()["complete"] is True


async def test_a_gstin_needs_an_indian_address(world: World) -> None:
    body = {**GOOD, "country": "AE", "state_code": None, "gstin": "27AAPFU0939F1ZV"}
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "PUT", body)
    assert res.status_code == 422 and res.json()["code"] == "invalid_gstin"


async def test_workspaces_are_separate(world: World) -> None:
    with app_for(world) as app:
        await call(app, "asha@shree.example", "PUT", GOOD)
        other = await call(app, "meena@pune.example", "GET")
    assert other.json()["legal_name"] == ""


async def test_a_member_cannot_read_or_change_them(world: World) -> None:
    with app_for(world) as app:
        read = await call(app, "ravi@shree.example", "GET")
        write = await call(app, "ravi@shree.example", "PUT", GOOD)
    assert read.status_code == 403 and read.json()["code"] == "owner_required"
    assert write.status_code == 403 and write.json()["code"] == "owner_required"


async def test_the_admin_is_sent_to_the_admin_pages(world: World) -> None:
    with app_for(world) as app:
        res = await req(app, "GET", URL, key=ADMIN)
    assert res.status_code == 403 and res.json()["code"] == "no_workspace"


# ------------------------------------------------------------------ the deployer's side


async def test_company_details_round_trip_and_are_audited(world: World) -> None:
    body = {"legal_name": "Callobuzz", "state_code": "27", "city": "Pune", "pincode": "411038"}
    with app_for(world) as app:
        put = await req(app, "PUT", "/api/v1/admin/company", key=ADMIN, json=body)
        got = await req(app, "GET", "/api/v1/admin/company", key=ADMIN)
        bad = await req(
            app, "PUT", "/api/v1/admin/company", key=ADMIN, json={**body, "state_code": "99"}
        )
    assert put.status_code == 200 and got.json()["legal_name"] == "Callobuzz"
    assert got.json()["country"] == "IN"
    assert bad.status_code == 422
    [audit] = world.audits("settings.updated")
    assert "company.legal_name" in audit.details["changed"]


async def test_company_routes_are_admin_only(world: World) -> None:
    with app_for(world) as app:
        res = await call(app, "asha@shree.example", "GET", url="/api/v1/admin/company")
    assert res.status_code == 403


async def test_seller_profile_checks_the_gstin_state_against_the_company(world: World) -> None:
    with app_for(world) as app:
        await req(
            app,
            "PUT",
            "/api/v1/admin/company",
            key=ADMIN,
            json={"legal_name": "C", "state_code": "29"},
        )
        mismatch = await req(
            app,
            "PUT",
            "/api/v1/admin/billing/tax-profile",
            key=ADMIN,
            json={"gstin": "27AAPFU0939F1ZV"},
        )
        await req(
            app,
            "PUT",
            "/api/v1/admin/company",
            key=ADMIN,
            json={"legal_name": "C", "state_code": "27"},
        )
        fine = await req(
            app,
            "PUT",
            "/api/v1/admin/billing/tax-profile",
            key=ADMIN,
            json={"gstin": "27AAPFU0939F1ZV", "invoice_prefix": "CB"},
        )
        bad = await req(
            app,
            "PUT",
            "/api/v1/admin/billing/tax-profile",
            key=ADMIN,
            json={"gstin": "27ABCDE1234F1Z5"},
        )
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "gstin_state_mismatch"
    assert fine.status_code == 200, fine.text
    assert fine.json()["number_example"].startswith("CB/") and fine.json()[
        "number_example"
    ].endswith("/00001")
    assert bad.status_code == 422
    [audit] = world.audits("billing.tax_profile_updated")
    assert "gstin" in audit.details["changed"]
