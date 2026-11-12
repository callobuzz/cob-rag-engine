"""The `trust` engine-settings section (TV4 step 0): defaults, stored-value filtering, patching."""

from collections.abc import Iterator

import pytest

from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    _parse_stored,
    apply_changes,
    invalidate_engine_settings_cache,
    settings_diff,
)
from tests.unit.scope_harness import scoped_app
from tests.unit.test_engine_settings import _audit_rows, _install, _patch


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    invalidate_engine_settings_cache()
    yield
    invalidate_engine_settings_cache()


def test_trust_defaults_are_the_safe_ones() -> None:
    t = EngineSettings().trust
    assert (t.allow_hide_human_button, t.require_ai_disclosure, t.disclosure_wording_editable) == (
        False,
        True,
        True,
    )
    assert (t.renewal_reminder_days, t.price_change_notice_days) == (7, 30)
    assert t.spend_cap_max_minor == {} and t.support_enabled and t.support_sla_hours is None
    assert t.value_report_enabled and not t.value_report_cc_deployer
    assert t.price_sheet.public_token is None and t.price_sheet.show_add_ons


def test_stored_trust_with_unknown_and_invalid_keys_falls_back_per_field() -> None:
    s = _parse_stored({"trust": {"price_change_notice_days": 3, "gone": 1, "support_sla_hours": 4}})
    assert s.trust.price_change_notice_days == 30
    assert s.trust.support_sla_hours == 4


def test_stored_price_sheet_filters_keys_like_the_billing_block() -> None:
    s = _parse_stored(
        {"trust": {"price_sheet": {"note": "Prices exclude GST.", "gone": 1, "show_add_ons": 5}}}
    )
    assert s.trust.price_sheet.note == "Prices exclude GST."
    assert s.trust.price_sheet.show_add_ons is True  # invalid stored value -> default


def test_stored_garbage_trust_is_defaults() -> None:
    assert _parse_stored({"trust": "nope"}).trust == EngineSettings().trust


def test_patch_cannot_set_the_public_token_through_apply_changes() -> None:
    cur = EngineSettings()
    new = apply_changes(
        cur, {"trust": {"price_sheet": {"public_token": "x", "note": "hi"}}}, allow_http_logo=True
    )
    assert new.trust.price_sheet.public_token is None
    assert new.trust.price_sheet.note == "hi"
    assert settings_diff(cur, new) == {"trust.price_sheet.note": {"from": "", "to": "hi"}}


def test_null_resets_a_trust_field() -> None:
    cur = apply_changes(
        EngineSettings(), {"trust": {"renewal_reminder_days": 3}}, allow_http_logo=True
    )
    assert cur.trust.renewal_reminder_days == 3
    reset = apply_changes(cur, {"trust": {"renewal_reminder_days": None}}, allow_http_logo=True)
    assert reset.trust.renewal_reminder_days == 7


async def test_patch_cannot_set_the_public_price_sheet_token() -> None:
    with scoped_app() as (app, world):
        _install(world)
        res = await _patch(app, {"trust": {"price_sheet": {"public_token": "x"}}})
    assert res.status_code == 422, res.text


@pytest.mark.parametrize(
    "body",
    [
        {"trust": {"price_change_notice_days": 3}},
        {"trust": {"renewal_reminder_days": 31}},
        {"trust": {"nope": True}},
        {"trust": {"spend_cap_max_minor": {"EUR": 5}}},
    ],
)
async def test_invalid_trust_patches_are_422(body: dict) -> None:
    with scoped_app() as (app, world):
        _install(world)
        res = await _patch(app, body)
    assert res.status_code == 422, res.text


async def test_patch_audits_changed_trust_fields() -> None:
    with scoped_app() as (app, world):
        row = _install(world)
        res = await _patch(app, {"trust": {"renewal_reminder_days": 3}})
    assert res.status_code == 200, res.text
    assert res.json()["trust"]["renewal_reminder_days"] == 3
    assert row.value["trust"]["renewal_reminder_days"] == 3
    (audit,) = _audit_rows(world)
    assert audit.action == "settings.updated"
    assert "trust.renewal_reminder_days" in audit.details["changed"]


async def test_get_returns_trust_defaults() -> None:
    from tests.unit.scope_harness import call

    with scoped_app() as (app, world):
        _install(world, {})
        res = await call(app, "admin", "GET", "/api/v1/admin/settings")
    assert res.status_code == 200, res.text
    assert res.json()["trust"]["require_ai_disclosure"] is True
