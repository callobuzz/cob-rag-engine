"""COB5 on real PostgreSQL: the proposals table, and a card that runs once under real concurrency.

The unit tests prove the rules with an in-memory database; what only PostgreSQL can prove is the
single `UPDATE ... WHERE status = 'pending' AND expires_at > now ... RETURNING` claim (rows are
locked, so concurrent confirms wait and then find nothing to claim), the `status` check
constraint and the cascades. Requires Docker.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob import proposals
from callobuzz_rag.cob.tools.actions.common import ProposalDraft
from callobuzz_rag.cob.types import CobCaller, CobRole
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
from callobuzz_rag.db.models.message import Message
from tests.unit.cob_tool_helpers import FakeInternalClient

pytestmark = pytest.mark.integration

KB = uuid.uuid4()


async def _seed(engine: Any) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A workspace, a person and a COB conversation; returns their ids."""
    tenant, user, corpus, conv = (uuid.uuid4() for _ in range(4))
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO tenants (id, name, plan_id) "
                "SELECT :id, :n, id FROM plans WHERE is_default LIMIT 1"
            ),
            {"id": tenant, "n": f"ws-{tenant}"},
        )
        await c.execute(
            text("INSERT INTO users (id, email, name) VALUES (:id, :e, 'Asha')"),
            {"id": user, "e": f"asha-{user}@example.com"},
        )
        await c.execute(
            text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'sys', :t)"),
            {"id": corpus, "t": tenant},
        )
        await c.execute(
            text(
                "INSERT INTO conversations (id, corpus_id, kind, channel, user_id, scope_tenant_id) "
                "VALUES (:id, :c, 'cob', 'cob', :u, :t)"
            ),
            {"id": conv, "c": corpus, "u": user, "t": tenant},
        )
    return tenant, user, conv


def _caller(user: uuid.UUID, tenant: uuid.UUID, role: CobRole = CobRole.OWNER) -> CobCaller:
    return CobCaller(role, user, tenant, uuid.uuid4(), "Bearer test")


def _request(user: uuid.UUID) -> Any:
    from tests.unit.accounts_fakes import request_stub

    stub = request_stub(Settings())
    stub.state.user = SimpleNamespace(id=user, email="asha@example.com")
    return stub


def _client() -> FakeInternalClient:
    client = FakeInternalClient()
    client.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": "wk"}, 201)
    return client


async def _propose(factory: Any, caller: CobCaller, conv: uuid.UUID) -> uuid.UUID:
    draft = ProposalDraft(
        title="Create a widget key",
        summary="s",
        args=[],
        risk="low",
        exec_args={
            "corpus_id": str(KB),
            "kb_name": "Shree Labs",
            "name": "Main site",
            "allowed_origins": ["https://shreelabs.in"],
        },
    )
    async with factory() as session:
        row = await proposals.create_proposal(session, caller, conv, "create_widget_key", draft)
        await session.commit()
        return row.id


async def test_a_card_runs_once_however_many_confirms(db_engine: Any) -> None:
    tenant, user, conv = await _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pid = await _propose(factory, _caller(user, tenant), conv)
    client = _client()

    async def confirm() -> Any:
        async with factory() as session:
            return await proposals.confirm(
                session, _request(user), _caller(user, tenant), pid, None, client=client
            )

    results = await asyncio.gather(*[confirm() for _ in range(6)], return_exceptions=True)
    done = [r for r in results if not isinstance(r, Exception)]
    used = [r for r in results if isinstance(r, APIError) and r.code == "cob_proposal_used"]
    assert len(done) == 1 and len(used) == 5, results
    assert [(m, p) for m, p, _ in client.calls if m != "GET"] == [
        ("POST", f"/api/v1/corpora/{KB}/widget-keys")
    ]
    async with factory() as session:
        row = await session.get(CobActionProposal, pid)
        assert row is not None and row.status == "done" and row.used_at is not None
        audits = (
            (await session.execute(select(AuditLog).where(AuditLog.action == "cob.action")))
            .scalars()
            .all()
        )
        mine = [a for a in audits if a.details.get("proposal_id") == str(pid)]
        assert len(mine) == 1 and mine[0].details["ok"] is True
        notes = (
            (await session.execute(select(Message).where(Message.conversation_id == conv)))
            .scalars()
            .all()
        )
        assert len(notes) == 1 and notes[0].content.startswith("Done:")


async def test_an_old_card_is_refused_and_marked_expired(db_engine: Any) -> None:
    tenant, user, conv = await _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pid = await _propose(factory, _caller(user, tenant), conv)
    async with db_engine.begin() as c:
        await c.execute(
            text("UPDATE cob_action_proposals SET expires_at = :t WHERE id = :id"),
            {"t": datetime.now(UTC) - timedelta(seconds=1), "id": pid},
        )
    client = _client()
    async with factory() as session:
        with pytest.raises(APIError) as err:
            await proposals.confirm(
                session, _request(user), _caller(user, tenant), pid, None, client=client
            )
    assert err.value.status_code == 410 and err.value.code == "cob_proposal_expired"
    assert client.calls == []
    async with factory() as session:
        row = await session.get(CobActionProposal, pid)
        assert row is not None and row.status == "expired"


async def test_someone_elses_card_and_a_member_forged_row_never_call_a_route(
    db_engine: Any,
) -> None:
    tenant, user, conv = await _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pid = await _propose(factory, _caller(user, tenant), conv)
    client = _client()
    stranger = uuid.uuid4()
    async with factory() as session:
        with pytest.raises(APIError) as err:
            await proposals.confirm(
                session, _request(stranger), _caller(stranger, tenant), pid, None, client=client
            )
    assert err.value.code == "cob_proposal_not_found"
    # The same person, but now only a member: the owner tool is no longer offered.
    async with factory() as session:
        with pytest.raises(APIError) as scope:
            await proposals.confirm(
                session,
                _request(user),
                _caller(user, tenant, CobRole.MEMBER),
                pid,
                None,
                client=client,
            )
    assert scope.value.code == "cob_proposal_scope_changed"
    assert client.calls == []


async def test_the_table_rejects_a_bad_status_and_cascades(db_engine: Any) -> None:
    tenant, user, conv = await _seed(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pid = await _propose(factory, _caller(user, tenant), conv)
    with pytest.raises(IntegrityError):
        async with db_engine.begin() as c:
            await c.execute(
                text("UPDATE cob_action_proposals SET status = 'bogus' WHERE id = :id"), {"id": pid}
            )
    async with db_engine.begin() as c:
        await c.execute(text("DELETE FROM conversations WHERE id = :id"), {"id": conv})
        left = (
            await c.execute(
                text("SELECT count(*) FROM cob_action_proposals WHERE id = :id"), {"id": pid}
            )
        ).scalar_one()
    assert left == 0  # the card goes with its conversation


# ------------------------------------------------------------------------------------------
# Every tool through the real app (real routes, real middleware, real audit) on real PostgreSQL.
# ------------------------------------------------------------------------------------------

ADMIN_KEY = "admin-key-cob-actions"
PASSWORD = "a long pass phrase"
IP = "203.0.113.7"


class World:
    """A running app, an owner, a member, an operator and a COB conversation to hang cards on."""

    def __init__(self) -> None:
        self.mail: list[tuple[str, str, dict[str, Any]]] = []

    async def start(self, db_url: str, redis_url: str) -> None:
        import os

        from httpx import ASGITransport, AsyncClient
        from sqlalchemy.ext.asyncio import create_async_engine

        from callobuzz_rag.accounts.ratelimit import AccountRateLimiter
        from callobuzz_rag.api.app import create_app
        from callobuzz_rag.cob.tools.http import InternalClient

        self.engine = create_async_engine(db_url)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            session_secret="e" * 64,  # type: ignore[arg-type]
            superadmin_email="ops-cob@example.com",
            email_provider="log",
            admin_api_key=ADMIN_KEY,  # type: ignore[arg-type]
        )
        with patch.dict(os.environ, {"RAG_ENABLE_CONTENT_PORTAL": "false"}):
            self.app = create_app()
        self.app.state.settings = self.settings
        self.app.state.session_factory = self.factory
        self.app.state.account_rate_limiter = AccountRateLimiter(redis_url)
        self.app.state.vector_store = AsyncMock()
        self.http = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://t")
        self.admin = {"X-API-Key": ADMIN_KEY}

        made = await self.http.post(
            "/api/v1/admin/tenants", json={"name": "Shree Labs"}, headers=self.admin
        )
        self.tenant = uuid.UUID(made.json()["id"])
        self.owner_token, self.owner_id = await self._join("asha@example.com", "owner", "Asha")
        self.owner_headers = {"Authorization": f"Bearer {self.owner_token}"}
        self.member_token, self.member_id = await self._join("ravi@example.com", "member", "Ravi")
        self.member_headers = {"Authorization": f"Bearer {self.member_token}"}

        self.owner = CobCaller(
            CobRole.OWNER, self.owner_id, self.tenant, None, f"Bearer {self.owner_token}"
        )
        self.operator = CobCaller(CobRole.ADMIN, self.owner_id, None, None, None)
        self.clients = {
            "owner": InternalClient(self.app, self.owner.authorization, client_ip=IP),
            "operator": InternalClient(
                self.app, None, client_ip=IP, headers={"X-API-Key": ADMIN_KEY}
            ),
            "member": InternalClient(self.app, f"Bearer {self.member_token}", client_ip=IP),
        }
        # The cards hang on a COB conversation of a separate workspace, so it shows up in no list.
        other, self.conversation, corpus = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with self.engine.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO tenants (id, name, plan_id) "
                    "SELECT :id, :n, id FROM plans WHERE is_default LIMIT 1"
                ),
                {"id": other, "n": f"conv-{other}"},
            )
            await c.execute(
                text("INSERT INTO corpora (id, name, tenant_id) VALUES (:id, 'sys', :t)"),
                {"id": corpus, "t": other},
            )
            await c.execute(
                text(
                    "INSERT INTO conversations (id, corpus_id, kind, channel, user_id, "
                    "scope_tenant_id) VALUES (:id, :c, 'cob', 'cob', :u, :t)"
                ),
                {"id": self.conversation, "c": corpus, "u": self.owner_id, "t": self.tenant},
            )

    async def _join(self, email: str, role: str, name: str) -> tuple[str, uuid.UUID]:
        if role == "owner":
            made = await self.http.post(
                f"/api/v1/admin/tenants/{self.tenant}/invitations",
                json={"email": email, "role": role},
                headers=self.admin,
            )
            token = made.json()["accept_url"].split("#token=")[1]
        else:
            made = await self.http.post(
                "/api/v1/tenants/me/members",
                json={"email": email, "role": role},
                headers=self.owner_headers,
            )
            assert made.status_code == 201, made.text
            invites = [c for t, to, c in self.mail if t == "invitation" and to == email]
            token = invites[-1]["accept_url"].split("#token=")[1]
        joined = await self.http.post(
            "/api/v1/invitations/accept",
            json={"token": token, "name": name, "password": PASSWORD},
        )
        assert joined.status_code == 200, joined.text
        return joined.json()["access_token"], uuid.UUID(joined.json()["user"]["id"])

    async def api(self, method: str, path: str, who: str = "owner", **kw: Any) -> Any:
        headers = {
            "owner": self.owner_headers,
            "member": self.member_headers,
            "operator": self.admin,
        }
        return await self.http.request(method, path, headers=headers[who], **kw)

    def caller(self, who: str) -> CobCaller:
        return {
            "owner": self.owner,
            "operator": self.operator,
            "member": CobCaller(
                CobRole.MEMBER, self.member_id, self.tenant, None, f"Bearer {self.member_token}"
            ),
        }[who]

    def context(self, tool: Any, who: str = "owner", question: str = "") -> Any:
        from datetime import UTC, datetime

        from callobuzz_rag.cob.tools.base import ToolContext
        from callobuzz_rag.cob.tools.http import tool_client

        return ToolContext(
            caller=self.caller(who),
            client=tool_client(tool, self.clients[who]),
            language="en",
            page=None,
            now=datetime.now(UTC),
            question=question,
        )

    async def propose(
        self, tool_name: str, args: dict[str, Any], who: str = "owner", question: str = ""
    ) -> Any:
        from callobuzz_rag.cob.tools import REGISTRY

        tool: Any = REGISTRY[tool_name]
        return await tool.propose(
            self.context(tool, who, question), tool.args_model.model_validate(args)
        )

    async def card(
        self, tool_name: str, args: dict[str, Any], who: str = "owner", question: str = ""
    ) -> tuple[Any, Any]:
        """Propose through the real read routes, store the card, confirm it through the real
        write route. Returns (draft, result)."""
        draft = await self.propose(tool_name, args, who, question)
        assert isinstance(draft, ProposalDraft), (tool_name, draft)
        caller = self.caller(who)
        async with self.factory() as session:
            row = await proposals.create_proposal(
                session, caller, self.conversation, tool_name, draft
            )
            await session.commit()
            pid = row.id
        async with self.factory() as session:
            result = await proposals.confirm(
                session,
                _request(self.owner_id),
                caller,
                pid,
                draft.confirm_name,
                client=self.clients[who],
            )
        return draft, result

    async def scalar(self, sql: str, **params: Any) -> Any:
        async with self.engine.begin() as c:
            return (await c.execute(text(sql), params)).scalar_one_or_none()

    async def stop(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


def _ok(result: Any) -> None:
    assert result.status == "done", (
        result.status,
        result.http_status,
        result.code,
        result.message,
    )


async def _scenario(world: World) -> dict[str, Any]:
    from callobuzz_rag.cob.tools.base import ToolResult

    out: dict[str, Any] = {}
    api = world.api

    # --- knowledge bases
    _, made = await world.card("create_knowledge_base", {"name": "Clinic"})
    _ok(made)
    kbs = (await api("GET", "/api/v1/corpora")).json()["items"]
    clinic = next(k["id"] for k in kbs if k["name"] == "Clinic")
    scratch = (await api("POST", "/api/v1/corpora", json={"name": "Scratch"})).json()["id"]

    # --- widget keys
    _, made = await world.card(
        "create_widget_key", {"kb": "Clinic", "site": "shreelabs.in", "name": "Main site"}
    )
    _ok(made)
    _, made = await world.card(
        "add_allowed_site", {"kb": "Clinic", "site": "shop.in", "key": "Main site"}
    )
    _ok(made)
    keys = (await api("GET", f"/api/v1/corpora/{clinic}/widget-keys")).json()
    out["origins"] = sorted(keys[0]["allowed_origins"])

    # --- named keys: rotate shows the secret once and stores it nowhere
    created = await api("POST", "/api/v1/tenants/me/keys", json={"name": "Zapier"})
    assert created.status_code == 201, created.text
    old_secret = created.json()["api_key"]
    await api("POST", f"/api/v1/corpora/{clinic}/keys", json={"name": "Reports"})
    await api("POST", "/api/v1/tenants/me/keys", json={"name": "Retired"})
    _, rotated = await world.card("rotate_key", {"key": "Zapier"})
    _ok(rotated)
    assert rotated.secret and rotated.secret != old_secret
    out["secret"] = rotated.secret
    for name in ("Reports", "Retired", "Main site"):
        draft, gone = await world.card("delete_key", {"key": name})
        assert draft.confirm_name == name
        _ok(gone)
    left = await api("GET", f"/api/v1/corpora/{clinic}/widget-keys")
    out["widget_keys_left"] = len(left.json())

    # --- assistant: a draft, publish it, switch it off and on, then the reply language
    patched = await api(
        "PATCH",
        f"/api/v1/corpora/{clinic}/assistant/draft",
        json={"patch": {"language": {"primary": "en", "reply_policy": "fixed"}}},
    )
    assert patched.status_code == 200, patched.text
    _, made = await world.card("publish_assistant", {"kb": "Clinic"})
    _ok(made)
    _, made = await world.card("toggle_assistant", {"kb": "Clinic", "state": "off"})
    _ok(made)
    _, made = await world.card("toggle_assistant", {"kb": "Clinic", "state": "on"})
    _ok(made)
    _, made = await world.card("switch_reply_language", {"kb": "Clinic", "language": "hi"})
    _ok(made)
    state = (await api("GET", f"/api/v1/corpora/{clinic}/assistant")).json()
    out["language"] = state["published"]["config"]["language"]["primary"]
    guided = await world.propose("start_guided_setup", {"kb": "Clinic"})
    assert isinstance(guided, ToolResult) and guided.link is not None  # a link, nothing to confirm
    out["guided"] = str(guided.link.route)

    # --- uploads: a failed one is retried once, a document is deleted
    failed, doc = uuid.uuid4(), uuid.uuid4()
    async with world.engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO ingest_jobs (id, status, corpus_id, mode, storage_key, "
                "review_metadata, total_documents) VALUES (:id, 'failed', :c, 'new', 'raw/x/menu', "
                "CAST(:m AS jsonb), 1)"
            ),
            {
                "id": failed,
                "c": uuid.UUID(clinic),
                "m": '{"file_name": "menu.pdf", "mime_type": "application/pdf"}',
            },
        )
        await c.execute(
            text(
                "INSERT INTO documents (id, corpus_id, file_name, mime_type) "
                "VALUES (:id, :c, 'price-list.pdf', 'application/pdf')"
            ),
            {"id": doc, "c": uuid.UUID(clinic)},
        )
    storage = MagicMock()
    storage.exists = AsyncMock(return_value=True)
    with (
        patch("callobuzz_rag.api.routes.ingest.object_storage", return_value=storage),
        patch("callobuzz_rag.worker.tasks.ingest_document_task") as task,
    ):
        _, made = await world.card("retry_ingestion", {"kb": "Clinic", "file": "menu"})
        _ok(made)
        out["dispatched"] = task.delay.call_count
        # The old job is marked as retried, so it is offered no more (by COB or the Jobs tab).
        listed = (await api("GET", "/api/v1/ingest/jobs", params={"status": "failed"})).json()
        out["retried_flag"] = [j["retried"] for j in listed["items"] if j["id"] == str(failed)]
        again = await world.propose("retry_ingestion", {"kb": "Clinic"})
        out["second_retry_offer"] = type(again).__name__
    draft, gone = await world.card("delete_document", {"kb": "Clinic", "file": "price-list"})
    assert draft.confirm_name == "price-list.pdf"
    _ok(gone)
    out["document_left"] = await world.scalar("SELECT count(*) FROM documents WHERE id = :d", d=doc)
    with patch("callobuzz_rag.storage.cleanup.schedule_prefix_cleanup", AsyncMock()):
        draft, gone = await world.card("delete_knowledge_base", {"kb": "Scratch"})
        assert draft.confirm_name == "Scratch"
        _ok(gone)
    out["scratch_left"] = await world.scalar(
        "SELECT count(*) FROM corpora WHERE id = :c", c=uuid.UUID(scratch)
    )

    # --- team
    _, made = await world.card("invite_member", {"email": "kiran@example.com", "role": "member"})
    _ok(made)
    out["invite_mailed"] = any(to == "kiran@example.com" for _, to, _ in world.mail)

    # --- a member's forged row: the route itself refuses, with the dashboard's own status
    out["forged"] = await _forged(world)

    _, made = await world.card(
        "change_member_role", {"member": "ravi@example.com", "role": "owner"}
    )
    _ok(made)
    out["ravi_role"] = await world.scalar(
        "SELECT role FROM tenant_members WHERE user_id = :u", u=world.member_id
    )

    # --- the operator's customers
    pilot = {"id": str(await world.scalar("SELECT id FROM plans WHERE name = 'Pilot'"))}  # seeded
    ids = {}
    for name in ("Anand Dental Care", "Bright Cafe", "Old Gym", "Last Shop"):
        made_t = await api("POST", "/api/v1/admin/tenants", "operator", json={"name": name})
        ids[name] = uuid.UUID(made_t.json()["id"])
    async with world.engine.begin() as c:
        await c.execute(
            text("UPDATE tenants SET status = 'pending', activated_at = NULL WHERE id = :t"),
            {"t": ids["Anand Dental Care"]},
        )
    _, made = await world.card(
        "approve_customer", {"customer": "Anand Dental Care", "plan": "Pilot"}, "operator"
    )
    _ok(made)
    _, made = await world.card(
        "change_customer_plan", {"customer": "Bright Cafe", "plan": "Pilot"}, "operator"
    )
    _ok(made)
    draft, made = await world.card("suspend_customer", {"customer": "Old Gym"}, "operator")
    assert draft.confirm_name == "Old Gym"
    _ok(made)
    draft, made = await world.card("delete_customer", {"customer": "Last Shop"}, "operator")
    assert draft.confirm_name == "Last Shop"
    _ok(made)
    out["customers"] = {
        "approved": await world.scalar(
            "SELECT status || ':' || plan_id::text FROM tenants WHERE id = :t",
            t=ids["Anand Dental Care"],
        ),
        "plan_of_cafe": await world.scalar(
            "SELECT plan_id::text FROM tenants WHERE id = :t", t=ids["Bright Cafe"]
        ),
        "gym": await world.scalar("SELECT status FROM tenants WHERE id = :t", t=ids["Old Gym"]),
        "shop_left": await world.scalar(
            "SELECT count(*) FROM tenants WHERE id = :t", t=ids["Last Shop"]
        ),
        "pilot": pilot["id"],
    }
    return out


async def _forge(world: World, tool: str, args: str, risk: str) -> uuid.UUID:
    """A proposal row for the member that COB never proposed, inserted directly."""
    forged = uuid.uuid4()
    async with world.engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO cob_action_proposals (id, user_id, tenant_id, conversation_id, tool, "
                "args, summary, risk, status, expires_at) VALUES (:id, :u, :t, :c, :tool, "
                "CAST(:a AS jsonb), 's', :risk, 'pending', now() + interval '10 minutes')"
            ),
            {
                "id": forged,
                "u": world.member_id,
                "t": world.tenant,
                "c": world.conversation,
                "tool": tool,
                "a": args,
                "risk": risk,
            },
        )
    return forged


async def _forged(world: World) -> dict[str, Any]:
    """Confirming a member's forged row reaches the route, which refuses the member exactly as
    it refuses the dashboard; a member whose role is read as it is now never gets that far."""
    forged = await _forge(world, "create_knowledge_base", '{"name": "Forged"}', "low")
    direct = await world.api("POST", "/api/v1/corpora", "member", json={"name": "Forged"})
    # The role the card was made under (a stale one), with the member's own token.
    stale = CobCaller(
        CobRole.OWNER, world.member_id, world.tenant, None, f"Bearer {world.member_token}"
    )
    async with world.factory() as session:
        result = await proposals.confirm(
            session, _request(world.member_id), stale, forged, None, client=world.clients["member"]
        )
    now = await _forge(
        world,
        "rotate_key",
        json.dumps({"key_id": str(uuid.uuid4()), "key_name": "x", "scope": "workspace"}),
        "medium",
    )
    refused = "not refused"
    async with world.factory() as session:
        try:
            await proposals.confirm(
                session,
                _request(world.member_id),
                world.caller("member"),
                now,
                None,
                client=world.clients["member"],
            )
        except APIError as err:
            refused = f"{err.status_code}:{err.code}"
    return {
        "dashboard": (direct.status_code, direct.json().get("code")),
        "card": (result.status, result.http_status, result.code),
        "created": await world.scalar("SELECT count(*) FROM corpora WHERE name = 'Forged'"),
        "role_now": refused,
    }


async def test_every_action_runs_through_its_real_route(
    migrated_postgres: Any, redis_container: Any
) -> None:
    from callobuzz_rag.accounts.audit import AUDIT_ACTIONS
    from tests.integration.conftest import _async_url
    from tests.integration.test_accounts_flow import _redis_url

    world = World()

    def capture(template: str, to: str, context: Any, *, expires_s: float) -> None:
        world.mail.append((template, to, dict(context)))

    with (
        patch("callobuzz_rag.accounts.email.dispatch.enqueue_email", capture),
        patch("callobuzz_rag.evaluation.golden_runner.enqueue_publish_runs", AsyncMock()),
    ):
        await world.start(_async_url(migrated_postgres), _redis_url(redis_container))
        try:
            out = await _scenario(world)
            async with world.factory() as session:
                audit: list[Any] = list((await session.execute(select(AuditLog))).scalars().all())
                where = CobActionProposal.conversation_id == world.conversation
                found = await session.execute(select(CobActionProposal).where(where))
                cards: list[Any] = list(found.scalars().all())
        finally:
            await world.stop()

    assert out["origins"] == ["https://shop.in", "https://shreelabs.in"]
    assert out["widget_keys_left"] == 0
    assert out["language"] == "hi"
    assert out["guided"]
    assert out["dispatched"] == 1 and out["retried_flag"] == [True]
    assert out["second_retry_offer"] == "ToolResult"
    assert out["document_left"] == 0 and out["scratch_left"] == 0
    assert out["invite_mailed"] is True
    assert out["ravi_role"] == "owner"
    customers = out["customers"]
    assert customers["approved"] == f"active:{customers['pilot']}"
    assert customers["plan_of_cafe"] == customers["pilot"]
    assert customers["gym"] == "suspended" and customers["shop_left"] == 0

    forged = out["forged"]
    assert forged["created"] == 0
    assert forged["card"][0] == "failed" and forged["card"][1:] == forged["dashboard"]
    assert forged["role_now"] == "409:cob_proposal_scope_changed"

    # The route's own audit row sits next to cob.action for the confirmed cards.
    actions = {a.action for a in audit}
    assert {
        "api_key.rotated",
        "api_key.revoked",
        "assistant.published",
        "assistant.enabled_changed",
        "member.invited",
        "member.role_changed",
        "tenant.activated",
        "tenant.plan_changed",
        "tenant.suspended",
        "tenant.deleted",
        "cob.action",
    } <= actions
    assert actions <= AUDIT_ACTIONS
    # Every card that reached its route is audited; one refused before it (role changed) is not.
    ran = [
        c
        for c in cards
        if c.status in ("done", "failed") and c.result["code"] != "cob_proposal_scope_changed"
    ]
    mine = {str(c.id) for c in cards}
    assert len(
        [a for a in audit if a.action == "cob.action" and a.details.get("proposal_id") in mine]
    ) == len(ran)

    # The rotated key is in no stored place: not the card, not the audit.
    secret = out["secret"]
    assert all(secret not in str(c.result) and secret not in str(c.args) for c in cards)
    assert all(secret not in str(a.details) for a in audit)
