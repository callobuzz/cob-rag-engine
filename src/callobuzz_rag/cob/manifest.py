"""`docs/cob/manifest.yaml`: which files make up COB's documentation, and who may read each.

The same manifest feeds COB's knowledge base and the in-app Docs section, so the two can never
disagree. Everything is validated at load: a typo in a section override fails the build instead
of silently leaking admin-only text.
"""

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from callobuzz_rag.cob.types import Audience
from callobuzz_rag.core.exceptions import RAGKernelError

MANIFEST_RELATIVE = Path("docs") / "cob" / "manifest.yaml"
_GLOB_CHARS = ("*", "?", "[")


class ManifestError(RAGKernelError):
    """The docs manifest or one of its sources is not valid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ManifestTopic(_Strict):
    id: str
    title: str
    audience: Audience | None = None


class SectionOverride(_Strict):
    audience: Audience | None = None
    topic: str | None = None


class ManifestSource(_Strict):
    id: str
    path: str
    kind: Literal["html", "markdown", "generated"]
    split: Literal["section", "page"]
    slug: str
    topic: str
    audience: Audience | None = None
    public_url: str | None = None
    sections: dict[str, SectionOverride] = {}

    @property
    def is_glob(self) -> bool:
        return any(c in self.path for c in _GLOB_CHARS)


class Manifest(_Strict):
    version: int
    topics: list[ManifestTopic]
    sources: list[ManifestSource]

    def topic_ids(self) -> set[str]:
        return {t.id for t in self.topics}


def docs_root() -> Path:
    """The repository (or image) root: the nearest parent of this package holding
    `pyproject.toml`, the same idea as `core/config.py:_project_env_file`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise ManifestError("Cannot find the project root (a folder with pyproject.toml).")


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_RELATIVE


def source_files(root: Path, source: ManifestSource) -> list[Path]:
    """The files a source stands for, sorted. A glob must match at least one file."""
    if source.is_glob:
        files = sorted(p for p in root.glob(source.path) if p.is_file())
        if not files:
            raise ManifestError(f"Source '{source.id}': '{source.path}' matches no file.")
        return files
    path = root / source.path
    if not path.is_file():
        raise ManifestError(f"Source '{source.id}': '{source.path}' does not exist.")
    return [path]


def load_manifest(root: Path | None = None) -> Manifest:
    root = root or docs_root()
    path = manifest_path(root)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        manifest = Manifest.model_validate(raw)
    except OSError as exc:
        raise ManifestError(f"Cannot read {MANIFEST_RELATIVE}: {exc}") from exc
    except (yaml.YAMLError, ValidationError) as exc:
        raise ManifestError(f"Invalid {MANIFEST_RELATIVE}: {exc}") from exc
    topics = manifest.topic_ids()
    if len(topics) != len(manifest.topics):
        raise ManifestError("Manifest topic ids must be unique.")
    ids = [s.id for s in manifest.sources]
    if len(set(ids)) != len(ids):
        raise ManifestError("Manifest source ids must be unique.")
    for source in manifest.sources:
        if source.topic not in topics:
            raise ManifestError(f"Source '{source.id}' names the unknown topic '{source.topic}'.")
        for anchor, override in source.sections.items():
            if override.topic and override.topic not in topics:
                raise ManifestError(
                    f"Source '{source.id}', section '{anchor}': unknown topic '{override.topic}'."
                )
        source_files(root, source)
    return manifest


def _normalized(data: bytes) -> bytes:
    # A checkout with Windows line endings must hash like the same files in the image.
    return data.replace(b"\r\n", b"\n")


def manifest_hash(root: Path, manifest: Manifest) -> str:
    """sha256 over the manifest bytes and every source file's path and bytes (sorted by path)."""
    digest = hashlib.sha256()
    digest.update(_normalized(manifest_path(root).read_bytes()))
    files: dict[str, Path] = {}
    for source in manifest.sources:
        for file in source_files(root, source):
            files[file.relative_to(root).as_posix()] = file
    for rel in sorted(files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_normalized(files[rel].read_bytes()))
    return digest.hexdigest()
