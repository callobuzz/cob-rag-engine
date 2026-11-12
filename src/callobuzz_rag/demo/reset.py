"""The nightly reset: put the demo back exactly as the seed describes it.

Runs in the API process (it needs the ASGI app for the in-process client), at the time of
`RAG_DEMO_RESET_AT_UTC` and at start when the last reset is missing or older than 25 hours (that
is the first seed). Everything but the two sign-ins goes through the engine's own routes with the
admin key, so every rule, cap and audit runs as it does for the dashboard; the two user rows and
the owner's membership are written directly (`demo/accounts.py`), as `accounts/bootstrap.py` does.

Order: lock, engine settings, plan, remove the visitors' workspaces, the operator's sign-in, the
business, its owner, the knowledge base, the assistant (its business card is part of the assistant
configuration), the widget key, the documents and FAQ, wait for them to be learned, sample chats.
A step that answers non-2xx stops the run: the report names it, the audit row says so, and the
lock is always released. Nothing here logs a body (a created workspace's answer holds an API key).
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from callobuzz_rag.core.exceptions import RAGKernelError
from callobuzz_rag.demo.seed_data import Seed

logger = logging.getLogger(__name__)

LOCK_KEY: Final = "demo:reset:lock"
STATUS_KEY: Final = "demo:status"
LAST_RESET_KEY: Final = "demo:last_reset_at"
LOCK_TTL_S: Final = 1800
STATUS_TTL_S: Final = 30 * 24 * 3600
STALE_AFTER: Final = timedelta(hours=25)
MIN_CALLS_FOR_CHATS: Final = 100
WAIT_TIMEOUT_S: Final = 600.0
POLL_INTERVAL_S: Final = 10.0
REQUEST_TIMEOUT_S: Final = 60.0
BASE_URL: Final = "http://demo.internal"

ACTOR: Final = ("system", None, "demo reset")

STEPS: Final = (
    "engine_settings",
    "plan",
    "remove",
    "operator",
    "business",
    "owner",
    "knowledge_base",
    "assistant",
    "widget_key",
    "documents",
    "faq",
    "wait",
    "sample_chats",
)


class ResetRunning(RAGKernelError):  # noqa: N818 - named in the epic
    """Another reset holds the lock."""


class ResetUnavailable(RAGKernelError):  # noqa: N818
    """Redis did not answer: a reset cannot be locked, so none runs (fail closed)."""


class StepFailedError(RAGKernelError):
    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"{step}: {detail}")
        self.step = step
        self.detail = detail


@dataclass(frozen=True)
class Reply:
    status: int
    body: Any = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def code(self) -> str | None:
        return self.body.get("code") if isinstance(self.body, dict) else None


class DemoClient(Protocol):
    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Reply: ...


class InternalDemoClient:
    """The engine's own routes through the ASGI app, as the admin (machine) credential.

    The same pattern as `cob.tools.http.InternalClient`, with a longer timeout: publishing and a
    sample answer can take longer than a dashboard call.
    """

    def __init__(self, app: Any, admin_key: str) -> None:
        self._app = app
        self._headers = {"X-API-Key": admin_key}

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None
    ) -> Reply:
        import httpx

        transport = httpx.ASGITransport(app=self._app, client=("127.0.0.1", 0))
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url=BASE_URL, timeout=REQUEST_TIMEOUT_S
            ) as http:
                response = await http.request(
                    method,
                    path,
                    params={k: v for k, v in (params or {}).items() if v is not None},
                    json=json,
                    headers=self._headers,
                )
        except httpx.HTTPError:
            logger.warning("demo reset: %s %s failed to answer", method, path)
            return Reply(599)
        try:
            body = response.json()
        except ValueError:
            body = None
        return Reply(response.status_code, body)


# ---------------------------------------------------------------------------- dependencies


@dataclass
class ResetDeps:
    seed: Seed
    password: str
    owner_email: str
    dashboard_url: str
    prepare_operator: Callable[[], Awaitable[None]]
    ensure_owner: Callable[[str], Awaitable[None]]
    model_calls_left: Callable[[], Awaitable[int]]
    has_model_key: Callable[[], bool]
    write_audit: Callable[[dict[str, Any]], Awaitable[None]]
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    poll_interval_s: float = POLL_INTERVAL_S
    wait_timeout_s: float = WAIT_TIMEOUT_S

    @classmethod
    def fake(
        cls,
        *,
        audit: list[dict[str, Any]] | None = None,
        model_calls_left: int = 1500,
        has_model_key: bool = True,
    ) -> "ResetDeps":
        """No database, no waiting: what the unit tests run the reset with."""
        from callobuzz_rag.demo import seed_data

        sink = audit if audit is not None else []

        async def nothing(*_: Any) -> None:
            return None

        async def left() -> int:
            return model_calls_left

        async def write(entry: dict[str, Any]) -> None:
            sink.append(entry)

        async def no_sleep(_: float) -> None:
            return None

        return cls(
            seed=seed_data.load(),
            password="Nirmal-Clinic-Demo-2026",
            owner_email="owner@nirmalclinic.example.com",
            dashboard_url="https://demo.sahayak.example.com",
            prepare_operator=nothing,
            ensure_owner=nothing,
            model_calls_left=left,
            has_model_key=lambda: has_model_key,
            write_audit=write,
            sleep=no_sleep,
        )

    @classmethod
    def from_app(cls, app: Any, settings: Any) -> "ResetDeps":
        """The real dependencies of the API process."""
        from callobuzz_rag.demo import accounts, budget, seed_data

        factory = app.state.session_factory

        async def prepare_operator() -> None:
            async with factory() as session:
                await accounts.prepare_operator(
                    session, settings.superadmin_email, settings.demo_password
                )
                await session.commit()

        seed = seed_data.load()

        async def ensure_owner(tenant_id: str) -> None:
            async with factory() as session:
                await accounts.ensure_owner(
                    session,
                    uuid.UUID(tenant_id),
                    settings.demo_owner_email,
                    seed.business.owner_name,
                    settings.demo_password,
                )
                await session.commit()

        async def model_calls_left() -> int:
            used = await budget.used_today_from_settings()
            return 0 if used is None else max(0, settings.demo_model_calls_per_day - used)

        async def write_audit(entry: dict[str, Any]) -> None:
            from callobuzz_rag.accounts.audit import record_audit

            async with factory() as session:
                await record_audit(
                    session,
                    None,
                    entry["action"],
                    details=entry["detail"],
                    actor=ACTOR,
                )
                await session.commit()

        return cls(
            seed=seed,
            password=settings.demo_password,
            owner_email=settings.demo_owner_email,
            dashboard_url=settings.dashboard_url,
            prepare_operator=prepare_operator,
            ensure_owner=ensure_owner,
            model_calls_left=model_calls_left,
            has_model_key=lambda: bool(settings.llm_api_key.get_secret_value()),
            write_audit=write_audit,
        )


@dataclass
class ResetReport:
    trigger: str = "manual"
    result: str = "ok"
    failed_step: str | None = None
    workspaces_removed: int = 0
    knowledge_bases_removed: int = 0
    documents_queued: int = 0
    sample_chats: int = 0
    started_at: str = ""
    finished_at: str = ""
    detail: str | None = field(default=None, repr=False)

    def audit_detail(self) -> dict[str, Any]:
        return {
            "trigger": self.trigger,
            "result": self.result,
            "workspaces_removed": self.workspaces_removed,
            "knowledge_bases_removed": self.knowledge_bases_removed,
            "documents_queued": self.documents_queued,
            "sample_chats": self.sample_chats,
            "failed_step": self.failed_step,
        }


# ---------------------------------------------------------------------------- pure helpers


def workspaces_to_remove(tenants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every workspace except the operator's own and COB's system workspace."""
    return [t for t in tenants if not t.get("is_operator") and not t.get("is_system")]


def next_reset_at(hhmm: str, now: datetime) -> datetime:
    """The next time `HH:MM` UTC after `now`."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    today = now.astimezone(UTC).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return today if today > now else today + timedelta(days=1)


def is_stale(last_reset_at: str | None, now: datetime) -> bool:
    """True when there is no reset on record, or the last one is older than 25 hours."""
    if not last_reset_at:
        return True
    try:
        last = datetime.fromisoformat(last_reset_at)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last > STALE_AFTER


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


# ---------------------------------------------------------------------------- the run


class _Run:
    def __init__(self, client: DemoClient, deps: ResetDeps, report: ResetReport) -> None:
        self.c = client
        self.d = deps
        self.report = report
        self.plan_id: str | None = None
        self.tenant_id: str | None = None
        self.corpus_id: str | None = None
        self.jobs: list[str] = []

    async def call(
        self,
        step: str,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        accept: tuple[int, ...] = (),
    ) -> Reply:
        reply = await self.c.request(method, path, params=params, json=json)
        if not reply.ok and reply.status not in accept:
            raise StepFailedError(step, f"{method} {path} answered {reply.status}")
        return reply

    @staticmethod
    def _id(step: str, reply: Reply) -> str:
        value = reply.body.get("id") if isinstance(reply.body, dict) else None
        if not value:
            raise StepFailedError(step, "the answer had no id")
        return str(value)

    @staticmethod
    def _items(reply: Reply) -> list[dict[str, Any]]:
        items = reply.body.get("items") if isinstance(reply.body, dict) else None
        return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []

    async def engine_settings(self) -> None:
        body = dict(self.d.seed.engine.settings)
        await self.call("engine_settings", "PATCH", "/api/v1/admin/settings", json=body)

    async def plan(self) -> None:
        seed = self.d.seed.plan
        listed = await self.call("plan", "GET", "/api/v1/admin/plans")
        existing = next((p for p in self._items(listed) if p.get("name") == seed.name), None)
        if existing is not None:
            self.plan_id = str(existing["id"])
            await self.call("plan", "PATCH", f"/api/v1/admin/plans/{self.plan_id}", json=seed.body)
        else:
            created = await self.call("plan", "POST", "/api/v1/admin/plans", json=seed.body)
            self.plan_id = self._id("plan", created)

    async def remove(self) -> None:
        listed = await self.call("remove", "GET", "/api/v1/admin/tenants", params={"limit": 500})
        tenants = self._items(listed)
        for tenant in workspaces_to_remove(tenants):
            await self.call(
                "remove",
                "DELETE",
                f"/api/v1/admin/tenants/{tenant['id']}",
                params={"confirm_name": tenant.get("name", "")},
            )
            self.report.workspaces_removed += 1
        for operator in (t for t in tenants if t.get("is_operator")):
            kbs = await self.call(
                "remove",
                "GET",
                "/api/v1/corpora",
                params={"tenant_id": operator["id"], "limit": 500},
            )
            for kb in self._items(kbs):
                await self.call(
                    "remove",
                    "DELETE",
                    f"/api/v1/corpora/{kb['id']}",
                    params={"confirm_name": kb.get("name", "")},
                )
                self.report.knowledge_bases_removed += 1

    async def operator(self) -> None:
        try:
            await self.d.prepare_operator()
        except RAGKernelError as exc:
            raise StepFailedError("operator", str(exc)) from exc

    async def business(self) -> None:
        body: dict[str, Any] = {"name": self.d.seed.business.name}
        if self.plan_id:
            body["plan_id"] = self.plan_id
        created = await self.call("business", "POST", "/api/v1/admin/tenants", json=body)
        self.tenant_id = self._id("business", created)

    async def owner(self) -> None:
        assert self.tenant_id is not None
        try:
            await self.d.ensure_owner(self.tenant_id)
        except RAGKernelError as exc:
            raise StepFailedError("owner", str(exc)) from exc

    async def knowledge_base(self) -> None:
        business = self.d.seed.business
        created = await self.call(
            "knowledge_base",
            "POST",
            "/api/v1/corpora",
            json={
                "name": business.knowledge_base_name,
                "description": business.knowledge_base_description,
                "tenant_id": self.tenant_id,
            },
        )
        self.corpus_id = self._id("knowledge_base", created)

    @staticmethod
    def _revision(reply: Reply) -> int:
        body = reply.body if isinstance(reply.body, dict) else {}
        return int(body.get("draft_revision", body.get("revision", 0)) or 0)

    async def assistant(self) -> None:
        base = f"/api/v1/corpora/{self.corpus_id}/assistant"
        seed = self.d.seed.assistant
        started = await self.call(
            "assistant",
            "POST",
            f"{base}/draft/from-template",
            json={"template": seed.template, "keep_identity": True},
        )
        patched = await self.call(
            "assistant",
            "PATCH",
            f"{base}/draft",
            json={"patch": seed.patch, "base_revision": self._revision(started)},
        )
        await self.call(
            "assistant",
            "POST",
            f"{base}/publish",
            json={"note": "Demo seed", "base_revision": self._revision(patched)},
        )

    async def widget_key(self) -> None:
        await self.call(
            "widget_key",
            "POST",
            f"/api/v1/corpora/{self.corpus_id}/widget-keys",
            json={
                "name": self.d.seed.business.widget_key_name,
                "allowed_origins": [_origin(self.d.dashboard_url)],
                "rate_limit_per_minute": 10,
                "daily_message_limit": 300,
            },
        )

    def _job(self, reply: Reply) -> None:
        body = reply.body if isinstance(reply.body, dict) else {}
        job = body.get("job_id")
        if job:
            self.jobs.append(str(job))

    async def documents(self) -> None:
        """Articles, not bare records: an article's Markdown headings become the heading path
        of its chunks, as for an uploaded file."""
        for d in self.d.seed.documents:
            reply = await self.call(
                "documents",
                "POST",
                f"/api/v1/corpora/{self.corpus_id}/articles",
                json={"kind": "article", "title": d.title, "body": d.text},
            )
            self._job(reply)
        self.report.documents_queued = len(self.d.seed.documents)

    async def faq(self) -> None:
        for item in self.d.seed.faq:
            reply = await self.call(
                "faq",
                "POST",
                f"/api/v1/corpora/{self.corpus_id}/articles",
                json={"kind": "faq", "title": item.title, "body": item.body},
            )
            self._job(reply)

    async def wait(self) -> None:
        deadline = self.d.wait_timeout_s
        waited = 0.0
        pending = list(self.jobs)
        while pending:
            still: list[str] = []
            for job in pending:
                reply = await self.call("wait", "GET", f"/api/v1/ingest/jobs/{job}")
                status = reply.body.get("status") if isinstance(reply.body, dict) else None
                if status == "failed":
                    raise StepFailedError("wait", "a document could not be learned")
                if status != "completed":
                    still.append(job)
            pending = still
            if not pending:
                break
            if waited >= deadline:
                raise StepFailedError("wait", "the documents were not learned in time")
            await self.d.sleep(self.d.poll_interval_s)
            waited += self.d.poll_interval_s

    async def sample_chats(self) -> None:
        """Best effort: a model outage must not leave the demo half reset (this is the last
        step, so everything else is already in place)."""
        if not self.d.has_model_key() or await self.d.model_calls_left() < MIN_CALLS_FOR_CHATS:
            logger.info("demo reset: sample chats skipped (no model key or too little budget)")
            return
        base = f"/api/v1/corpora/{self.corpus_id}/conversations"
        for question in self.d.seed.questions:
            conversation = await self.c.request(
                "POST", base, json={"title": question.text[:80], "created_by": "demo"}
            )
            if not conversation.ok or not isinstance(conversation.body, dict):
                logger.warning(
                    "demo reset: a sample chat could not start (%s)", conversation.status
                )
                continue
            sent = await self.c.request(
                "POST",
                f"{base}/{conversation.body.get('id')}/messages",
                json={"content": question.text},
            )
            if sent.ok:
                self.report.sample_chats += 1
            else:
                logger.warning("demo reset: a sample question got %s", sent.status)


async def _release(redis: Any, token: str) -> None:
    from callobuzz_rag.tiers.lanes import RELEASE_SCRIPT

    try:
        await redis.eval(RELEASE_SCRIPT, 1, LOCK_KEY, token)
    except Exception:
        logger.warning("demo reset: could not release the lock (it expires in 30 minutes)")


async def run_reset(
    client: DemoClient, redis: Any, deps: ResetDeps, *, trigger: str
) -> ResetReport:
    """One reset.

    Raises:
        ResetRunning: another reset holds the lock.
        ResetUnavailable: Redis did not answer (nothing ran).
    """
    token = uuid.uuid4().hex
    try:
        got = await redis.set(LOCK_KEY, token, ex=LOCK_TTL_S, nx=True)
    except Exception as exc:
        raise ResetUnavailable("the queue server did not answer") from exc
    if not got:
        raise ResetRunning("a reset is already running")

    report = ResetReport(trigger=trigger, started_at=deps.now().isoformat())
    run = _Run(client, deps, report)
    try:
        await _status(redis, {"running": True, "trigger": trigger, "started_at": report.started_at})
        for name in STEPS:
            try:
                await getattr(run, name)()
            except StepFailedError as exc:
                report.result, report.failed_step, report.detail = "failed", exc.step, exc.detail
                logger.error("demo reset failed at %s: %s", exc.step, exc.detail)
                break
            except Exception as exc:
                report.result, report.failed_step = "failed", name
                report.detail = type(exc).__name__
                logger.exception("demo reset failed at %s", name)
                break
    finally:
        report.finished_at = deps.now().isoformat()
        if report.result == "ok":
            try:
                await redis.set(LAST_RESET_KEY, report.finished_at)
            except Exception:
                logger.warning("demo reset: could not record the reset time")
        await _status(redis, {"running": False, **asdict(report)})
        await _release(redis, token)
        try:
            await deps.write_audit({"action": "demo.reset", "detail": report.audit_detail()})
        except Exception:
            logger.warning("demo reset: could not write the audit entry", exc_info=True)
    return report


async def _status(redis: Any, value: dict[str, Any]) -> None:
    try:
        await redis.set(STATUS_KEY, json.dumps(value), ex=STATUS_TTL_S)
    except Exception:
        logger.warning("demo reset: could not record the status")
