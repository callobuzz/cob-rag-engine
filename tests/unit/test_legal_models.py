"""P3 T1: the legal tables are registered, and the `legal` engine setting round-trips."""

from callobuzz_rag.accounts.settings_store import EngineSettings, LegalSettings, _parse_stored
from callobuzz_rag.db.models import LegalAcceptance, LegalDocument
from callobuzz_rag.legal.store import SLUGS
from tests.unit.accounts_fakes import MODELS


def test_models_are_registered() -> None:
    assert MODELS["legal_documents"] is LegalDocument
    assert MODELS["legal_acceptances"] is LegalAcceptance


def test_slug_check_lists_exactly_the_slugs() -> None:
    check = next(
        c
        for c in LegalDocument.__table_args__
        if getattr(c, "name", "") == "ck_legal_documents_slug"
    )
    text = str(check.sqltext)
    for slug in SLUGS:
        assert f"'{slug}'" in text
    assert text.count("'") == 2 * len(SLUGS)


def test_acceptance_user_is_set_null_and_document_restrict() -> None:
    table = LegalAcceptance.__table__
    (user_fk,) = table.c.user_id.foreign_keys
    (tenant_fk,) = table.c.tenant_id.foreign_keys
    (doc_fk,) = table.c.document_id.foreign_keys
    assert user_fk.ondelete == "SET NULL"
    assert tenant_fk.ondelete == "SET NULL"
    assert doc_fk.ondelete == "RESTRICT"


def test_legal_settings_defaults_and_round_trip() -> None:
    settings = EngineSettings()
    assert settings.legal.show_in_footer is True
    assert settings.legal.grievance_email is None
    changed = settings.model_copy(
        update={
            "legal": LegalSettings(
                grievance_name="Asha Sharma",
                grievance_email="grievance@sharmafurniture.in",
                jurisdiction_city="Pune",
                website="https://sharmafurniture.in",
            )
        }
    )
    again = _parse_stored(changed.model_dump(mode="json"))
    assert again.legal.grievance_name == "Asha Sharma"
    assert again.legal.jurisdiction_city == "Pune"


def test_bad_stored_legal_value_falls_back_to_default() -> None:
    stored = EngineSettings().model_dump(mode="json")
    stored["legal"] = {"website": "javascript:alert(1)", "grievance_name": "Asha"}
    parsed = _parse_stored(stored)
    assert parsed.legal.website is None
    assert parsed.legal.grievance_name == "Asha"
    assert _parse_stored({"legal": "nonsense"}).legal.show_in_footer is True
