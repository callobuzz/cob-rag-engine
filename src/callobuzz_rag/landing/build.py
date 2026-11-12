"""The front-end build as the pages see it (LP1).

`services/dashboard/landing/` builds the website's small CSS and JS and its fonts into
`landing/static/build/` (committed). Vite writes `manifest.json` there; this module reads it so a
template can say `asset("core")`. A missing build never breaks a page: the content renders
without the files and one warning is logged.
"""

import functools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from callobuzz_rag.landing.fonts import FontManifest

logger = logging.getLogger(__name__)

BUILD_DIR: Final = Path(__file__).parent / "static" / "build"
URL_PREFIX: Final = "/_landing/build/"
_warned = False


@dataclass(frozen=True)
class Entry:
    js: list[str] = field(default_factory=list)
    css: list[str] = field(default_factory=list)


@functools.cache
def manifest() -> dict[str, Any]:
    """Vite's manifest, or `{}` when the build has not been run."""
    global _warned
    path = BUILD_DIR / "manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if not _warned:
            logger.warning("landing build not found at %s: pages render without CSS or JS", path)
            _warned = True
        return {}
    return data if isinstance(data, dict) else {}


@functools.cache
def fonts() -> FontManifest:
    """`{family key: {subset: file}}` written by the font step of the build."""
    try:
        data = json.loads((BUILD_DIR / "fonts" / "fonts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def reload() -> None:
    """Forget what was read (tests, and a new build under a running process)."""
    manifest.cache_clear()
    fonts.cache_clear()


def entry_files(data: dict[str, Any], name: str) -> list[str]:
    """Every file one entry needs, shared chunks first, each once."""
    out: list[str] = []

    def walk(key: str) -> None:
        item = data.get(key)
        if not isinstance(item, dict):
            return
        for imported in item.get("imports", []):
            walk(imported)
        for path in [item.get("file"), *item.get("css", [])]:
            if isinstance(path, str) and path not in out:
                out.append(path)

    for key, item in data.items():
        if isinstance(item, dict) and item.get("isEntry") and item.get("name") == name:
            walk(key)
    return out


def entry(name: str) -> Entry:
    """The scripts and styles of one entry as URLs (`core`, `home`, `pages`)."""
    files = entry_files(manifest(), name)
    # Vite lists the styles of a script entry in "css"; a CSS-only entry is its own file.
    return Entry(
        js=[URL_PREFIX + f for f in files if f.endswith(".js")],
        css=[URL_PREFIX + f for f in files if f.endswith(".css")],
    )


def build_file(name: str) -> Path | None:
    """The file behind `/_landing/build/<name>`, only when the manifest (or the font manifest)
    names it. Never a path the request invented."""
    known = {
        path
        for item in manifest().values()
        if isinstance(item, dict)
        for path in [item.get("file"), *item.get("css", []), *item.get("assets", [])]
        if isinstance(path, str)
    }
    for subsets in fonts().values():
        known.update(subsets.values())
    if name not in known:
        return None
    target = (BUILD_DIR / name).resolve()
    if BUILD_DIR.resolve() not in target.parents or not target.is_file():
        return None
    return target
