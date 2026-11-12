"""The `---` YAML block at the top of a screen or generated page."""

import re

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from callobuzz_rag.cob.manifest import ManifestError
from callobuzz_rag.cob.types import Audience

_BLOCK = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


class FrontMatter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    route: str | None = None
    audience: Audience | None = None
    title: str | None = None
    topic: str | None = None
    summary: str | None = None
    starters: list[str] = []
    admin_starters: list[str] = []
    slug: str | None = None


def split_front_matter(text: str, where: str) -> tuple[FrontMatter, str]:
    """(front matter, the rest). A file without a block gets an empty one."""
    match = _BLOCK.match(text)
    if not match:
        return FrontMatter(), text
    try:
        raw = yaml.safe_load(match.group(1)) or {}
        if not isinstance(raw, dict):
            raise ManifestError(f"{where}: the front matter must be a mapping.")
        meta = FrontMatter.model_validate(raw)
    except yaml.YAMLError as exc:
        raise ManifestError(f"{where}: invalid front matter: {exc}") from exc
    except ValidationError as exc:
        raise ManifestError(f"{where}: invalid front matter: {exc}") from exc
    return meta, text[match.end() :]
