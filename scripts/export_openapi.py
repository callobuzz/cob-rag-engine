"""Write the engine's OpenAPI spec to docs/openapi.json.

    python scripts/export_openapi.py          # write the file
    python scripts/export_openapi.py --check  # exit 1 if the file is out of date

Clients (the SDKs, cob-rag-mcp) generate their types from this file, so it is
committed and kept in sync by tests/unit/test_openapi_spec.py.
"""

import argparse
import json
import sys
from pathlib import Path

SPEC_PATH = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def render() -> str:
    """The spec as the app serves it at /openapi.json, as stable pretty-printed JSON.

    Built from the engine's defaults, never a local settings file, so every
    machine produces the same file. Optional route groups (the Content Portal)
    are switched on so the spec documents everything the engine can serve.
    """
    import os
    from unittest.mock import patch

    from callobuzz_rag.api.app import create_app
    from callobuzz_rag.core.config import Settings

    def _defaults() -> Settings:
        return Settings(_env_file=None, enable_content_portal=True)  # type: ignore[call-arg]

    env = {k: v for k, v in os.environ.items() if not k.startswith("RAG_")}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("callobuzz_rag.api.app.get_settings", _defaults),
        patch("callobuzz_rag.core.config.get_settings", _defaults),
    ):
        spec = create_app().openapi()
    return json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args()
    spec = render()
    if args.check:
        current = SPEC_PATH.read_text(encoding="utf-8") if SPEC_PATH.exists() else ""
        if current != spec:
            sys.stderr.write(f"{SPEC_PATH} is out of date: run python scripts/export_openapi.py\n")
            return 1
        return 0
    SPEC_PATH.write_text(spec, encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {SPEC_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
