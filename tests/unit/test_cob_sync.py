"""COB's docs sync: idempotent, one enqueue per version, removes what the docs dropped."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from callobuzz_rag.cob.kb import load_docs_index, records_for
from callobuzz_rag.cob.sync import maybe_enqueue_sync, request_resync, run_sync
from callobuzz_rag.cob.system import engine_embedding, ensure_cob_system
from callobuzz_rag.cob.types import DocsIndex
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.cob_status import CobStatus
from callobuzz_rag.db.models.corpus import Corpus
from callobuzz_rag.db.models.ingest_job import IngestJob
from callobuzz_rag.pipeline.records import TextRecord
from tests.unit.accounts_fakes import FakeDB
from tests.unit.test_cob_manifest import SCREEN, make_root, write

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


class Store:
    """The documents of the system knowledge base, and the collaborators that change them."""

    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.ingest_calls = 0
        self.records_seen: list[str] = []
        self.remove_calls = 0
        self.removed: set[str] = set()
        self.recreated: list[tuple[str, int]] = []
        self.fail_with: str | None = None

    async def ingest(
        self, batch: list[TextRecord], corpus_id: uuid.UUID, collection: str, job_id: uuid.UUID
    ) -> dict[str, Any]:
        self.ingest_calls += 1
        if self.fail_with:
            return {"status": "failed", "failed_at_step": "embedding", "error": self.fail_with}
        for record in batch:
            self.ids.add(record.id)
            self.records_seen.append(record.id)
        return {"status": "completed"}

    async def remove(self, corpus_id: uuid.UUID, external_ids: list[str]) -> int:
        self.remove_calls += 1
        self.removed |= set(external_ids)
        self.ids -= set(external_ids)
        return len(external_ids)

    async def existing(self, corpus_id: uuid.UUID) -> set[str]:
        return set(self.ids)

    async def recreate(self, corpus_id: uuid.UUID, collection: str, dims: int) -> None:
        self.recreated.append((collection, dims))


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db = FakeDB()
        self.sf = self.db.factory()
        self.store = Store()
        self.settings = Settings(_env_file=None)  # type: ignore[call-arg]
        self.index: DocsIndex = load_docs_index(root)

    async def setup(self) -> "Env":
        await ensure_cob_system(self.db.session(), settings=self.settings, lock=False)  # type: ignore[arg-type]
        return self

    def reload(self) -> DocsIndex:
        self.index = load_docs_index(self.root)
        return self.index

    def status(self) -> CobStatus:
        return next(r for r in self.db.all(CobStatus))

    async def sync(self, *, force: bool = False, index: DocsIndex | None = None) -> Any:
        return await run_sync(
            self.sf,
            index=index or self.index,
            embedding=engine_embedding(self.settings),
            ingest=self.store.ingest,
            remove=self.store.remove,
            existing_ids=self.store.existing,
            recreate_collection=self.store.recreate,
            now=lambda: NOW,
            force=force,
        )


@pytest.fixture
async def env(tmp_path: Path) -> Env:
    root = make_root(tmp_path)
    write(
        root / "docs/cob/screens/other.md",
        SCREEN.replace("/widget", "/other").replace("Website widget", "Other"),
    )
    environment = Env(root)
    await environment.setup()
    # The migration seeds this row; the fake database starts empty.
    environment.db.add(CobStatus(id=1, sync_state="idle", document_count=0))
    return environment


async def test_first_sync_ingests_every_record_and_sets_status(env: Env) -> None:
    out = await env.sync()
    assert env.store.ids == {r.id for r in records_for(env.index)}
    status = env.status()
    assert status.docs_version == env.index.version and status.sync_state == "idle"
    assert status.document_count == out.documents == len(records_for(env.index))
    assert status.manifest_hash == env.index.manifest_hash and status.synced_at == NOW
    assert status.last_error is None
    jobs = env.db.all(IngestJob)
    assert jobs and all(j.mode == "records" for j in jobs)


async def test_second_sync_of_same_version_is_a_no_op(env: Env) -> None:
    await env.sync()
    calls = env.store.ingest_calls
    out = await env.sync()
    assert out.skipped and env.store.ingest_calls == calls and env.store.remove_calls == 0
    assert env.status().sync_state == "idle"


async def test_a_forced_sync_runs_again_at_the_same_version(env: Env) -> None:
    await env.sync()
    calls = env.store.ingest_calls
    out = await env.sync(force=True)
    assert not out.skipped and env.store.ingest_calls == 2 * calls


async def test_batches_hold_at_most_fifty_records(env: Env, tmp_path: Path) -> None:
    sections = "\n".join(f"## Section {i}\nText {i}.\n" for i in range(120))
    write(
        env.root / "docs/cob/screens/big.md",
        SCREEN.replace("/widget", "/big").replace("Embed the widget", "x") + sections,
    )
    env.reload()
    sizes: list[int] = []
    original = env.store.ingest

    async def counting(batch: list[TextRecord], *a: Any) -> dict[str, Any]:
        sizes.append(len(batch))
        return await original(batch, *a)

    env.store.ingest = counting  # type: ignore[method-assign]
    await env.sync()
    assert max(sizes) == 50 and sum(sizes) == len(records_for(env.index))


async def test_removed_source_deletes_its_documents(env: Env) -> None:
    await env.sync()
    (env.root / "docs/cob/screens/other.md").unlink()
    new_index = env.reload()
    await env.sync(index=new_index)
    assert {
        "system:cob/screens/other#",
        "system:cob/screens/other#embed-the-widget",
    } <= env.store.removed
    assert env.store.ids == {r.id for r in records_for(new_index)}
    assert env.status().docs_version == new_index.version


async def test_an_edited_page_is_ingested_again_under_the_same_ids(env: Env) -> None:
    await env.sync()
    env.store.records_seen.clear()
    write(env.root / "docs/cob/screens/widget.md", SCREEN.replace("Paste", "Paste it"))
    await env.sync(index=env.reload())
    assert "system:cob/screens/widget#embed-the-widget" in env.store.records_seen
    assert env.store.remove_calls == 0  # nothing was dropped, only edited


async def test_enqueue_happens_once_for_many_workers(env: Env) -> None:
    sent: list[str] = []
    results = await asyncio.gather(
        *[maybe_enqueue_sync(env.sf, env.index, delay=sent.append, now=NOW) for _ in range(5)]
    )
    assert sum(results) == 1 and sent == [env.index.version]
    status = env.status()
    assert status.sync_state == "queued" and status.requested_version == env.index.version


async def test_no_enqueue_when_the_knowledge_base_is_current(env: Env) -> None:
    await env.sync()
    sent: list[str] = []
    assert not await maybe_enqueue_sync(env.sf, env.index, delay=sent.append, now=NOW)
    assert sent == []


async def test_a_changed_version_enqueues_again(env: Env) -> None:
    await env.sync()
    write(env.root / "docs/cob/screens/widget.md", SCREEN.replace("Paste", "Paste it"))
    sent: list[str] = []
    assert await maybe_enqueue_sync(env.sf, env.reload(), delay=sent.append, now=NOW)
    assert sent == [env.index.version]


async def test_a_stuck_queued_sync_is_enqueued_again_after_ten_minutes(env: Env) -> None:
    sent: list[str] = []
    assert await maybe_enqueue_sync(env.sf, env.index, delay=sent.append, now=NOW)
    assert not await maybe_enqueue_sync(
        env.sf, env.index, delay=sent.append, now=NOW + timedelta(minutes=5)
    )
    assert await maybe_enqueue_sync(
        env.sf, env.index, delay=sent.append, now=NOW + timedelta(minutes=11)
    )
    assert len(sent) == 2


async def test_a_running_sync_blocks_a_new_enqueue_until_it_is_stale(env: Env) -> None:
    status = env.status()
    status.sync_state, status.updated_at = "running", NOW
    sent: list[str] = []
    assert not await maybe_enqueue_sync(
        env.sf, env.index, delay=sent.append, now=NOW + timedelta(minutes=20)
    )
    assert await maybe_enqueue_sync(
        env.sf, env.index, delay=sent.append, now=NOW + timedelta(hours=2)
    )


async def test_embedding_change_rebuilds(env: Env) -> None:
    await env.sync()
    corpus = next(c for c in env.db.all(Corpus) if c.is_system)
    corpus.applied_embedding = {"provider": "local", "model": "old-model", "dimensions": 384}
    out = await env.sync(force=True)
    assert out.rebuilt and env.store.recreated == [("cob_docs", env.settings.embedding_dimensions)]
    assert env.store.ids == {r.id for r in records_for(env.index)}
    assert corpus.applied_embedding == engine_embedding(env.settings)
    assert env.store.removed  # the old documents went before the re-ingest


async def test_failure_records_the_error_and_allows_a_retry(env: Env) -> None:
    env.store.fail_with = "x" * 900
    out = await env.sync()
    assert out.failed
    status = env.status()
    assert status.sync_state == "failed" and status.docs_version is None
    assert status.last_error is not None and len(status.last_error) == 500
    sent: list[str] = []
    assert await maybe_enqueue_sync(env.sf, env.index, delay=sent.append, now=NOW)
    env.store.fail_with = None
    assert not (await env.sync()).failed
    assert env.status().sync_state == "idle" and env.status().last_error is None


async def test_missing_system_corpus_is_a_recorded_failure(tmp_path: Path) -> None:
    environment = Env(make_root(tmp_path))
    environment.db.add(CobStatus(id=1, sync_state="idle", document_count=0))
    out = await environment.sync()
    assert out.failed and "does not exist" in (out.error or "")


async def test_resync_queues_once(env: Env) -> None:
    assert await request_resync(env.db.session(), env.index, now=NOW)  # type: ignore[arg-type]
    assert env.status().sync_state == "queued"
    assert not await request_resync(env.db.session(), env.index, now=NOW)  # type: ignore[arg-type]
