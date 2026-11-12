"""GET /api/v1/admin/cob/status and POST /api/v1/admin/cob/resync: admin sessions only."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest

from callobuzz_rag.api.deps import get_session
from callobuzz_rag.cob.kb import load_docs_index
from callobuzz_rag.cob.system import ensure_cob_system
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.cob_status import CobStatus
from tests.unit.accounts_fakes import FakeDB
from tests.unit.scope_harness import call, scoped_app

STATUS = "/api/v1/admin/cob/status"
RESYNC = "/api/v1/admin/cob/resync"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)


class Setup:
    def __init__(self, app: Any) -> None:
        self.db = FakeDB()
        self.status = self.db.add(CobStatus(id=1, sync_state="idle", document_count=0))
        self.delays: list[dict[str, Any]] = []

        async def override() -> Any:
            yield self.db.session()

        app.dependency_overrides[get_session] = override

    async def ready(self) -> None:
        await ensure_cob_system(
            self.db.session(),  # type: ignore[arg-type]
            settings=Settings(_env_file=None),  # type: ignore[call-arg]
            lock=False,
        )

    def delay(self, **kwargs: Any) -> None:
        self.delays.append(kwargs)


async def fetch(key: str | None, method: str, path: str, **kwargs: Any) -> tuple[Any, Setup]:
    with scoped_app() as (app, _world):
        setup = Setup(app)
        await setup.ready()
        with patch("callobuzz_rag.worker.cob_tasks.cob_sync_task.delay", side_effect=setup.delay):
            res = await call(app, key, method, path, **kwargs)
    return res, setup


async def test_status_for_an_admin_session() -> None:
    index = load_docs_index()
    with scoped_app() as (app, _world):
        setup = Setup(app)
        await setup.ready()
        setup.status.docs_version = index.version
        setup.status.document_count = 321
        setup.status.synced_at = NOW
        res = await call(app, "sa", "GET", STATUS)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["docs_version"] == index.version and body["current_version"] == index.version
    assert body["up_to_date"] is True and body["sync_state"] == "idle"
    assert body["document_count"] == 321 and body["last_error"] is None and body["eval"] is None
    assert body["corpus_id"] is not None


async def test_status_says_update_pending_when_the_versions_differ() -> None:
    with scoped_app() as (app, _world):
        setup = Setup(app)
        await setup.ready()
        setup.status.docs_version = "0.0.1+old"
        setup.status.sync_state = "failed"
        setup.status.last_error = "The embedding service is down."
        res = await call(app, "sa", "GET", STATUS)
    body = res.json()
    assert body["up_to_date"] is False and body["sync_state"] == "failed"
    assert body["last_error"] == "The embedding service is down."


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_an_api_key_is_refused_even_the_admin_key(method: str) -> None:
    res, _ = await fetch("admin", method, STATUS if method == "GET" else RESYNC, json={})
    assert res.status_code == 403 and res.json()["code"] == "session_required"


@pytest.mark.parametrize("key", ["owner_a", "member_a", "tk_a", "ck_a"])
async def test_everyone_but_the_operator_is_refused(key: str) -> None:
    res, setup = await fetch(key, "POST", RESYNC, json={})
    assert res.status_code == 403
    assert setup.delays == []


async def test_unauthenticated_is_401() -> None:
    res, _ = await fetch(None, "GET", STATUS)
    assert res.status_code == 401


async def test_resync_queues_once_forces_and_audits() -> None:
    with scoped_app() as (app, _world):
        setup = Setup(app)
        await setup.ready()
        setup.status.docs_version = "0.1.0+abc"
        with patch("callobuzz_rag.worker.cob_tasks.cob_sync_task.delay", side_effect=setup.delay):
            first = await call(app, "sa", "POST", RESYNC, json={"eval": False})
            second = await call(app, "sa", "POST", RESYNC, json={})
    assert first.status_code == 202 and first.json() == {"queued": True}
    assert second.status_code == 202 and second.json() == {"queued": False}
    assert setup.delays == [{"eval": False, "force": True}]
    assert setup.status.sync_state == "queued"
    audits = [a for a in setup.db.all(AuditLog) if a.action == "cob.resync_requested"]
    assert len(audits) == 1
    assert audits[0].details == {"eval": False, "from_version": "0.1.0+abc"}


async def test_a_queue_that_refuses_marks_the_sync_failed() -> None:
    with scoped_app() as (app, _world):
        setup = Setup(app)
        await setup.ready()
        with patch(
            "callobuzz_rag.worker.cob_tasks.cob_sync_task.delay",
            side_effect=ConnectionError("redis down"),
        ):
            res = await call(app, "sa", "POST", RESYNC, json={})
    assert res.status_code == 503
    assert setup.status.sync_state == "failed" and setup.status.last_error


async def test_resync_body_is_strict() -> None:
    res, _ = await fetch("sa", "POST", RESYNC, json={"eval": True, "extra": 1})
    assert res.status_code == 422
