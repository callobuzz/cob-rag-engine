"""The notices generator: closure of runtime requirements, deterministic output, honest gaps."""

# `scripts/` is not a package: load it the way tests/unit/test_setup_wizard.py loads scripts/setup.py
import importlib.util
from pathlib import Path

_p = Path(__file__).resolve().parents[2] / "scripts" / "third_party_notices.py"
_spec = importlib.util.spec_from_file_location("third_party_notices", _p)
tpn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpn)
Component, collect_python, render, runtime_closure = (
    tpn.Component,
    tpn.collect_python,
    tpn.render,
    tpn.runtime_closure,
)


def _dist(name, version, licence, requires=(), text="Copyright (c) X\nMIT terms"):
    return {
        "name": name,
        "version": version,
        "license": licence,
        "requires": list(requires),
        "license_text": text,
    }


def test_runtime_closure_follows_requirements_and_ignores_extras_and_dev() -> None:
    dists = {
        "callobuzz-rag": _dist(
            "callobuzz-rag",
            "0.1.0",
            "LicenseRef-Callobuzz-Proprietary",
            ["fastapi>=0.1", "ragas; extra == 'eval'"],
        ),
        "fastapi": _dist("fastapi", "0.115", "MIT", ["starlette"]),
        "starlette": _dist("starlette", "0.40", "BSD-3-Clause"),
        "ragas": _dist("ragas", "0.2", "Apache-2.0"),
        "pytest": _dist("pytest", "8", "MIT"),
    }
    assert runtime_closure(dists, root="callobuzz-rag") == ["fastapi", "starlette"]


def test_the_eval_extra_is_its_own_closure() -> None:
    dists = {
        "callobuzz-rag": _dist(
            "callobuzz-rag", "0.1.0", "X", ["fastapi", "ragas; extra == 'eval'"]
        ),
        "fastapi": _dist("fastapi", "0.115", "MIT"),
        "ragas": _dist("ragas", "0.2", "Apache-2.0", ["fastapi", "datasets"]),
        "datasets": _dist("datasets", "3", "Apache-2.0"),
    }
    assert runtime_closure(dists, root="callobuzz-rag", extras=("eval",)) == [
        "datasets",
        "fastapi",
        "ragas",
    ]
    only = [c.name for c in tpn.collect_python(dists, root="callobuzz-rag", extra="eval")]
    assert only == ["datasets", "ragas"]  # what the default install does not already carry


def test_render_is_deterministic_sorted_and_carries_full_texts() -> None:
    comps = [
        Component("python", "starlette", "0.40", "BSD-3-Clause", "Copyright (c) Encode\nBSD text"),
        Component("python", "fastapi", "0.115", "MIT", "Copyright (c) Tiangolo\nMIT text"),
        Component("service", "valkey/valkey:8.1-alpine", "8.1", "BSD-3-Clause", None),
    ]
    out = render(comps)
    assert out == render(list(reversed(comps)))
    assert out.index("fastapi 0.115") < out.index("starlette 0.40")
    assert "MIT text" in out and "BSD text" in out
    assert "valkey/valkey:8.1-alpine" in out and "pulled from its publisher" in out


def test_identical_texts_are_written_once() -> None:
    same = "Copyright (c) A\nthe same words"
    out = render(
        [
            Component("python", "alpha", "1", "MIT", same),
            Component("python", "beta", "2", "MIT", same),
        ]
    )
    assert out.count("the same words") == 1
    assert "[same text as alpha above]" in out


def test_own_packages_are_never_listed() -> None:
    dists = {
        "callobuzz-rag": _dist("callobuzz-rag", "0.1.0", "LicenseRef-X", ["fastapi"]),
        "fastapi": _dist("fastapi", "0.115", "MIT"),
    }
    comps = collect_python(dists, root="callobuzz-rag")
    assert [c.name for c in comps] == ["fastapi"]
    assert "callobuzz-rag" not in render(comps)


def test_missing_licence_text_is_flagged_not_invented() -> None:
    comps = collect_python(
        {
            "callobuzz-rag": _dist("callobuzz-rag", "0.1.0", "X", ["odd"]),
            "odd": _dist("odd", "1.0", "MIT", text=None),
        },
        root="callobuzz-rag",
    )
    assert "[licence text not bundled with the package: MIT, see its project page]" in render(comps)


def test_markers_are_judged_for_the_linux_target() -> None:
    dists = {
        "callobuzz-rag": _dist(
            "callobuzz-rag",
            "0.1.0",
            "X",
            [
                "click",
                "uvicorn[standard]",
                "pywin32; sys_platform == 'win32'",
                "winonly; platform_system == 'Windows' and python_version >= '3.8'",
            ],
        ),
        "click": _dist("click", "8", "BSD-3-Clause", ["colorama; platform_system == 'Windows'"]),
        "colorama": _dist("colorama", "0.4", "BSD-3-Clause"),
        "pywin32": _dist("pywin32", "312", "PSF"),
        "winonly": _dist("winonly", "1", "MIT"),
        "uvicorn": _dist(
            "uvicorn",
            "0.3",
            "BSD-3-Clause",
            ["uvloop; (sys_platform != 'win32' and extra == 'standard')"],
        ),
        "uvloop": _dist("uvloop", "0.2", "MIT"),
    }
    assert runtime_closure(dists, root="callobuzz-rag") == ["click", "uvicorn", "uvloop"]
    assert tpn.marker_applies("python_version < '3.10'") is False
    assert tpn.marker_applies("python_version >= '3.9' and sys_platform == 'linux'") is True
    assert tpn.marker_applies("some unreadable ?? thing") is True


def test_the_committed_notices_credit_every_component_with_a_licence() -> None:
    text = (Path(__file__).resolve().parents[2] / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    unknown = [ln for ln in text.splitlines() if ln.endswith("\N{EM DASH} UNKNOWN")]
    assert unknown == []
    # Generated from the Linux images, never a Windows development venv.
    assert not any(ln.startswith(("pywin32 ", "colorama ")) for ln in text.splitlines())
