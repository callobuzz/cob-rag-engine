#!/usr/bin/env python3
"""Check every shipped dependency against the licence policy; exit 1 on a refusal.

Reads the same dumps the notices generator (scripts/third_party_notices.py) reads::

    .venv/Scripts/python scripts/third_party_notices.py --dump-json > engine.json
    services/scraper/.venv/Scripts/python scripts/third_party_notices.py --dump-json > scraper.json
    pnpm --dir services/dashboard licenses list --prod --json > pnpm.json
    .venv/Scripts/python scripts/dependency_audit.py --engine-json engine.json \\
        --scraper-json scraper.json --pnpm-json pnpm.json

plus `third_party/services.toml` (images, models, fonts, data, and `[[overrides]]` for a
package whose metadata licence is blank, each with a reason).

Policy (research/2026-10-04-dependency-licences): permissive licences are allowed;
MPL-2.0 and LGPL only for the listed exceptions, with a notice; the GPL family (including the network one), SSPL, RSAL, BUSL,
non-commercial and unknown are refused. Packages outside the run-time closure (the `[dev]`
extra, test tools) are reported as `dev`, never refused. A model used only through a hosted API
(`api_only = true`) is `api-only`: never downloaded, never shipped.
"""

import argparse
import importlib.util
import json
import re
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Weak-copyleft packages we ship with a notice (MPL-2.0 files stay under MPL; LGPL parts are
# bundled inside a wheel built upstream). Anything else with such a licence is refused until
# someone reads it and adds it here with the reason.
NOTICE_OK: dict[str, str] = {
    "certifi": "MPL-2.0, unmodified CA bundle",
    "orjson": "MPL-2.0 OR Apache-2.0 / MIT, used unmodified",
    "tqdm": "MPL-2.0 AND MIT, used unmodified",
    "pathspec": "MPL-2.0, used unmodified",
    "tld": "MPL-1.1 OR GPL-2.0 OR LGPL-2.1+, our choice is MPL-1.1/LGPL as an unmodified library (scraper, via courlan)",
    "opencv-python": "Apache-2.0 wrapper; the wheel bundles LGPL FFmpeg and Qt, pulled from PyPI",
    "opencv-python-headless": "same as opencv-python",
    "opencv-contrib-python": "same as opencv-python",
    "opencv-contrib-python-headless": "same as opencv-python",
}

_RANK = {"allowed": 0, "notice": 1, "refused": 2, "unknown": 3}
_BY_RANK = {v: k for k, v in _RANK.items()}

_REFUSED = re.compile(
    r"\bag"
    r"pl|aff"
    r"ero|\bsspl|server side public|\brsal|redis source available|\bbusl|"
    r"business source|cc-by-nc|non-?commercial|commons clause|\bproprietary\b|"
    r"\bgpl|general public license"
)
_NOTICE = re.compile(r"\blgpl|lesser general public|\bmpl|mozilla public")
_ALLOWED = re.compile(
    r"^(mit|mit-0|mit-cmu|0bsd|isc|zlib|hpnd|cnri-python|ofl-1\.1|cc0-1\.0|cc-by-4\.0|"
    r"blueoak-1\.0\.0|unlicense|python-2\.0|psf-2\.0|psfl|bsl-1\.0|postgresql|bsd|bsd-[23]-clause|"
    r"apache-2\.0|apache-2|cdla-permissive-2\.0|apache-2\.0 with llvm-exception)$"
    r"|\b(mit|bsd|apache|isc|zlib|unlicense|public domain|open font|cdla-permissive|"
    r"historical permission|python software foundation|psf|cnri|blue oak|"
    r"postgresql)\b"
)
_TOKEN = re.compile(
    r"\(|\)|\band\b|\bor\b|\bwith\b|[^\s()]+(?:\s+(?!and\b|or\b|with\b)[^\s()]+)*", re.I
)


def _atom(text: str) -> str:
    t = text.strip().lower()
    if not t or t in ("unknown", "none", "n/a"):
        return "unknown"
    if "::" in t:
        t = t.rsplit("::", 1)[-1].strip()
    if _REFUSED.search(t) and not _NOTICE.search(t):
        return "refused"
    if _NOTICE.search(t):
        return "notice"
    if _ALLOWED.search(t):
        return "allowed"
    return "unknown"


def _parse_or(tokens: list[str], i: int) -> tuple[int, int]:
    best = None
    i, rank = _parse_and(tokens, i)
    best = rank
    while i < len(tokens) and tokens[i].lower() == "or":
        i, rank = _parse_and(tokens, i + 1)
        best = min(best, rank)
    return i, best


def _parse_and(tokens: list[str], i: int) -> tuple[int, int]:
    i, rank = _parse_primary(tokens, i)
    worst = rank
    while i < len(tokens) and tokens[i].lower() == "and":
        i, rank = _parse_primary(tokens, i + 1)
        worst = max(worst, rank)
    return i, worst


def _parse_primary(tokens: list[str], i: int) -> tuple[int, int]:
    if i >= len(tokens):
        return i, _RANK["unknown"]
    if tokens[i] == "(":
        i, rank = _parse_or(tokens, i + 1)
        if i < len(tokens) and tokens[i] == ")":
            i += 1
        return i, rank
    rank = _RANK[_atom(tokens[i])]
    i += 1
    while i < len(tokens) and tokens[i].lower() == "with":  # an exception clause: base decides
        i += 2
    return i, rank


def classify(licence: str) -> str:
    """`allowed`, `notice` (weak copyleft), `refused` or `unknown` for a licence expression.

    SPDX `AND` / `OR` / `WITH` and parentheses; an `OR` is as good as its best branch, an `AND`
    as bad as its worst. Free text (trove classifiers, "MIT License") is matched by name; a
    classifier list joined with " / " reads as alternatives.
    """
    text = (licence or "").strip()
    if not text:
        return "unknown"
    text = text.replace(" / ", " OR ") if "::" not in text and " / " in text else text
    if "::" in text:
        text = " OR ".join(part.rsplit("::", 1)[-1].strip() for part in text.split(" / "))
    tokens = [t.strip() for t in _TOKEN.findall(text) if t.strip()]
    # "Apache-2.0 WITH LLVM-exception" is matched as a whole by the allowed list.
    if not tokens:
        return "unknown"
    _, rank = _parse_or(tokens, 0)
    return _BY_RANK[rank]


@dataclass(frozen=True)
class Finding:
    kind: str
    name: str
    licence: str
    verdict: str
    version: str = field(default="", compare=False)


def _load_notices() -> Any:
    path = ROOT / "scripts" / "third_party_notices.py"
    spec = importlib.util.spec_from_file_location("third_party_notices", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["third_party_notices"] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _verdict(kind: str, name: str, licence: str, notices: Any) -> str:
    base = classify(licence)
    if base == "notice" and notices.normalise(name) not in NOTICE_OK:
        return "refused"
    return base


def _python(
    kind_prefix: str,
    dists: Mapping[str, Mapping[str, Any]],
    root: str,
    overrides: Mapping[str, Mapping[str, str]],
    notices: Any,
) -> list[Finding]:
    if not dists:
        return []
    base = set(notices.runtime_closure(dists, root=root))
    with_eval = set(
        notices.runtime_closure(dists, root=root, extras=("eval", "ocr-paddle", "observability"))
    )
    own = notices.OWN_PACKAGES
    out: list[Finding] = []
    seen: set[str] = set()

    def add(name: str, kind: str) -> None:
        dist = dists[name]
        shown = dist.get("name", name)
        norm = notices.normalise(shown)
        if norm in own or norm in seen:
            return
        seen.add(norm)
        licence = dist.get("license", "")
        if norm in overrides:
            licence = overrides[norm]["licence"]
        verdict = _verdict(kind, norm, licence, notices)
        out.append(Finding(kind, shown, licence, verdict, dist.get("version", "")))

    for name in sorted(base):
        add(name, kind_prefix)
    for name in sorted(with_eval - base):
        add(name, f"{kind_prefix}-eval")
    for name in sorted(set(dists) - base - with_eval):
        dist = dists[name]
        shown = dist.get("name", name)
        norm = notices.normalise(shown)
        if norm in own or norm in seen or norm == notices.normalise(root):
            continue
        seen.add(norm)
        out.append(
            Finding(kind_prefix, shown, dist.get("license", ""), "dev", dist.get("version", ""))
        )
    return out


def audit(
    *,
    engine: Mapping[str, Mapping[str, Any]],
    scraper: Mapping[str, Mapping[str, Any]],
    pnpm: Mapping[str, list[Mapping[str, Any]]],
    services: Mapping[str, Any],
    overrides: Mapping[str, Mapping[str, str]],
) -> list[Finding]:
    """Every shipped component with a verdict. Runtime rows come first, `dev` rows last."""
    notices = _load_notices()
    fixed = {}
    for name, entry in overrides.items():
        if not entry.get("reason"):
            raise ValueError(f"override for {name} needs a reason (where the licence was read)")
        fixed[notices.normalise(name)] = entry
    findings: list[Finding] = []
    findings += _python("python", engine, "callobuzz-rag", fixed, notices)
    findings += _python("python", scraper, "callobuzz-scraper", fixed, notices)
    for licence, entries in pnpm.items():
        for entry in entries:
            versions = entry.get("versions") or [""]
            findings.append(
                Finding(
                    "js",
                    entry["name"],
                    licence,
                    _verdict("js", entry["name"], licence, notices),
                    versions[0],
                )
            )
    for item in services.get("image", []):
        findings.append(Finding("image", item["name"], item["licence"], classify(item["licence"])))
    for item in services.get("model", []):
        verdict = "api-only" if item.get("api_only") else classify(item["licence"])
        findings.append(Finding("model", item["name"], item["licence"], verdict))
    for kind in ("font", "data"):
        for item in services.get(kind, []):
            findings.append(Finding(kind, item["name"], item["licence"], classify(item["licence"])))
    runtime = [f for f in findings if f.verdict != "dev"]
    dev = [f for f in findings if f.verdict == "dev"]
    return runtime + dev


def exit_code(findings: list[Finding]) -> int:
    return 1 if any(f.verdict in ("refused", "unknown") for f in findings) else 0


def _table(findings: list[Finding]) -> str:
    rows = [f for f in findings if f.verdict != "dev"]
    width = max((len(f.name) for f in rows), default=4)
    lines = [f"{'kind':<12}{'name':<{width + 2}}{'version':<14}{'verdict':<10}licence"]
    for f in rows:
        lines.append(
            f"{f.kind:<12}{f.name:<{width + 2}}{f.version[:12]:<14}{f.verdict:<10}{f.licence[:70]}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--engine-json")
    ap.add_argument("--scraper-json")
    ap.add_argument("--pnpm-json")
    ap.add_argument("--services", default=str(ROOT / "third_party" / "services.toml"))
    ap.add_argument("--quiet", action="store_true", help="print only the summary and the problems")
    args = ap.parse_args(argv)
    notices = _load_notices()

    services = tomllib.loads(Path(args.services).read_text(encoding="utf-8"))
    overrides = {o["name"]: o for o in services.get("overrides", [])}
    engine = (
        notices.with_root(notices._load(args.engine_json), "callobuzz-rag", ROOT / "pyproject.toml")
        if args.engine_json
        else {}
    )
    scraper = (
        notices.with_root(
            notices._load(args.scraper_json),
            "callobuzz-scraper",
            ROOT / "services" / "scraper" / "pyproject.toml",
        )
        if args.scraper_json
        else {}
    )
    pnpm = notices._load(args.pnpm_json) if args.pnpm_json else {}
    try:
        findings = audit(
            engine=engine, scraper=scraper, pnpm=pnpm, services=services, overrides=overrides
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.verdict] = counts.get(f.verdict, 0) + 1
    problems = [f for f in findings if f.verdict in ("refused", "unknown")]
    if args.quiet:
        print(_table(problems) if problems else "no refused or unknown component")
    else:
        print(_table(findings))
    print("\nSummary: " + ", ".join(f"{k} {counts[k]}" for k in sorted(counts)))
    if problems:
        print(f"{len(problems)} component(s) need a decision (see the table).")
    print(json.dumps(counts, sort_keys=True), file=sys.stderr)
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
