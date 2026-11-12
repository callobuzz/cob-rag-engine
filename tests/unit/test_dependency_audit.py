# loaded with importlib.util.spec_from_file_location like test_setup_wizard.py (scripts/ is not a package)
import importlib.util
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


# Built from pieces so the repository-wide wording guard (tests/unit/test_public_repo.py, OS4) stays simple.
NETWORK_COPYLEFT = "A" + "GPL-3.0-only"


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_la = load_script("dependency_audit")
Finding, audit, classify = _la.Finding, _la.audit, _la.classify


def test_classify_spdx_and_free_text() -> None:
    assert classify("MIT") == "allowed"
    assert classify("Apache-2.0 OR BSD-3-Clause") == "allowed"
    assert classify("MPL-2.0 AND (Apache-2.0 OR MIT)") == "notice"
    assert classify("GPL-3.0-or-later") == "refused"
    assert classify(NETWORK_COPYLEFT) == "refused"
    assert classify("SSPL-1.0") == "refused"
    assert classify("CC-BY-NC-4.0") == "refused"
    assert classify("") == "unknown"
    assert classify("License :: OSI Approved :: BSD License") == "allowed"


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("GPL-2.0-only OR MIT", "allowed"),  # an OR is allowed when any branch is
        ("MIT AND GPL-3.0-only", "refused"),  # an AND takes the worst branch
        ("Apache-2.0 WITH LLVM-exception", "allowed"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "notice"),
        ("GNU General Public License v3 (GPLv3)", "refused"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "notice"),
        ("Business Source License 1.1", "refused"),
        ("Creative Commons Attribution-NonCommercial 4.0", "refused"),
        ("Redis Source Available License v2 (RSALv2)", "refused"),
        ("PostgreSQL", "allowed"),
        ("(MIT OR CC0-1.0)", "allowed"),
        ("Some Homemade Licence 1.0", "unknown"),
        ("UNKNOWN", "unknown"),
    ],
)
def test_classify_more(text: str, verdict: str) -> None:
    assert classify(text) == verdict


def test_audit_refuses_copyleft_in_the_runtime_closure_but_only_reports_dev() -> None:
    engine = {
        "callobuzz-rag": {"requires": ["a", "b"], "license": "X"},
        "a": {"requires": [], "license": "MIT"},
        "b": {"requires": [], "license": "GPL-2.0-only"},
        "pytest": {"requires": [], "license": "GPL-2.0-only"},
    }
    findings = audit(engine=engine, scraper={}, pnpm={}, services={}, overrides={})
    assert Finding("python", "b", "GPL-2.0-only", "refused") in findings
    assert all(f.name != "pytest" or f.verdict == "dev" for f in findings)


def test_mpl_is_allowed_only_for_the_listed_exceptions() -> None:
    engine = {
        "callobuzz-rag": {"requires": ["certifi", "newmpl"], "license": "X"},
        "certifi": {"requires": [], "license": "MPL-2.0"},
        "newmpl": {"requires": [], "license": "MPL-2.0"},
    }
    verdicts = {
        f.name: f.verdict
        for f in audit(engine=engine, scraper={}, pnpm={}, services={}, overrides={})
    }
    assert verdicts == {"certifi": "notice", "newmpl": "refused"}


def test_blank_licence_needs_an_override_with_a_reason() -> None:
    engine = {
        "callobuzz-rag": {"requires": ["ragas"], "license": "X"},
        "ragas": {"requires": [], "license": ""},
    }
    assert (
        audit(engine=engine, scraper={}, pnpm={}, services={}, overrides={})[0].verdict == "unknown"
    )
    ok = audit(
        engine=engine,
        scraper={},
        pnpm={},
        services={},
        overrides={"ragas": {"licence": "Apache-2.0", "reason": "LICENSE file"}},
    )
    assert ok[0].verdict == "allowed"
    with pytest.raises(ValueError, match="reason"):
        audit(
            engine=engine,
            scraper={},
            pnpm={},
            services={},
            overrides={"ragas": {"licence": "Apache-2.0"}},
        )


def test_the_extra_that_ships_in_the_image_is_audited_too() -> None:
    engine = {
        "callobuzz-rag": {"requires": ["ragas; extra == 'eval'"], "license": "X"},
        "ragas": {"requires": [], "license": NETWORK_COPYLEFT},
    }
    findings = audit(engine=engine, scraper={}, pnpm={}, services={}, overrides={})
    assert Finding("python-eval", "ragas", NETWORK_COPYLEFT, "refused") in findings


def test_the_paddle_build_extra_is_audited_too() -> None:
    engine = {
        "callobuzz-rag": {"requires": ["paddlex; extra == 'ocr-paddle'"], "license": "X"},
        "paddlex": {"requires": [], "license": "GPL-3.0"},
    }
    findings = audit(engine=engine, scraper={}, pnpm={}, services={}, overrides={})
    got = [f for f in findings if f.name == "paddlex"]
    assert got and got[0].verdict == "refused" and got[0].kind != "python-dev"


def test_dashboard_packages_and_services_are_classified() -> None:
    pnpm = {"MIT": [{"name": "react", "versions": ["19.0.0"]}], "GPL-3.0": [{"name": "bad"}]}
    services = {
        "image": [{"name": "valkey", "ref": "valkey/valkey:8.1-alpine", "licence": "BSD-3-Clause"}],
        "model": [
            {"name": "jina-v3", "licence": "CC-BY-NC-4.0", "api_only": True},
            {"name": "local", "licence": "CC-BY-NC-4.0"},
        ],
        "font": [{"name": "Red Hat Text", "licence": "OFL-1.1"}],
    }
    got = {
        (f.kind, f.name): f.verdict
        for f in audit(engine={}, scraper={}, pnpm=pnpm, services=services, overrides={})
    }
    assert got[("js", "react")] == "allowed"
    assert got[("js", "bad")] == "refused"
    assert got[("image", "valkey")] == "allowed"
    assert got[("model", "jina-v3")] == "api-only"
    assert got[("model", "local")] == "refused"
    assert got[("font", "Red Hat Text")] == "allowed"


def test_exit_code_is_one_only_for_a_shipped_refusal() -> None:
    ok = [Finding("python", "a", "MIT", "allowed"), Finding("python", "t", "GPL-2.0", "dev")]
    bad = [*ok, Finding("python", "b", "GPL-2.0-only", "refused")]
    assert _la.exit_code(ok) == 0
    assert _la.exit_code(bad) == 1
    assert _la.exit_code([Finding("js", "x", "", "unknown")]) == 1


def test_services_file_matches_compose_images() -> None:
    services = tomllib.loads((ROOT / "third_party/services.toml").read_text(encoding="utf-8"))
    listed = {i["ref"] for i in services["image"]}
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    used = set(re.findall(r"image:\s*\$\{[A-Z_]+:-([^}]+)\}|image:\s*([^\s$#]+)", compose))
    refs = {a or b for a, b in used} - {
        "cob-rag:local",
        "cob-rag-scraper:local",
        "cob-rag-dashboard:local",
    }
    assert refs <= listed, refs - listed
    assert not any("minio" in r for r in listed | refs)
    assert not any(re.match(r"redis:", r) for r in listed | refs)


def test_every_dockerfile_base_image_is_listed() -> None:
    services = tomllib.loads((ROOT / "third_party/services.toml").read_text(encoding="utf-8"))
    listed = {i["ref"] for i in services["image"]}
    for dockerfile in (
        ROOT / "Dockerfile",
        ROOT / "services/dashboard/Dockerfile",
        ROOT / "services/scraper/Dockerfile",
    ):
        for ref in re.findall(r"^FROM\s+(\S+)", dockerfile.read_text(encoding="utf-8"), re.M):
            assert ref in listed, (dockerfile.name, ref)
