"""Write COB's generated references: the settings pages and the API pages.

    python scripts/gen_cob_refs.py          # write docs/cob/generated/**
    python scripts/gen_cob_refs.py --check  # exit 1 if they are out of date

The API pages come from docs/openapi.json (python scripts/export_openapi.py), so regenerate
that first when a route changed. The pages are committed and ship in the image;
tests/unit/test_cob_generated_refs.py fails when they are stale.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATED = Path("docs") / "cob" / "generated"


def render_all() -> dict[str, str]:
    from callobuzz_rag.cob.generate.api_ref import render_api_pages
    from callobuzz_rag.cob.generate.settings_ref import render_settings_pages

    return {**render_settings_pages(ROOT), **render_api_pages(ROOT)}


def existing() -> set[str]:
    base = ROOT / GENERATED
    return {p.relative_to(ROOT).as_posix() for p in base.rglob("*.md")} if base.is_dir() else set()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the files are stale")
    args = parser.parse_args()
    files = render_all()
    stale: list[str] = []
    for rel, content in files.items():
        path = ROOT / rel
        current = path.read_bytes().decode("utf-8") if path.is_file() else None
        if current is not None:
            current = current.replace("\r\n", "\n")
        if current != content:
            stale.append(rel)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8"))
    extra = sorted(existing() - set(files))
    if args.check:
        if stale or extra:
            for rel in stale:
                print(f"out of date: {rel}")
            for rel in extra:
                print(f"no longer generated: {rel}")
            print("run: python scripts/gen_cob_refs.py")
            return 1
        print(f"{len(files)} generated pages are current")
        return 0
    for rel in extra:
        print(f"obsolete (delete it by hand): {rel}")
    print(f"wrote {len(stale)} of {len(files)} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
