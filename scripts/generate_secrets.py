"""Fill every required secret in the project's env file.

    python scripts/generate_secrets.py            # creates it from the template if missing
    python scripts/generate_secrets.py --force    # regenerate ALL secrets (dangerous)

`make setup` (scripts/setup.py) is the friendlier way in: it asks its questions and then
generates the secrets with the GENERATORS table below. This script stays for CI, for
rotating or filling secrets on their own, and for people who edit the file by hand.

Standard library only, so it runs before any virtualenv exists.

Only EMPTY values are filled; a value you already set is never touched. That
matters: RAG_DB_PASSWORD is read by Postgres only when its data directory is
first initialised, and RAG_ENCRYPTION_KEY protects stored LLM keys — changing
either on an existing deployment breaks it. `--force` exists for a fresh
install only.
"""

import argparse
import base64
import contextlib
import os
import re
import secrets
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_NAME = "." + "env"
TEMPLATE = ROOT / (ENV_NAME + ".example")
TARGET = ROOT / ENV_NAME


def _hex(n: int = 24) -> Callable[[], str]:
    # Hex, not base64: `/`, `+` and `=` need escaping in connection URLs and
    # some tools mangle them. 24 bytes = 192 bits.
    return lambda: secrets.token_hex(n)


def _fernet() -> str:
    # Exactly what cryptography.fernet.Fernet.generate_key() returns.
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


GENERATORS: dict[str, Callable[[], str]] = {
    "RAG_DB_PASSWORD": _hex(),
    "RAG_REDIS_PASSWORD": _hex(),
    "RAG_QDRANT_API_KEY": _hex(),
    "RAG_ADMIN_API_KEY": lambda: "admin_" + secrets.token_hex(24),
    "RAG_ENCRYPTION_KEY": _fernet,
    "RAG_SESSION_SECRET": _hex(32),  # 64 hex chars; signs dashboard sign-ins
    "RAG_WEBSITE_RENDER_TOKEN": _hex(32),  # BIZ1: the scraper's render endpoint
    "RAG_LANGFUSE_DB_PASSWORD": _hex(),
    "RAG_LANGFUSE_NEXTAUTH_SECRET": _hex(32),
    "RAG_LANGFUSE_SALT": _hex(32),
}

# KEY=value, optionally followed by whitespace + `# comment`.
LINE = re.compile(r"^(?P<key>[A-Z0-9_]+)=(?P<value>[^#\n]*?)(?P<rest>\s+#.*)?$")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="overwrite existing secrets")
    args = parser.parse_args()

    if not TARGET.exists():
        if not TEMPLATE.exists():
            print(f"error: {TEMPLATE.name} not found in {ROOT}", file=sys.stderr)
            return 1
        shutil.copyfile(TEMPLATE, TARGET)
        print(f"created {TARGET.name} from {TEMPLATE.name}")

    lines = TARGET.read_text(encoding="utf-8").splitlines(keepends=True)
    filled: list[str] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        m = LINE.match(line.rstrip("\r\n"))
        if not m or m["key"] not in GENERATORS:
            continue
        key = m["key"]
        seen.add(key)
        if m["value"].strip() and not args.force:
            continue
        eol = line[len(line.rstrip("\r\n")) :]
        lines[i] = f"{key}={GENERATORS[key]()}{m['rest'] or ''}{eol}"
        filled.append(key)

    missing = [k for k in GENERATORS if k not in seen]
    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n# --- added by scripts/generate_secrets.py ---\n")
        for key in missing:
            lines.append(f"{key}={GENERATORS[key]()}\n")
            filled.append(key)

    TARGET.write_text("".join(lines), encoding="utf-8")
    with contextlib.suppress(OSError):
        TARGET.chmod(0o600)

    if filled:
        print(f"generated {len(filled)} secret(s) in {TARGET.name}:")
        for key in filled:
            print(f"  {key}")
        print(f"Your admin API key is RAG_ADMIN_API_KEY in {TARGET.name}.")
        print("Back up RAG_ENCRYPTION_KEY: losing it makes stored LLM keys unreadable.")
    else:
        print(f"all secrets in {TARGET.name} already set; nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
