"""Billing settings: env switches and the `EngineSettings.billing` section."""

import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from callobuzz_rag.accounts.settings_store import (
    EngineSettings,
    _parse_stored,
    apply_changes,
    settings_diff,
)
from callobuzz_rag.billing import config as billing_config
from callobuzz_rag.core.config import Settings


def test_defaults() -> None:
    s = Settings(_env_file=None)
    assert s.billing_enabled is True
    assert s.billing_post_interval_seconds == 60
    assert s.billing_reconcile_cron == "30 3 * * *"


def test_bad_values_refused() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, billing_post_interval_seconds=5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, billing_reconcile_cron="every day")


def test_config_cache_and_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAG_BILLING_ENABLED", "false")
    billing_config.reset_billing_config_cache()
    try:
        assert billing_config.billing_enabled() is False
        with pytest.raises(HTTPException) as exc:
            billing_config.require_billing_enabled(SimpleNamespace(state=SimpleNamespace()))  # type: ignore[arg-type]
        assert exc.value.status_code == 404
        assert exc.value.code == "billing_disabled"  # type: ignore[attr-defined]
    finally:
        monkeypatch.delenv("RAG_BILLING_ENABLED")
        billing_config.reset_billing_config_cache()
    assert billing_config.billing_enabled() is True
    assert billing_config.usd_inr_rate() > 0


def test_stored_json_without_billing_reads_defaults() -> None:
    s = _parse_stored({"signup_mode": "open"})
    assert s.billing.default_currency == "INR"
    assert s.billing.confirm_above_minor == {"INR": 1_000_000, "USD": 10_000}


def test_bad_stored_billing_value_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        s = _parse_stored(
            {
                "signup_mode": "open",
                "billing": {"default_currency": "USD", "confirm_above_minor": {"EUR": 5}},
            }
        )
    assert s.signup_mode == "open"
    assert s.billing.default_currency == "USD"
    assert s.billing.confirm_above_minor == {"INR": 1_000_000, "USD": 10_000}
    assert "ignoring invalid stored value" in caplog.text


def test_confirm_above_rules() -> None:
    for bad in ({"EUR": 5}, {"INR": 0}):
        with pytest.raises(ValidationError):
            EngineSettings.model_validate({"billing": {"confirm_above_minor": bad}})


def test_patch_billing_section_and_diff() -> None:
    cur = EngineSettings()
    new = apply_changes(cur, {"billing": {"default_currency": "USD"}}, allow_http_logo=True)
    assert new.billing.default_currency == "USD"
    assert settings_diff(cur, new) == {"billing.default_currency": {"from": "INR", "to": "USD"}}
    reset = apply_changes(new, {"billing": {"default_currency": None}}, allow_http_logo=True)
    assert reset.billing.default_currency == "INR"
