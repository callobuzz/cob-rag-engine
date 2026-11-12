import uuid
from types import SimpleNamespace
from typing import Any

from callobuzz_rag.core.config import Settings
from callobuzz_rag.worker import website_tasks
from callobuzz_rag.worker.celery_app import celery_app


def test_tasks_are_registered_on_the_ingestion_queue() -> None:
    for name in ("callobuzz_rag.website.learn_site", "callobuzz_rag.website.learn_urls"):
        task = celery_app.tasks[name]
        assert task.queue == "ingestion"
        assert task.acks_late is True


def test_deps_use_a_fetcher_scoped_to_the_site() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    deps = website_tasks.build_deps(settings, lambda: None, object(), _noop_ingest)
    source = SimpleNamespace(
        origin="https://www.shreedental.in", scope="shreedental.in", tenant_id=uuid.uuid4()
    )
    fetcher = deps.fetcher(source)
    assert fetcher._scope == "shreedental.in"
    assert fetcher._origin == "https://www.shreedental.in"


async def _noop_ingest(batch: list[Any], corpus_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    return {"status": "completed"}


def test_a_missing_site_is_a_quiet_stop(monkeypatch) -> None:
    import asyncio

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, model: type, key: Any) -> None:
            return None

    class Engine:
        async def dispose(self) -> None:
            return None

    class Vectors:
        def __init__(self, settings: Any) -> None:
            pass

        async def close(self) -> None:
            return None

    monkeypatch.setattr("callobuzz_rag.db.engine.create_engine", lambda s: Engine())
    monkeypatch.setattr(
        "callobuzz_rag.db.engine.create_session_factory", lambda e: lambda: Session()
    )
    monkeypatch.setattr("callobuzz_rag.vectordb.qdrant.QdrantVectorProvider", Vectors)
    result = asyncio.run(
        website_tasks._learn(str(uuid.uuid4()), kind="first", trigger="manual", urls=None)
    )
    assert result == {"status": "stopped", "reason": "gone"}


# BIZ2 freshness
def test_freshness_tasks_are_registered_on_their_queues() -> None:
    check = celery_app.tasks["callobuzz_rag.website.check_pages"]
    tick = celery_app.tasks["callobuzz_rag.website.schedule_checks"]
    assert (check.queue, check.acks_late) == ("ingestion", True)
    assert (tick.queue, tick.acks_late) == ("periodic", True)


def test_the_beat_runs_the_scheduler_every_check_interval() -> None:
    from callobuzz_rag.worker.beat_schedule import build_beat_schedule

    settings = Settings(_env_file=None, website_check_interval_seconds=120)  # type: ignore[call-arg]
    entry = build_beat_schedule(settings)["website-schedule-checks"]
    assert entry["task"] == "callobuzz_rag.website.schedule_checks"
    assert entry["schedule"] == 120.0 and entry["options"]["queue"] == "periodic"


def test_a_check_of_a_missing_site_is_a_quiet_stop(monkeypatch) -> None:
    import asyncio

    class Session:
        async def __aenter__(self) -> "Session":
            return self

        async def __aexit__(self, *exc: Any) -> None:
            return None

        async def get(self, model: type, key: Any) -> None:
            return None

    class Engine:
        async def dispose(self) -> None:
            return None

    class Vectors:
        def __init__(self, settings: Any) -> None:
            pass

        async def close(self) -> None:
            return None

    monkeypatch.setattr("callobuzz_rag.db.engine.create_engine", lambda s: Engine())
    monkeypatch.setattr(
        "callobuzz_rag.db.engine.create_session_factory", lambda e: lambda: Session()
    )
    monkeypatch.setattr("callobuzz_rag.vectordb.qdrant.QdrantVectorProvider", Vectors)
    result = asyncio.run(website_tasks._check(str(uuid.uuid4()), [str(uuid.uuid4())], "schedule"))
    assert result == {"status": "stopped", "reason": "gone"}


# /BIZ2 freshness
