"""One page-address glob for everything an owner types into "which pages" (conversation C9).

Rules, the same in `widget/static/widget-core.js` (both read `tests/fixtures/conversation/
page_globs.json`):

- `**` matches any characters, `*` any characters except `/`, `?` one character except `/`;
  every other character is literal (dots too); matching is anchored and case-sensitive.
- A glob that starts with `http` is matched against the whole address (without `#fragment`);
  any other glob is matched against the path plus the query string (`/tests/lipid?x=1`).
"""

import re
from functools import cache


@cache
def _regex(glob: str) -> re.Pattern[str]:
    out: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*" and glob[i + 1 : i + 2] == "*":
            out.append(".*")
            i += 2
            continue
        out.append("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.DOTALL)


_ADDRESS = re.compile(r"^[a-z][a-z0-9+.-]*://[^/]+(/[^#]*)?", re.IGNORECASE)


def _target(glob: str, url: str) -> str:
    address = url.split("#", 1)[0]
    if glob.startswith("http"):
        return address
    found = _ADDRESS.match(address)
    # Path plus query. A string that is not an address has no page: only `**` matches it.
    return (found.group(1) or "/") if found else ""


def matches(glob: str, url: str) -> bool:
    return bool(_regex(glob).match(_target(glob, url)))
