"""The Handoff model (C5)."""

import uuid

from callobuzz_rag.db.models import Handoff


def test_model_shape() -> None:
    cols = Handoff.__table__.columns
    corpus_fk = next(iter(cols["corpus_id"].foreign_keys))
    assert corpus_fk.ondelete == "CASCADE"
    assert next(iter(cols["tenant_id"].foreign_keys)).ondelete == "CASCADE"
    assert next(iter(cols["conversation_id"].foreign_keys)).ondelete == "SET NULL"
    assert next(iter(cols["lead_id"].foreign_keys)).ondelete == "SET NULL"
    assert not cols["corpus_id"].nullable and cols["office_open"].nullable
    names = {i.name for i in Handoff.__table__.indexes}
    assert {"ix_handoffs_corpus_created", "ix_handoffs_tenant_created", "ix_handoffs_new"} <= names
    row = Handoff(
        corpus_id=uuid.uuid4(), tenant_id=uuid.uuid4(), channel="whatsapp", reason="keyword"
    )
    assert row.summary_encrypted is None
