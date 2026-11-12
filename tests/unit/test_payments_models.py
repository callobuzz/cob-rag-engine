"""P1 T1: the payment tables exist as models, are registered, and keep financial rows tenant-FK free."""

import pytest
from sqlalchemy import UniqueConstraint

from callobuzz_rag.db.models import (
    AddOnGrant,
    BillingProfile,
    Payment,
    PaymentEvent,
    PaymentOrder,
    PaymentProviderSetting,
    PaymentRefund,
)
from tests.unit.accounts_fakes import MODELS

ALL = (BillingProfile, Payment, PaymentEvent, PaymentOrder, PaymentProviderSetting, PaymentRefund)


def _unique_columns(model: type) -> set[frozenset[str]]:
    table = model.__table__  # type: ignore[attr-defined]
    found = {
        frozenset(c.name for c in cons.columns)
        for cons in table.constraints
        if isinstance(cons, UniqueConstraint)
    }
    found |= {frozenset([c.name]) for c in table.columns if c.unique}
    return found


@pytest.mark.parametrize("model", ALL)
def test_registered_in_fakes(model: type) -> None:
    assert model in MODELS.values() or model in set(MODELS.values())


def test_provider_payment_id_and_event_id_are_unique() -> None:
    assert frozenset({"provider_payment_id"}) in _unique_columns(Payment)
    assert frozenset({"event_id"}) in _unique_columns(PaymentEvent)
    assert frozenset({"tenant_id", "idempotency_key"}) in _unique_columns(PaymentOrder)


@pytest.mark.parametrize("model", [PaymentOrder, Payment, PaymentRefund])
def test_financial_rows_have_no_tenant_foreign_key(model: type) -> None:
    columns = model.__table__.columns  # type: ignore[attr-defined]
    assert not columns["tenant_id"].foreign_keys
    assert not columns["account_id"].foreign_keys


def test_billing_profile_goes_with_the_workspace() -> None:
    (fk,) = BillingProfile.__table__.columns["tenant_id"].foreign_keys
    assert fk.ondelete == "CASCADE"


def test_grant_payment_check_allows_gateway() -> None:
    texts = [str(c.sqltext) for c in AddOnGrant.__table__.constraints if hasattr(c, "sqltext")]
    assert any("gateway" in t and "payment in" in t for t in texts)
    assert "provider_payment_id" in AddOnGrant.__table__.columns
