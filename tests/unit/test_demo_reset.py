from datetime import UTC, datetime
from typing import Any

import pytest

from callobuzz_rag.demo import reset
from tests.unit.accounts_fakes import FakeRedis

OP, SYS, OLD = "op-tenant", "cob-system", "visitor-made"


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[Any] = []
        self.tenants: list[dict[str, Any]] = [
            {"id": OP, "name": "Operator", "is_operator": True, "is_system": False},
            {"id": SYS, "name": "COB", "is_operator": False, "is_system": True},
            {"id": OLD, "name": "Nirmal Family Clinic", "is_operator": False, "is_system": False},
        ]
        self.fail_on: str | None = None
        self.settings_replies: list[reset.Reply] = []

    async def request(
        self, method: str, path: str, *, params: Any = None, json: Any = None
    ) -> reset.Reply:
        self.calls.append((method, path))
        self.bodies.append(json)
        if (method, path) == ("PATCH", "/api/v1/admin/settings") and self.settings_replies:
            return self.settings_replies.pop(0)
        if self.fail_on and self.fail_on in path:
            return reset.Reply(500, {"detail": "boom"})
        return reset.Reply(200, self._answer(method, path))

    def _answer(self, method: str, path: str) -> object:
        if (method, path) == ("GET", "/api/v1/admin/tenants"):
            return {"items": self.tenants}
        if (method, path) == ("GET", "/api/v1/admin/plans"):
            return {"items": []}
        if "/ingest/jobs/" in path:
            return {"status": "completed"}
        if path.endswith("/articles"):
            return {"id": "x", "job_id": f"job-{len(self.calls)}"}
        return {"id": f"new:{path}", "draft_revision": 3}


def _index(client: FakeClient, method: str, ending: str, nth: int = 0) -> int:
    hits = [i for i, (m, p) in enumerate(client.calls) if m == method and p.endswith(ending)]
    return hits[nth]


def test_removal_plan_keeps_operator_and_system() -> None:
    tenants = FakeClient().tenants
    assert [t["id"] for t in reset.workspaces_to_remove(tenants)] == [OLD]


async def test_full_reset_order_and_audit() -> None:
    client, redis = FakeClient(), FakeRedis()
    audit: list[dict[str, Any]] = []
    report = await reset.run_reset(
        client, redis, reset.ResetDeps.fake(audit=audit), trigger="manual"
    )
    assert report.result == "ok" and report.failed_step is None
    order = [
        _index(client, "PATCH", "/api/v1/admin/settings"),
        _index(client, "GET", "/api/v1/admin/plans"),
        _index(client, "POST", "/api/v1/admin/plans"),
        _index(client, "DELETE", f"/api/v1/admin/tenants/{OLD}"),
        _index(client, "POST", "/api/v1/admin/tenants"),
        _index(client, "POST", "/api/v1/corpora"),
        _index(client, "POST", "/assistant/draft/from-template"),
        _index(client, "PATCH", "/assistant/draft"),
        _index(client, "POST", "/assistant/publish"),
        _index(client, "POST", "/widget-keys"),
        _index(client, "POST", "/articles"),
        _index(client, "POST", "/articles"),
        _index(client, "POST", "/conversations"),
    ]
    assert order == sorted(order)
    assert ("DELETE", f"/api/v1/admin/tenants/{OP}") not in client.calls
    assert ("DELETE", f"/api/v1/admin/tenants/{SYS}") not in client.calls
    assert audit and audit[-1]["action"] == "demo.reset"
    assert audit[-1]["detail"]["result"] == "ok"
    assert audit[-1]["detail"]["workspaces_removed"] == 1
    assert audit[-1]["detail"]["documents_queued"] == 5
    assert audit[-1]["detail"]["sample_chats"] == 6
    assert "demo:reset:lock" not in redis.values  # released
    assert "demo:last_reset_at" in redis.values
    assert reset.STATUS_KEY in redis.values


async def test_the_jobs_are_waited_for_before_the_chats() -> None:
    client = FakeClient()
    await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    waits = [i for i, (m, p) in enumerate(client.calls) if m == "GET" and "/ingest/jobs/" in p]
    chats = [i for i, (_, p) in enumerate(client.calls) if p.endswith("/conversations")]
    assert len(waits) == 11  # five article jobs and six FAQ jobs
    assert max(waits) < min(chats)


async def test_the_documents_go_in_as_markdown_articles() -> None:
    client = FakeClient()
    await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert not any(p.endswith("/ingest/records") for _, p in client.calls)
    articles = [
        client.bodies[i]
        for i, (m, p) in enumerate(client.calls)
        if m == "POST" and p.endswith("/articles")
    ]
    docs = [a for a in articles if a["kind"] == "article"]
    assert len(docs) == 5 and len(articles) == 11
    assert all(a["body"].lstrip().startswith("#") for a in docs)


async def test_the_business_is_made_on_the_demo_plan_for_the_new_knowledge_base() -> None:
    client = FakeClient()
    await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    plan_id = "new:/api/v1/admin/plans"
    tenant_body = client.bodies[_index(client, "POST", "/api/v1/admin/tenants")]
    assert tenant_body == {"name": "Nirmal Family Clinic", "plan_id": plan_id}
    corpus_body = client.bodies[_index(client, "POST", "/api/v1/corpora")]
    assert corpus_body["tenant_id"] == "new:/api/v1/admin/tenants"
    publish = client.bodies[_index(client, "POST", "/assistant/publish")]
    assert publish["base_revision"] == 3


async def test_an_existing_demo_plan_is_updated_not_duplicated() -> None:
    class Existing(FakeClient):
        def _answer(self, method: str, path: str) -> object:
            if (method, path) == ("GET", "/api/v1/admin/plans"):
                return {"items": [{"id": "plan-1", "name": "Demo"}]}
            return super()._answer(method, path)

    client = Existing()
    await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert ("PATCH", "/api/v1/admin/plans/plan-1") in client.calls
    assert ("POST", "/api/v1/admin/plans") not in client.calls


async def test_branding_is_applied_in_one_settings_call() -> None:
    client = FakeClient()
    report = await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert report.result == "ok"
    bodies = [
        b
        for (m, p), b in zip(client.calls, client.bodies, strict=True)
        if (m, p) == ("PATCH", "/api/v1/admin/settings")
    ]
    assert len(bodies) == 1 and "branding" in bodies[0]


async def test_a_403_on_settings_is_a_failure() -> None:
    client = FakeClient()
    client.settings_replies = [reset.Reply(403, {"detail": "no", "code": "forbidden"})]
    report = await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert report.result == "failed" and report.failed_step == "engine_settings"


async def test_a_second_reset_waits_for_the_lock() -> None:
    redis = FakeRedis()
    await redis.set("demo:reset:lock", "other-run", ex=1800, nx=True)
    with pytest.raises(reset.ResetRunning):
        await reset.run_reset(FakeClient(), redis, reset.ResetDeps.fake(), trigger="schedule")
    assert redis.values["demo:reset:lock"] == "other-run"  # not ours to release


async def test_lock_unavailable_fails_closed() -> None:
    redis = FakeRedis()
    redis.down = True
    client = FakeClient()
    with pytest.raises(reset.ResetUnavailable):
        await reset.run_reset(client, redis, reset.ResetDeps.fake(), trigger="schedule")
    assert client.calls == []


async def test_a_failed_step_is_reported_and_the_lock_released() -> None:
    client, redis = FakeClient(), FakeRedis()
    client.fail_on = "/widget-keys"
    audit: list[dict[str, Any]] = []
    report = await reset.run_reset(
        client, redis, reset.ResetDeps.fake(audit=audit), trigger="start"
    )
    assert report.result == "failed" and report.failed_step == "widget_key"
    assert audit[-1]["detail"]["failed_step"] == "widget_key"
    assert audit[-1]["detail"]["result"] == "failed"
    assert "demo:reset:lock" not in redis.values
    assert "demo:last_reset_at" not in redis.values  # only a good reset counts
    assert not any(p.endswith("/articles") for _, p in client.calls)  # the run stopped


async def test_an_unexpected_error_in_a_step_is_a_failed_report() -> None:
    class Boom(FakeClient):
        async def request(self, method: str, path: str, **kw: Any) -> reset.Reply:
            if path.endswith("/api/v1/corpora") and method == "POST":
                raise RuntimeError("secret detail that must not be kept")
            return await super().request(method, path, **kw)

    redis = FakeRedis()
    report = await reset.run_reset(Boom(), redis, reset.ResetDeps.fake(), trigger="manual")
    assert report.result == "failed" and report.failed_step == "knowledge_base"
    assert "demo:reset:lock" not in redis.values


async def test_a_job_that_fails_to_learn_fails_the_wait_step() -> None:
    class Failing(FakeClient):
        def _answer(self, method: str, path: str) -> object:
            if "/ingest/jobs/" in path:
                return {"status": "failed"}
            return super()._answer(method, path)

    report = await reset.run_reset(Failing(), FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert report.failed_step == "wait"


async def test_a_job_that_never_finishes_times_out() -> None:
    class Slow(FakeClient):
        def _answer(self, method: str, path: str) -> object:
            if "/ingest/jobs/" in path:
                return {"status": "processing"}
            return super()._answer(method, path)

    deps = reset.ResetDeps.fake()
    deps.wait_timeout_s = 30.0
    report = await reset.run_reset(Slow(), FakeRedis(), deps, trigger="manual")
    assert report.failed_step == "wait"


async def test_sample_chats_are_skipped_without_budget() -> None:
    client = FakeClient()
    deps = reset.ResetDeps.fake(model_calls_left=50)
    report = await reset.run_reset(client, FakeRedis(), deps, trigger="manual")
    assert report.result == "ok" and report.sample_chats == 0
    assert not any("/conversations" in p for _, p in client.calls)


async def test_sample_chats_are_skipped_without_a_model_key() -> None:
    client = FakeClient()
    deps = reset.ResetDeps.fake(has_model_key=False)
    report = await reset.run_reset(client, FakeRedis(), deps, trigger="manual")
    assert report.result == "ok" and report.sample_chats == 0


async def test_a_failing_sample_chat_does_not_fail_the_reset() -> None:
    client = FakeClient()
    client.fail_on = "/messages"
    report = await reset.run_reset(client, FakeRedis(), reset.ResetDeps.fake(), trigger="manual")
    assert report.result == "ok" and report.sample_chats == 0


def test_next_reset_time() -> None:
    now = datetime(2026, 10, 7, 22, 0, tzinfo=UTC)
    assert reset.next_reset_at("21:30", now) == datetime(2026, 10, 8, 21, 30, tzinfo=UTC)
    early = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
    assert reset.next_reset_at("21:30", early) == datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    exact = datetime(2026, 10, 7, 21, 30, tzinfo=UTC)
    assert reset.next_reset_at("21:30", exact) == datetime(2026, 10, 8, 21, 30, tzinfo=UTC)


def test_staleness() -> None:
    now = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    assert reset.is_stale(None, now)
    assert reset.is_stale("garbage", now)
    assert reset.is_stale(datetime(2026, 10, 7, 10, 0, tzinfo=UTC).isoformat(), now)
    assert not reset.is_stale(datetime(2026, 10, 8, 3, 0, tzinfo=UTC).isoformat(), now)
