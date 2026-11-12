"""Docker build-context helpers: does every COPY source reach the image build? (stdlib only)."""

import fnmatch
import re
from collections.abc import Iterable, Sequence


# --------------------------------------------------------------------------------------
# Docker build context: does every COPY source reach the image build?
# --------------------------------------------------------------------------------------
def _pattern_regex(pattern: str) -> re.Pattern[str]:
    """Translate one .dockerignore pattern into an anchored regex (Go filepath.Match plus **)."""
    pattern = pattern.strip().strip("/") if pattern.strip() != "/" else pattern
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif ch == "*":
            out.append("[^/]*")
            i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(ch))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def dockerignore_excluded(path: str, lines: Sequence[str]) -> bool:
    """True when `.dockerignore` lines keep `path` out of the build context.

    Docker semantics: a pattern matches the path or any of its parent folders, a leading `!`
    re-includes, and the last matching line wins.
    """
    parts = path.strip("/").split("/")
    prefixes = ["/".join(parts[: i + 1]) for i in range(len(parts))]
    excluded = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:].strip()
        regex = _pattern_regex(line)
        if any(regex.match(p) for p in prefixes):
            excluded = not negate
    return excluded


_COPY_FLAG = re.compile(r"^--[a-z-]+(=\S*)?$")


def copy_sources(dockerfile: str) -> list[str]:
    """Sources of every `COPY` (and `ADD`) that reads from the build context (not `--from=`)."""
    joined = re.sub(r"\\r?\n", " ", dockerfile)
    sources: list[str] = []
    for raw in joined.splitlines():
        line = raw.strip()
        if not re.match(r"(?i)^(COPY|ADD)\s", line):
            continue
        tokens = line.split()[1:]
        flags = [t for t in tokens if _COPY_FLAG.match(t)]
        if any(f.startswith("--from=") for f in flags):
            continue
        args = [t for t in tokens if not _COPY_FLAG.match(t)]
        sources.extend(args[:-1])
    return sources


def unreachable_copy_sources(
    dockerfile: str, tracked: Iterable[str], ignore_lines: Sequence[str]
) -> list[str]:
    """COPY sources with no tracked file that survives the `.dockerignore`."""
    files = [p.strip("/") for p in tracked]
    missing: list[str] = []
    for src in copy_sources(dockerfile):
        norm = src.strip()
        while norm.startswith("./"):
            norm = norm[2:]
        norm = norm.strip("/")
        if norm in ("", "."):
            candidates = files
        elif any(c in norm for c in "*?["):
            candidates = [f for f in files if fnmatch.fnmatchcase(f, norm)] + [
                f
                for f in files
                if any(
                    fnmatch.fnmatchcase("/".join(f.split("/")[: n + 1]), norm)
                    for n in range(len(f.split("/")))
                )
            ]
        else:
            candidates = [f for f in files if f == norm or f.startswith(norm + "/")]
        if not any(not dockerignore_excluded(f, ignore_lines) for f in candidates):
            missing.append(src)
    return missing
