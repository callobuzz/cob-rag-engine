"""Back up and restore every Qdrant collection as a snapshot file in a folder.

    python -m callobuzz_rag.vectordb.snapshots create DIR
    python -m callobuzz_rag.vectordb.snapshots restore DIR

`create` asks Qdrant for a snapshot of each collection, downloads it into DIR, checks it, and
removes the copy Qdrant keeps; `manifest.json` (file, sha256, size per collection) is written
last, so a folder without one is an unfinished backup. `restore` verifies every file against
the manifest first and only then uploads them (priority `snapshot`: the snapshot wins).

Exit codes: 0 done, 1 Qdrant error (the message names the collection), 2 bad folder, bad
manifest or checksum mismatch. Used by deploy/backup.sh and deploy/restore.sh.
"""

import argparse
import asyncio
import contextlib
import hashlib
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import httpx

from callobuzz_rag.core.exceptions import RAGKernelError

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
_CHUNK = 1024 * 1024


class SnapshotError(RAGKernelError):
    """A snapshot could not be taken, read back or restored."""

    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def _path(collection: str, *rest: str) -> str:
    return "/".join(
        ["/collections", quote(collection, safe=""), *(quote(r, safe="") for r in rest)]
    )


def _fail_text(response: httpx.Response) -> str:
    try:
        body = response.json()
        detail = body.get("status", {}).get("error") if isinstance(body, dict) else None
    except ValueError:
        detail = None
    return f"HTTP {response.status_code}" + (f": {detail}" if detail else "")


async def _list_collections(client: httpx.AsyncClient) -> list[str]:
    try:
        response = await client.get("/collections")
    except httpx.HTTPError as exc:
        raise SnapshotError(f"Could not reach Qdrant: {exc}") from exc
    if response.status_code != 200:
        raise SnapshotError(f"Qdrant refused the collection list ({_fail_text(response)})")
    return [c["name"] for c in response.json()["result"]["collections"]]


async def _snapshot_one(
    client: httpx.AsyncClient, collection: str, out_dir: Path
) -> dict[str, object]:
    try:
        response = await client.post(_path(collection, "snapshots"), params={"wait": "true"})
        if response.status_code != 200:
            raise SnapshotError(
                f"Qdrant could not snapshot collection {collection} ({_fail_text(response)})"
            )
        name = response.json()["result"]["name"]
        if not name or name != Path(name).name:
            raise SnapshotError(f"Qdrant named the snapshot of {collection} unsafely")
        target = out_dir / name
        part = out_dir / f"{name}.part"
        digest = hashlib.sha256()
        size = 0
        try:
            async with client.stream("GET", _path(collection, "snapshots", name)) as download:
                if download.status_code != 200:
                    await download.aread()
                    raise SnapshotError(
                        f"Could not download the snapshot of collection {collection} "
                        f"({_fail_text(download)})"
                    )
                with part.open("wb") as handle:
                    async for block in download.aiter_bytes(_CHUNK):
                        handle.write(block)
                        digest.update(block)
                        size += len(block)
            os.replace(part, target)
        finally:
            with contextlib.suppress(OSError):
                part.unlink()
        gone = await client.delete(_path(collection, "snapshots", name), params={"wait": "true"})
        if gone.status_code != 200:
            logger.warning("could not remove the server copy of a snapshot (%s)", gone.status_code)
        return {"file": name, "sha256": digest.hexdigest(), "size": size}
    except httpx.HTTPError as exc:
        raise SnapshotError(f"Qdrant failed on collection {collection}: {exc}") from exc


async def create_snapshots(client: httpx.AsyncClient, out_dir: Path) -> list[Path]:
    """Snapshot every collection into `out_dir`; the manifest is written last, atomically."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        (out_dir / MANIFEST).unlink()  # an old manifest must not vouch for a new, unfinished run
    manifest: dict[str, dict[str, object]] = {}
    files: list[Path] = []
    for collection in await _list_collections(client):
        entry = await _snapshot_one(client, collection, out_dir)
        manifest[collection] = entry
        files.append(out_dir / str(entry["file"]))
        _say(f"snapshot {collection}: {entry['size']} bytes")
    part = out_dir / (MANIFEST + ".part")
    part.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(part, out_dir / MANIFEST)
    return files


def _read_manifest(in_dir: Path) -> dict[str, dict[str, object]]:
    path = in_dir / MANIFEST
    if not path.is_file():
        raise SnapshotError(
            f"{in_dir} has no {MANIFEST}: it is not a finished vector backup.", exit_code=2
        )
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SnapshotError(f"{path} is damaged ({exc}).", exit_code=2) from exc
    if not isinstance(manifest, dict):
        raise SnapshotError(f"{path} is damaged.", exit_code=2)
    return manifest


def _verify(in_dir: Path, manifest: dict[str, dict[str, object]]) -> dict[str, Path]:
    """Every file present, inside the folder and matching its checksum, or raise."""
    paths: dict[str, Path] = {}
    for collection, entry in manifest.items():
        name = str(entry.get("file", ""))
        if not name or name != Path(name).name or name.startswith("."):
            raise SnapshotError(
                f"The backup entry for collection {collection} points outside the folder.",
                exit_code=2,
            )
        path = in_dir / name
        if not path.is_file():
            raise SnapshotError(
                f"The snapshot file of collection {collection} is missing.", exit_code=2
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(_CHUNK):
                digest.update(block)
        if digest.hexdigest() != entry.get("sha256"):
            raise SnapshotError(
                f"The snapshot file of collection {collection} is damaged (checksum differs).",
                exit_code=2,
            )
        paths[collection] = path
    return paths


async def restore_snapshots(client: httpx.AsyncClient, in_dir: Path) -> list[str]:
    """Verify every file, then upload each one; returns the collections restored."""
    manifest = _read_manifest(in_dir)
    paths = _verify(in_dir, manifest)
    restored: list[str] = []
    for collection, path in paths.items():
        try:
            with path.open("rb") as handle:
                response = await client.post(
                    _path(collection, "snapshots", "upload"),
                    params={"priority": "snapshot", "wait": "true"},
                    files={"snapshot": (path.name, handle, "application/octet-stream")},
                )
        except httpx.HTTPError as exc:
            raise SnapshotError(f"Qdrant failed on collection {collection}: {exc}") from exc
        if response.status_code != 200:
            raise SnapshotError(
                f"Qdrant could not restore collection {collection} ({_fail_text(response)})"
            )
        restored.append(collection)
        _say(f"restored {collection}")
    return restored


def _client() -> httpx.AsyncClient:
    from callobuzz_rag.core.config import get_settings

    settings = get_settings()
    headers: dict[str, str] = {}
    key = settings.qdrant_api_key_or_none
    if key:
        headers["api-key"] = key
    return httpx.AsyncClient(base_url=settings.qdrant_url, headers=headers, timeout=600.0)


async def _run(command: str, folder: Path) -> None:
    async with _client() as client:
        if command == "create":
            await create_snapshots(client, folder)
        else:
            await restore_snapshots(client, folder)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m callobuzz_rag.vectordb.snapshots",
        description="Snapshot every Qdrant collection to a folder, or restore them from it.",
    )
    parser.add_argument("command", choices=["create", "restore"])
    parser.add_argument("folder", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    if args.command == "restore" and not args.folder.is_dir():
        sys.stderr.write(f"{args.folder} is not a folder.\n")
        return 2
    try:
        if args.command == "restore":
            _verify(args.folder, _read_manifest(args.folder))  # fail early, before Qdrant is asked
        asyncio.run(_run(args.command, args.folder))
    except SnapshotError as exc:
        sys.stderr.write(f"{exc}\n")
        return exc.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
