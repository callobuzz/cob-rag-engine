"""The licence files say what the project is: AGPL-3.0 engine, MIT SDKs and widget (D1),
a commercial licence on request (D4)."""

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from callobuzz_rag.core import product

ROOT = Path(__file__).resolve().parents[2]
WIDGET = ROOT / "src" / "callobuzz_rag" / "widget" / "static"
WIDGET_SCRIPTS = [
    "widget.js",
    "widget-core.js",
    "handoff.js",
    "lead_form.js",
    "markdown.js",
    "sensor.js",
]


def _git_blob(spec: str) -> bytes:
    try:
        out = subprocess.run(["git", "show", spec], cwd=ROOT, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout with history")
    return out.stdout


def test_license_is_the_agpl_text_restored_from_git() -> None:
    text = (ROOT / "LICENSE").read_bytes()
    assert text.startswith(b"                    GNU AFFERO GENERAL PUBLIC LICENSE")
    assert b"Version 3, 19 November 2007" in text
    assert text == _git_blob("a5676a25^:LICENSE")


def test_engine_metadata_is_agpl() -> None:
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert meta["license"] == "AGPL-3.0-only" == product.LICENSE_SPDX
    # PEP 639: the SPDX expression replaces licence classifiers; an old one would contradict it.
    assert not [c for c in meta.get("classifiers", []) if c.startswith("License ::")]


@pytest.mark.parametrize("folder", ["clients/js", "clients/python"])
def test_client_sdks_are_mit(folder: str) -> None:
    text = (ROOT / folder / "LICENSE").read_text("utf-8")
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Callobuzz" in text
    assert "Permission is hereby granted, free of charge" in text
    assert "proprietary" not in text.lower() and "envato" not in text.lower()


def test_client_package_fields_are_mit() -> None:
    assert json.loads((ROOT / "clients/js/package.json").read_text("utf-8"))["license"] == "MIT"
    py = tomllib.loads((ROOT / "clients/python/pyproject.toml").read_text("utf-8"))["project"]
    assert py["license"] == "MIT" == product.CLIENT_LICENSE_SPDX


@pytest.mark.parametrize("name", WIDGET_SCRIPTS)
def test_every_widget_script_says_mit_in_its_first_comment(name: str) -> None:
    head = (WIDGET / name).read_text("utf-8")[:600]
    assert head.startswith("/*!")
    assert "SPDX-License-Identifier: MIT" in head
    assert "proprietary" not in head.lower()


def test_the_served_widget_carries_the_full_mit_notice() -> None:
    script = (WIDGET / "widget.js").read_text("utf-8")
    assert "Permission is hereby granted, free of charge" in script[:3000]


def test_commercial_licence_note() -> None:
    text = (ROOT / "COMMERCIAL-LICENSE.md").read_text("utf-8")
    assert product.COMMERCIAL_LICENSE_URL == "https://callobuzz.com/contact"
    assert product.COMMERCIAL_LICENSE_URL in text
    assert "AGPL-3.0" in text and "MIT" in text
    for word in ("₹", "$", "price", "purchase code", "CodeCanyon", "Envato"):
        assert word.lower() not in text.lower(), word


def test_cla_stays() -> None:
    assert (ROOT / "CLA.md").is_file()
    assert "GNU AGPL-3.0" in (ROOT / "CLA.md").read_text("utf-8")
    assert (ROOT / ".github/workflows/cla.yml").is_file()
