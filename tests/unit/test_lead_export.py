import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from callobuzz_rag.conversation.leads.export import CSV_COLUMNS, iter_csv, lead_row, safe_cell


def _lead(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "created_at": datetime(2026, 10, 14, tzinfo=UTC),
        "status": "new",
        "qualification": None,
        "trigger": "buying_intent",
        "tags": ["callback"],
        "language": "hi",
        "page_url": "https://shree-labs.in",
        "notes_encrypted": None,
        "utm": {"utm_source": "google"},
        "assignee_user_id": None,
        "id": uuid.uuid4(),
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_injection_guard() -> None:
    assert safe_cell('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    for s in ("+91 98", "-1", "@SUM(1)", "\tx", "\rx"):
        assert safe_cell(s).startswith("'")
    assert safe_cell("+919876543210") == "+919876543210"  # a phone number is not a formula
    assert safe_cell("Priya") == "Priya"
    assert safe_cell(None) == ""


def test_rows_and_bom() -> None:
    fields = {"name": "Priya", "phone": "+919876543210", "email": None, "custom": {"t": "HbA1c"}}
    row = lead_row(_lead(), fields, "Patient help", notes=None)
    assert len(row) == len(CSV_COLUMNS) and row[4] == "+919876543210"
    assert row[6] == '{"t": "HbA1c"}' and row[10] == "callback" and row[13] == "google"
    out = "".join(iter_csv([row]))
    assert out.startswith("﻿created_at,reference,knowledge_base,")
    assert "Patient help" in out


def test_a_name_that_looks_like_a_formula_is_neutralised_in_the_file() -> None:
    fields = {"name": "=cmd|' /C calc'!A0", "phone": None, "email": "a@b.in", "custom": {}}
    row = lead_row(_lead(), fields, "KB", notes="@notes")
    out = "".join(iter_csv([row]))
    assert "'=cmd" in out and "'@notes" in out
    assert out.count("\n") == 2  # header and one row: no embedded line break


def test_commas_quotes_and_newlines_stay_inside_their_cell() -> None:
    import csv
    import io

    fields = {"name": 'Priya, "P"\nK', "phone": "+919876543210", "email": None, "custom": {}}
    row = lead_row(_lead(), fields, "KB", notes=None)
    text = "".join(iter_csv([row])).lstrip("﻿")
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[1][3] == 'Priya, "P"\nK' and len(parsed[1]) == len(CSV_COLUMNS)


# TV2 reference
def test_the_reference_column_comes_right_after_created() -> None:
    assert CSV_COLUMNS[:3] == ["created_at", "reference", "knowledge_base"]
    row = lead_row(_lead(), {"name": "Priya"}, "KB", notes=None, ref_code="SD-4F7K")
    assert row[1] == "SD-4F7K"
    assert lead_row(_lead(), {"name": "Priya"}, "KB", notes=None)[1] == ""


# /TV2 reference


# TV5 lead export check: every business can download its leads
def _export_world() -> Any:
    from cryptography.fernet import Fernet

    from tests.unit.scope_harness import scoped_app

    cm = scoped_app()
    app, world = cm.__enter__()
    app.state.settings = app.state.settings.model_copy(
        update={"encryption_key": Fernet.generate_key().decode()}
    )
    return cm, app, world


async def _export_as(who: str, *, advanced: bool, corpus_of: str = "a") -> Any:
    from unittest.mock import AsyncMock, patch

    from callobuzz_rag.conversation.leads.crypto import LeadCrypto
    from callobuzz_rag.core.secrets import FernetEncryptor
    from tests.unit.scope_harness import call
    from tests.unit.test_leads_api import HOOK, _lead

    cm, app, world = _export_world()
    try:
        crypto = LeadCrypto(FernetEncryptor(app.state.settings.encryption_key))
        corpus = world.corpus("a")
        world.plan_features[world.tenant_a.id] = {} if advanced else {"advanced": False}
        lead = _lead(corpus.id, corpus.tenant_id)
        lead.fields_encrypted = crypto.encrypt_json(
            {"name": "Priya Joshi", "phone": "+919822012345", "email": None, "custom": {}}
        )
        with (
            patch(HOOK + "_count_for_export", AsyncMock(return_value=1)),
            patch(HOOK + "list_leads", AsyncMock(return_value=([lead], 1))),
            patch(HOOK + "_corpus_names", AsyncMock(return_value={corpus.id: "Shree Dental"})),
        ):
            return await call(
                app, who, "GET", f"/api/v1/leads/export.csv?corpus_id={world.corpus(corpus_of).id}"
            )
    finally:
        cm.__exit__(None, None, None)


async def test_a_simple_mode_owner_can_export_their_leads() -> None:
    res = await _export_as("owner_a", advanced=False)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    assert res.headers["content-disposition"].startswith("attachment;")
    header = res.text.lstrip("﻿").splitlines()[0]
    assert header.split(",")[:3] == ["created_at", "reference", "knowledge_base"]  # TV2's column


async def test_the_export_is_not_behind_a_plan_feature() -> None:
    """A data route is never hidden by a plan, whatever the deployer switched off."""
    from callobuzz_rag.api.features import route_gate

    assert route_gate("GET", "/api/v1/leads/export.csv") is None


async def test_a_team_member_cannot_bulk_export_contact_details() -> None:
    """Existing rule (C4): downloading every contact is the owner's. The page hides the button
    from members; the engine refuses the route."""
    res = await _export_as("member_a", advanced=False)
    assert res.status_code == 403 and res.json()["code"] == "owner_required"


async def test_another_knowledge_bases_key_cannot_export_these_leads() -> None:
    res = await _export_as("ck_b", advanced=True)
    assert res.status_code == 403, res.text


# /TV5 lead export check
