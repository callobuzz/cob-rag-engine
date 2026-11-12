#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.txt: every third-party component we ship, with its licence text.

Standard library only (plus `tomllib`), so it runs under any interpreter. Three steps:

  1. Dump the installed Python distributions of the shipped images (Linux, the platform buyers
     install on; never a Windows development venv)::

         docker run --rm --no-deps -v "$PWD/scripts:/s:ro" --entrypoint python \\
             callobuzz-rag:local /s/third_party_notices.py --dump-json > engine.json
         docker run --rm --no-deps -v "$PWD/scripts:/s:ro" --entrypoint python \\
             callobuzz-rag-scraper:local /s/third_party_notices.py --dump-json > scraper.json

  2. Dump the dashboard's production packages::

         pnpm --dir services/dashboard licenses list --prod --json > pnpm.json

  3. Render (or `--check` that the committed file is current, which the release build does)::

         .venv/Scripts/python scripts/third_party_notices.py --engine-json engine.json \\
             --scraper-json scraper.json --pnpm-json pnpm.json \\
             --services third_party/services.toml --out THIRD_PARTY_NOTICES.txt

The output is deterministic: sorted, de-duplicated by identical licence text, never invented. A
component whose package carries no licence text is flagged as such, not given a guessed one.
"""

import argparse
import json
import re
import sys
import tomllib
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OWN_PACKAGES = frozenset({"callobuzz-rag", "callobuzz-scraper", "cob-rag-client"})
LICENCE_FILE = re.compile(r"^(LICEN[CS]E|NOTICE|COPYING|AUTHORS)", re.IGNORECASE)
MAX_TEXT = 200_000

KIND_ORDER = ("python", "python-eval", "js", "service", "model", "font", "data")
KIND_TITLES = {
    "python": "Python packages (the engine and the web scraper)",
    "python-eval": "Python packages installed only when you choose answer-quality evaluation",
    "js": "Dashboard (JavaScript packages in the production build)",
    "service": "Container images and services",
    "model": "AI models",
    "font": "Fonts",
    "data": "Data files",
}
KIND_NOTES = {
    "service": (
        "This image is pulled from its publisher when you run Docker Compose; it is not part of "
        "the zip. Its licence is published by the project."
    ),
    "model": (
        "This model is fetched from its publisher by the software, or ships inside a package "
        "listed above. Its licence is published by the model's authors."
    ),
    "font": "Bundled with the dashboard. Licence: SIL Open Font License 1.1.",
    "data": "A data file used by the software. Its licence is published by its authors.",
}


@dataclass(frozen=True, slots=True)
class Component:
    kind: str
    name: str
    version: str
    licence: str
    text: str | None
    note: str | None = None


def normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---------------------------------------------------------------------------- python


def dump_installed() -> dict[str, dict[str, Any]]:
    """This interpreter's installed distributions (name, version, licence, requires, texts)."""
    from importlib import metadata

    out: dict[str, dict[str, Any]] = {}
    for dist in metadata.distributions():
        meta = dist.metadata
        name = meta["Name"]
        if not name:
            continue
        licence = meta.get("License-Expression") or ""
        if not licence:
            raw = (meta.get("License") or "").strip()
            if raw and "\n" not in raw and len(raw) < 100:
                licence = raw
        if not licence:
            classes = [
                c.rsplit("::", 1)[-1].strip()
                for c in (meta.get_all("Classifier") or [])
                if c.startswith("License ::")
            ]
            licence = " / ".join(c for c in classes if c and c != "OSI Approved") or "UNKNOWN"
        texts: list[str] = []
        for f in dist.files or []:
            if ".dist-info" not in str(f) or not LICENCE_FILE.match(f.name):
                continue
            try:
                body = f.locate().read_bytes().decode("utf-8", errors="replace")
            except OSError:
                continue
            if body.strip():
                texts.append(body.strip())
        text = "\n\n".join(dict.fromkeys(texts))[:MAX_TEXT] or None
        out[normalise(name)] = {
            "name": name,
            "version": dist.version,
            "license": licence,
            "requires": list(dist.requires or []),
            "license_text": text,
        }
    return out


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[([^\]]*)\])?")
# The platform the shipped install runs on (the Dockerfile: python:3.12-slim on Linux x86-64).
TARGET_ENV: Mapping[str, str] = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "os_name": "posix",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "python_version": "3.12",
    "python_full_version": "3.12.0",
}

_COMPARE = re.compile(
    r"""(?P<l>[A-Za-z_]+|'[^']*'|"[^"]*")\s*(?P<op>===|==|!=|<=|>=|~=|<|>|not\s+in|in)\s*"""
    r"""(?P<r>[A-Za-z_]+|'[^']*'|"[^"]*")"""
)


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def _compare(left: str, op: str, right: str) -> bool:
    op = re.sub(r"\s+", " ", op)
    if op in ("in", "not in"):
        return (left in right) == (op == "in")
    if op in ("==", "===", "!="):
        return (left == right) == (op != "!=")
    a, b = _version_key(left), _version_key(right)
    if op == "~=":
        n = max(len(b) - 1, 1)
        return a >= b and a[:n] == b[:n]
    return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]


def marker_applies(
    marker: str, active: frozenset[str] = frozenset(), env: Mapping[str, str] = TARGET_ENV
) -> bool:
    """Whether an environment marker holds for the target platform and the active extras.

    An `extra == 'x'` clause is true only when `x` is active. A marker that cannot be read counts
    as applying, so a package is never dropped from the notices by a parsing gap.
    """
    if not marker.strip():
        return True

    def operand(tok: str) -> str:
        if tok[0] in "'\"":
            return tok[1:-1]
        return env.get(tok, "")

    def clause(m: re.Match[str]) -> str:
        left, right = m.group("l"), m.group("r")
        if left == "extra" or right == "extra":
            value = operand(right if left == "extra" else left)
            return str(normalise(value) in {normalise(x) for x in active})
        return str(_compare(operand(left), m.group("op"), operand(right)))

    expr = _COMPARE.sub(clause, marker)
    if not re.fullmatch(r"(?:\s|\(|\)|True|False|and|or|not)*", expr):
        return True
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))
    except SyntaxError:
        return True


def _parse_requirement(req: str) -> tuple[str, tuple[str, ...], str]:
    spec, _, marker = req.partition(";")
    m = _REQ_NAME.match(spec)
    if not m:
        return "", (), marker
    extras = tuple(e.strip() for e in (m.group(2) or "").split(",") if e.strip())
    return normalise(m.group(1)), extras, marker


def runtime_closure(
    dists: Mapping[str, Mapping[str, Any]], *, root: str, extras: Iterable[str] = ()
) -> list[str]:
    """Names of everything `root` needs at run time, sorted; the root itself is not listed.

    Requirements guarded by an `extra == ...` marker count only when that extra is asked for
    (by `extras` for the root, or by `pkg[extra]` for a dependency). Other environment markers
    are judged for the shipped platform (`TARGET_ENV`: Linux, Python 3.12), so a Windows-only
    package is left out. The dump must come from a Linux environment, because a package that is
    not installed is dropped silently.
    """
    root = normalise(root)
    seen: set[tuple[str, frozenset[str]]] = set()
    names: set[str] = set()
    queue: deque[tuple[str, frozenset[str]]] = deque([(root, frozenset(extras))])
    while queue:
        name, active = queue.popleft()
        if (name, active) in seen:
            continue
        seen.add((name, active))
        dist = dists.get(name)
        if dist is None:
            continue
        for req in dist.get("requires", []):
            dep, dep_extras, marker = _parse_requirement(req)
            if not dep:
                continue
            if not marker_applies(marker, active):
                continue
            if dep != root:
                names.add(dep)
            queue.append((dep, frozenset(dep_extras)))
    return sorted(n for n in names if n in dists)


def _python_component(kind: str, dist: Mapping[str, Any]) -> Component:
    return Component(kind, dist["name"], dist["version"], dist["license"], dist.get("license_text"))


def collect_python(
    dists: Mapping[str, Mapping[str, Any]], *, root: str, extra: str | None = None
) -> list[Component]:
    """The runtime closure of `root` (own packages never listed). With `extra`, only what that
    extra adds on top of the default install, as kind `python-eval`."""
    base = set(runtime_closure(dists, root=root))
    if extra is None:
        names, kind = base, "python"
    else:
        names, kind = set(runtime_closure(dists, root=root, extras=(extra,))) - base, "python-eval"
    return [
        _python_component(kind, dists[n])
        for n in sorted(names)
        if normalise(dists[n]["name"]) not in OWN_PACKAGES
    ]


def with_root(
    dists: dict[str, dict[str, Any]], root: str, pyproject: Path
) -> dict[str, dict[str, Any]]:
    """Make sure `root` exists in `dists`; when it is not installed, build it from its pyproject."""
    key = normalise(root)
    if key in dists:
        return dists
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    optional = data.get("optional-dependencies", {})
    requires = list(data.get("dependencies", []))
    for group, reqs in optional.items():
        requires += [f"{r}; extra == '{group}'" for r in reqs]
    dists[key] = {
        "name": root,
        "version": data.get("version", ""),
        "license": "",
        "requires": requires,
        "license_text": None,
    }
    return dists


# ---------------------------------------------------------------------------- javascript


def collect_js(data: Mapping[str, list[Mapping[str, Any]]]) -> list[Component]:
    """`pnpm licenses list --prod --json`: {licence: [{name, versions, paths, ...}]}."""
    out: dict[tuple[str, str], Component] = {}
    for licence, entries in data.items():
        for entry in entries:
            versions = entry.get("versions") or [""]
            paths = entry.get("paths") or []
            for i, version in enumerate(versions):
                text = _read_licence_dir(Path(paths[i])) if i < len(paths) else None
                out[(entry["name"], version)] = Component(
                    "js", entry["name"], version, licence, text
                )
    return list(out.values())


def _read_licence_dir(folder: Path) -> str | None:
    try:
        files = sorted(p for p in folder.iterdir() if p.is_file() and LICENCE_FILE.match(p.name))
    except OSError:
        return None
    texts = []
    for p in files:
        if p.name.upper().startswith("AUTHORS"):
            continue
        try:
            body = p.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if body:
            texts.append(body)
    return "\n\n".join(dict.fromkeys(texts))[:MAX_TEXT] or None


# ---------------------------------------------------------------------------- services


def collect_services(path: Path) -> list[Component]:
    """Images, models, fonts and data files from `third_party/services.toml`."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: list[Component] = []
    for img in data.get("image", []):
        ref = img["ref"]
        tag = ref.rsplit(":", 1)[-1]
        out.append(
            Component("service", ref, tag, img["licence"], None, f"{img['name']}. {img['url']}")
        )
    for kind, key in (("model", "model"), ("font", "font"), ("data", "data")):
        for item in data.get(key, []):
            text = None
            if item.get("path"):
                try:
                    text = (ROOT / item["path"]).read_text(encoding="utf-8").strip() or None
                except OSError:
                    text = None
            out.append(Component(kind, item["name"], "", item["licence"], text))
    return out


def apply_overrides(comps: list[Component], path: Path) -> list[Component]:
    """`[[overrides]]` in services.toml: a licence read by hand for a package whose metadata is blank."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    fixed = {normalise(o["name"]): o["licence"] for o in data.get("overrides", [])}
    return [
        replace(c, licence=fixed[normalise(c.name)])
        if c.kind.startswith("python") and normalise(c.name) in fixed
        else c
        for c in comps
    ]


# ---------------------------------------------------------------------------- render

HEADER = """\
THIRD-PARTY NOTICES

cob-rag-engine includes or uses the components listed below. Each keeps its own licence; the
licence text that came with each one is reproduced here. cob-rag-engine itself is covered by the
LICENSE file.

Container images (Postgres, Valkey, Qdrant and the others in the section on images) are pulled
from their publishers when you run Docker Compose on your own server. They are not part of the zip.
AI models are downloaded from their publishers, or ship inside a package listed here.

This file is generated by scripts/third_party_notices.py. Do not edit it by hand.
"""


def _label(c: Component) -> str:
    if not c.version or c.version in c.name:
        return c.name
    return f"{c.name} {c.version}"


def render(components: Iterable[Component]) -> str:
    comps = sorted(
        {(c.kind, c.name.lower(), c.version): c for c in components}.values(),
        key=lambda c: (
            KIND_ORDER.index(c.kind) if c.kind in KIND_ORDER else len(KIND_ORDER),
            c.name.lower(),
            c.version,
        ),
    )
    lines = [HEADER.rstrip("\n"), ""]
    seen_text: dict[str, str] = {}
    current = None
    for c in comps:
        if c.kind != current:
            current = c.kind
            title = KIND_TITLES.get(c.kind, c.kind)
            lines += ["", "=" * 78, title.upper(), "=" * 78, ""]
        lines.append(f"{_label(c)} \N{EM DASH} {c.licence}")
        if c.text:
            key = c.text.strip()
            if key in seen_text:
                lines.append(f"[same text as {seen_text[key]} above]")
            else:
                seen_text[key] = c.name
                lines += ["", key.replace("\r\n", "\n")]
        elif c.kind in KIND_NOTES:
            lines.append(KIND_NOTES[c.kind])
            if c.note:
                lines.append(c.note)
        else:
            lines.append(
                f"[licence text not bundled with the package: {c.licence}, see its project page]"
            )
        lines += ["", "-" * 78, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------- cli


def _load(path: str) -> Any:
    # Files written by PowerShell redirection may carry a BOM or UTF-16; accept both.
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    raise SystemExit(f"cannot read {path} as JSON")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dump-json", action="store_true", help="print this environment's packages")
    ap.add_argument("--engine-json")
    ap.add_argument("--scraper-json")
    ap.add_argument("--pnpm-json")
    ap.add_argument("--services", default=str(ROOT / "third_party" / "services.toml"))
    ap.add_argument("--out", default=str(ROOT / "THIRD_PARTY_NOTICES.txt"))
    ap.add_argument("--check", action="store_true", help="exit 1 when --out is out of date")
    args = ap.parse_args(argv)

    if args.dump_json:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        json.dump(dump_installed(), sys.stdout, indent=1, sort_keys=True)
        return 0

    comps: list[Component] = []
    if args.engine_json:
        engine = with_root(_load(args.engine_json), "callobuzz-rag", ROOT / "pyproject.toml")
        comps += collect_python(engine, root="callobuzz-rag")
        comps += collect_python(engine, root="callobuzz-rag", extra="eval")
    if args.scraper_json:
        scraper = with_root(
            _load(args.scraper_json),
            "callobuzz-scraper",
            ROOT / "services" / "scraper" / "pyproject.toml",
        )
        comps += collect_python(scraper, root="callobuzz-scraper")
    if args.pnpm_json:
        comps += collect_js(_load(args.pnpm_json))
    comps += collect_services(Path(args.services))
    comps = apply_overrides(comps, Path(args.services))

    text = render(comps)
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8").replace("\r\n", "\n") if out.exists() else ""
        if current != text:
            print(f"{out} is out of date; regenerate it with scripts/third_party_notices.py")
            return 1
        return 0
    out.write_text(text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
