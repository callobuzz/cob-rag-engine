"""R7, the tax document check, as pure rules (P2 T3). The database side is covered on PostgreSQL
in `tests/integration/test_invoices_issue_db.py`."""

import uuid

from callobuzz_rag.billing.reconcile import DocumentFact, DocumentFacts, check_documents

TENANT, ACCOUNT, PAYMENT, REFUND = (uuid.uuid4() for _ in range(4))


def test_nothing_missing_is_fine() -> None:
    assert check_documents(DocumentFacts([], [])) == []


def test_r7a_a_captured_payment_without_an_invoice() -> None:
    [finding] = check_documents(
        DocumentFacts([DocumentFact(TENANT, ACCOUNT, PAYMENT)], []),
    )
    assert finding.check == "R7a" and not finding.informational
    assert finding.detail == {"payment_id": str(PAYMENT), "reason": "invoice_missing"}
    assert (finding.tenant_id, finding.account_id) == (TENANT, ACCOUNT)


def test_r7b_a_processed_refund_without_a_credit_note() -> None:
    [finding] = check_documents(
        DocumentFacts([], [DocumentFact(TENANT, ACCOUNT, PAYMENT, REFUND)]),
    )
    assert finding.check == "R7b" and not finding.informational
    assert finding.detail["refund_id"] == str(REFUND)
    assert finding.detail["reason"] == "credit_note_missing"
