"""P2 T1: the tax document tables exist as models, are registered and keep no tenant foreign key."""

import pytest
from sqlalchemy import CheckConstraint

from callobuzz_rag.db.models import PaymentRefund, TaxDocument, TaxDocumentCounter
from tests.unit.accounts_fakes import MODELS


@pytest.mark.parametrize("model", [TaxDocument, TaxDocumentCounter])
def test_registered_in_fakes(model: type) -> None:
    assert model in set(MODELS.values())


@pytest.mark.parametrize("column", ["tenant_id", "account_id", "payment_id"])
def test_no_foreign_keys_so_documents_outlive_a_workspace(column: str) -> None:
    assert not TaxDocument.__table__.columns[column].foreign_keys


def test_named_checks_exist() -> None:
    names = {c.name for c in TaxDocument.__table__.constraints if isinstance(c, CheckConstraint)}
    assert {
        "ck_tax_documents_doc_type",
        "ck_tax_documents_credit_note_links",
        "ck_tax_documents_cn_negative",
        "ck_tax_documents_inv_positive",
        "ck_tax_documents_total_sum",
    } <= names


def test_series_is_a_generated_column_and_number_unique_per_series() -> None:
    table = TaxDocument.__table__
    assert table.columns["series"].computed is not None
    uniques = {
        tuple(c.name for c in cons.columns)
        for cons in table.constraints
        if cons.__class__.__name__ == "UniqueConstraint"
    }
    assert ("series", "number") in uniques


def test_counter_key_and_refund_column() -> None:
    assert [c.name for c in TaxDocumentCounter.__table__.primary_key.columns] == [
        "series",
        "prefix",
        "fy",
    ]
    assert "credit_note_skipped" in PaymentRefund.__table__.columns
