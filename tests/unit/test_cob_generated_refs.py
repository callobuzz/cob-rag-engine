"""COB's generated settings and API references stay current with the code."""

import subprocess
import sys
from pathlib import Path

from callobuzz_rag.cob.generate.settings_ref import parse_env_example
from callobuzz_rag.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_generated_references_are_current() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/gen_cob_refs.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr  # run: python scripts/gen_cob_refs.py


def test_every_setting_is_in_the_settings_reference() -> None:
    text = "\n".join(
        p.read_text(encoding="utf-8") for p in (ROOT / "docs/cob/generated/settings").glob("*.md")
    )
    for name in Settings.model_fields:
        assert f"`RAG_{name.upper()}`" in text, name


def test_settings_pages_are_admin_only() -> None:
    for page in (ROOT / "docs/cob/generated/settings").glob("*.md"):
        head = page.read_text(encoding="utf-8").split("---", 2)[1]
        assert "audience: admin" in head, page.name


def test_admin_paths_are_admin_sections() -> None:
    text = (ROOT / "docs/cob/generated/api/tenants.md").read_text(encoding="utf-8")
    block = text.split("## `GET /api/v1/admin/tenants`", 1)[1]
    assert block.lstrip().startswith("<!-- audience: admin -->")


def test_api_pages_use_the_base_url_placeholder_and_no_real_host() -> None:
    text = (ROOT / "docs/cob/generated/api/widget.md").read_text(encoding="utf-8")
    assert "{{BASE_URL}}/api/v1/" in text and "$COB_RAG_KEY" in text
    assert "127.0.0.1" not in text


def test_portal_pages_carry_the_enabled_note() -> None:
    pages = list((ROOT / "docs/cob/generated/api").glob("portal*.md"))
    assert pages
    for page in pages:
        assert "Content Portal is enabled" in page.read_text(encoding="utf-8")


def test_env_example_parser_groups_and_comments() -> None:
    text = (
        "# ==============================================================================\n"
        "# 2. Email (SMTP)\n"
        "# ==============================================================================\n"
        "# intro\n\n"
        "# SMTP server.\n"
        "RAG_SMTP_HOST=\n"
        "# --- Webhooks ---\n"
        "# Image names.\n"
        "# RAG_A=1\n"
        "# RAG_B=2\n"
    )
    found = {v.name: v for v in parse_env_example(text)}
    assert (
        found["RAG_SMTP_HOST"].group == "Email" and found["RAG_SMTP_HOST"].comment == "SMTP server."
    )
    assert found["RAG_A"].group == "Webhooks" and found["RAG_A"].commented_out
    assert found["RAG_B"].comment == "Image names."


def test_routes_only_the_admin_can_call_are_admin_sections_wherever_they_live() -> None:
    webhooks = (ROOT / "docs/cob/generated/api/webhooks.md").read_text(encoding="utf-8")
    block = webhooks.split("## `POST /api/v1/webhooks`", 1)[1]
    assert block.lstrip().startswith("<!-- audience: admin -->")
    assert "**Who can call it:** The admin key" in block
    migrations = (ROOT / "docs/cob/generated/api/migrations.md").read_text(encoding="utf-8")
    approve = migrations.split("## `POST /api/v1/migrations/{migration_id}/approve`", 1)[1]
    assert approve.lstrip().startswith("<!-- audience: admin -->")
