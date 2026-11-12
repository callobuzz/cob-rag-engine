from sqlalchemy import CheckConstraint, Table

from callobuzz_rag.db.models import Lead, LeadConsent, WebhookConfig
from callobuzz_rag.db.models.lead import LEAD_QUALIFICATIONS, LEAD_STATUSES


def _fk(table: Table, column: str) -> tuple[str, str | None]:
    (fk,) = table.c[column].foreign_keys
    return fk.target_fullname, fk.ondelete


def test_lead_columns_and_cascades() -> None:
    t = Lead.__table__
    assert _fk(t, "corpus_id") == ("corpora.id", "CASCADE")
    assert _fk(t, "tenant_id") == ("tenants.id", "CASCADE")
    assert _fk(t, "conversation_id") == ("conversations.id", "SET NULL")
    assert _fk(t, "widget_key_id") == ("widget_keys.id", "SET NULL")
    assert _fk(t, "assignee_user_id") == ("users.id", "SET NULL")
    for column in ("name", "phone", "email"):
        assert column not in t.c  # contact data only inside fields_encrypted
    assert not t.c.fields_encrypted.nullable
    assert t.c.notified_at.nullable
    checks = " ".join(str(c.sqltext) for c in t.constraints if isinstance(c, CheckConstraint))
    assert all(s in checks for s in LEAD_STATUSES)
    assert all(q in checks for q in LEAD_QUALIFICATIONS)


def test_consent_and_webhook() -> None:
    c = LeadConsent.__table__
    assert _fk(c, "lead_id") == ("leads.id", "SET NULL")
    assert _fk(c, "corpus_id") == ("corpora.id", "CASCADE")
    assert _fk(WebhookConfig.__table__, "corpus_id") == ("corpora.id", "CASCADE")
    assert WebhookConfig.__table__.c.corpus_id.nullable
