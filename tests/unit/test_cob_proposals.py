"""COB's proposals: a card runs once, for the right person, in the right workspace, in time.

The proposals service is driven directly with a fake clock and a fake internal client, so the
tests prove the rules (single use under concurrency, ten minutes, scope, typed name, the route's
own failure reported, audit and message written) without any model or HTTP in the way.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from callobuzz_rag.api.errors import APIError
from callobuzz_rag.cob import proposals
from callobuzz_rag.cob.tools.actions.common import ProposalDraft
from callobuzz_rag.cob.types import CobCaller, CobRole
from callobuzz_rag.core.config import Settings
from callobuzz_rag.db.models.audit_log import AuditLog
from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
from callobuzz_rag.db.models.conversation import Conversation
from callobuzz_rag.db.models.message import Message
from tests.unit.accounts_fakes import FakeDB, request_stub
from tests.unit.cob_tool_helpers import FakeInternalClient

KB = uuid.uuid4()
SHREE = uuid.uuid4()
ANAND = uuid.uuid4()
ALICE = uuid.uuid4()
RAVI = uuid.uuid4()
START = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)

WIDGET_ARGS = {
    "corpus_id": str(KB),
    "kb_name": "Shree Labs",
    "name": "Main site",
    "allowed_origins": ["https://shreelabs.in"],
}


class Clock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at

    def advance(self, seconds: int) -> None:
        self.at += timedelta(seconds=seconds)


class Env:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.clock = Clock()
        self.client = FakeInternalClient()
        self.conv = self.db.add(
            Conversation(corpus_id=uuid.uuid4(), kind="cob", channel="cob", message_count=0)
        )
        self.client.reply(
            "POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": str(uuid.uuid4())}, 201
        )
        self.client.reply(
            "GET",
            "/api/v1/corpora/{corpus_id}/delete-preview",
            {
                "confirm_name": "Shree Labs",
                "name": "Shree Labs",
                "counts": {"documents": 4},
            },
        )
        self.client.reply("DELETE", "/api/v1/corpora/{corpus_id}", None, 204)
        self.client.reply(
            "POST", "/api/v1/corpora", {"id": str(uuid.uuid4()), "api_key": "ck_X"}, 201
        )

    def caller(
        self,
        *,
        user: uuid.UUID = ALICE,
        tenant: uuid.UUID | None = SHREE,
        role: CobRole = CobRole.OWNER,
    ) -> CobCaller:
        return CobCaller(role, user, tenant, uuid.uuid4(), "Bearer test")

    def request(self, user: uuid.UUID = ALICE) -> Any:
        stub = request_stub(Settings())
        stub.state.user = SimpleNamespace(id=user, email="alice@shree.example")
        return stub

    async def propose(
        self,
        tool: str = "create_widget_key",
        exec_args: dict[str, Any] | None = None,
        *,
        confirm_name: str | None = None,
        caller: CobCaller | None = None,
    ) -> CobActionProposal:
        draft = ProposalDraft(
            title="t",
            summary="s",
            args=[],
            risk="high" if confirm_name else "low",
            exec_args=exec_args if exec_args is not None else dict(WIDGET_ARGS),
            confirm_name=confirm_name,
        )
        session = self.db.session()
        row = await proposals.create_proposal(
            session, caller or self.caller(), self.conv.id, tool, draft, now=self.clock.now()
        )
        await session.commit()
        return row

    async def confirm(
        self,
        pid: uuid.UUID,
        *,
        user: uuid.UUID = ALICE,
        tenant: uuid.UUID | None = SHREE,
        role: CobRole = CobRole.OWNER,
        confirm_name: str | None = None,
    ) -> Any:
        return await proposals.confirm(
            self.db.session(),
            self.request(user),
            self.caller(user=user, tenant=tenant, role=role),
            pid,
            confirm_name,
            client=self.client,
            now=self.clock.now(),
        )

    async def row(self, pid: uuid.UUID) -> CobActionProposal:
        row = await self.db.session().get(CobActionProposal, pid)
        assert row is not None
        return row

    def calls(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p, _ in self.client.calls if m != "GET"]

    def audit_rows(self, action: str) -> list[AuditLog]:
        return [a for a in self.db.all(AuditLog) if a.action == action]

    def messages(self) -> list[Message]:
        return self.db.all(Message)


@pytest.fixture
def env() -> Env:
    return Env()


async def test_confirm_is_single_use_under_concurrency(env: Env) -> None:
    p = await env.propose()
    results = await asyncio.gather(*[env.confirm(p.id) for _ in range(5)], return_exceptions=True)
    ok = [r for r in results if not isinstance(r, Exception)]
    used = [r for r in results if isinstance(r, APIError) and r.code == "cob_proposal_used"]
    assert len(ok) == 1 and len(used) == 4
    assert env.calls() == [("POST", f"/api/v1/corpora/{KB}/widget-keys")]


async def test_expired_after_ten_minutes(env: Env) -> None:
    p = await env.propose()
    env.clock.advance(601)
    with pytest.raises(APIError) as e:
        await env.confirm(p.id)
    assert e.value.status_code == 410 and e.value.code == "cob_proposal_expired"
    assert env.calls() == []
    assert (await env.row(p.id)).status == "expired"


async def test_still_valid_just_before_ten_minutes(env: Env) -> None:
    p = await env.propose()
    env.clock.advance(599)
    assert (await env.confirm(p.id)).status == "done"


async def test_other_user_gets_404(env: Env) -> None:
    p = await env.propose()
    with pytest.raises(APIError) as e:
        await env.confirm(p.id, user=RAVI)
    assert e.value.status_code == 404 and e.value.code == "cob_proposal_not_found"
    assert env.calls() == []
    assert (await env.row(p.id)).status == "pending"  # theirs is untouched


async def test_unknown_id_gets_the_same_404(env: Env) -> None:
    with pytest.raises(APIError) as e:
        await env.confirm(uuid.uuid4())
    assert e.value.code == "cob_proposal_not_found"


async def test_workspace_switch_invalidates(env: Env) -> None:
    p = await env.propose()
    with pytest.raises(APIError) as e:
        await env.confirm(p.id, tenant=ANAND)
    assert e.value.status_code == 409 and e.value.code == "cob_proposal_scope_changed"
    assert env.calls() == []
    assert (await env.row(p.id)).status == "failed"


async def test_role_downgrade_invalidates(env: Env) -> None:
    p = await env.propose()  # alice was an owner when the card was made
    with pytest.raises(APIError) as e:
        await env.confirm(p.id, role=CobRole.MEMBER)
    assert e.value.code == "cob_proposal_scope_changed"
    assert env.calls() == []


async def test_wrong_typed_name_keeps_it_pending(env: Env) -> None:
    p = await env.propose(
        "delete_knowledge_base",
        {"corpus_id": str(KB), "kb_name": "Shree Labs"},
        confirm_name="Shree Labs",
    )
    with pytest.raises(APIError) as e:
        await env.confirm(p.id, confirm_name="Shree lab")
    assert e.value.status_code == 422 and e.value.code == "confirm_mismatch"
    assert (await env.row(p.id)).status == "pending"
    assert env.calls() == []
    res = await env.confirm(p.id, confirm_name=" Shree Labs ")
    assert res.status == "done"
    assert env.calls() == [("DELETE", f"/api/v1/corpora/{KB}")]


async def test_typed_name_is_compared_with_the_current_name(env: Env) -> None:
    """The card said "Old Name"; the knowledge base was renamed since, so the new name is typed."""
    p = await env.propose(
        "delete_knowledge_base",
        {"corpus_id": str(KB), "kb_name": "Old Name"},
        confirm_name="Old Name",
    )
    with pytest.raises(APIError) as e:
        await env.confirm(p.id, confirm_name="Old Name")
    assert e.value.code == "confirm_mismatch"
    assert (await env.confirm(p.id, confirm_name="Shree Labs")).status == "done"


async def test_missing_typed_name_is_refused(env: Env) -> None:
    p = await env.propose(
        "delete_knowledge_base",
        {"corpus_id": str(KB), "kb_name": "Shree Labs"},
        confirm_name="Shree Labs",
    )
    with pytest.raises(APIError) as e:
        await env.confirm(p.id)
    assert e.value.code == "confirm_mismatch"


async def test_route_failure_is_reported_not_raised(env: Env) -> None:
    env.client.reply(
        "POST",
        "/api/v1/corpora",
        {"detail": "Your plan allows 1 knowledge base.", "code": "kb_limit_reached"},
        409,
    )
    p = await env.propose("create_knowledge_base", {"name": "Dental"})
    res = await env.confirm(p.id)
    assert res.status == "failed" and res.code == "kb_limit_reached" and res.http_status == 409
    assert "plan allows" in res.message
    assert (await env.row(p.id)).status == "failed"
    assert env.audit_rows("cob.action")[-1].details["ok"] is False
    assert env.messages()[-1].content.startswith("That didn't work:")


async def test_audit_and_message_written(env: Env) -> None:
    p = await env.propose()
    res = await env.confirm(p.id)
    (row,) = env.audit_rows("cob.action")
    assert row.details == {
        "tool": "create_widget_key",
        "method": "POST",
        "path": "/api/v1/corpora/{corpus_id}/widget-keys",
        "proposal_id": str(res.proposal_id),
        "status_code": 201,
        "ok": True,
    }
    last = env.messages()[-1]
    assert last.role == "assistant" and last.content.startswith("Done:")
    assert env.conv.message_count == 1
    stored = await env.row(p.id)
    assert stored.status == "done" and stored.result and stored.result["ok"] is True


async def test_cancel(env: Env) -> None:
    p = await env.propose()
    done = await proposals.cancel(env.db.session(), env.caller(), p.id, now=env.clock.now())
    assert done.status == "cancelled"
    with pytest.raises(APIError) as e:
        await env.confirm(p.id)
    assert e.value.code == "cob_proposal_used"
    with pytest.raises(APIError) as again:
        await proposals.cancel(env.db.session(), env.caller(), p.id, now=env.clock.now())
    assert again.value.code == "cob_proposal_used"
    assert env.calls() == []


async def test_cancel_of_someone_elses_card_is_404(env: Env) -> None:
    p = await env.propose()
    with pytest.raises(APIError) as e:
        await proposals.cancel(env.db.session(), env.caller(user=RAVI), p.id, now=env.clock.now())
    assert e.value.code == "cob_proposal_not_found"


async def test_card_statuses_show_expiry(env: Env) -> None:
    fresh = await env.propose()
    old = await env.propose()
    cancelled = await env.propose()
    await proposals.cancel(env.db.session(), env.caller(), cancelled.id, now=env.clock.now())
    later = env.clock.now() + timedelta(seconds=700)
    got = await proposals.card_statuses(
        env.db.session(), [fresh.id, old.id, cancelled.id], now=later
    )
    assert got == {fresh.id: "expired", old.id: "expired", cancelled.id: "cancelled"}
    now_ = await proposals.card_statuses(env.db.session(), [fresh.id], now=env.clock.now())
    assert now_ == {fresh.id: "pending"}


async def test_a_tool_that_raises_fails_the_card_without_a_crash(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    from callobuzz_rag.cob.tools import REGISTRY

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(REGISTRY["create_widget_key"], "execute", boom)
    p = await env.propose()
    res = await env.confirm(p.id)
    assert res.status == "failed" and res.http_status == 500 and res.code == "cob_action_failed"
    (audit,) = env.audit_rows("cob.action")
    assert audit.details["method"] == "POST"
    assert audit.details["path"] == "/api/v1/corpora/{corpus_id}/widget-keys"


@pytest.mark.parametrize(
    ("reply", "http_status", "code"),
    [
        ({"detail": "x", "code": "owner_required"}, 403, "owner_required"),
        ({"detail": "x"}, 403, "forbidden"),
        ({"detail": "x"}, 404, "not_found"),
        ({"detail": "x"}, 500, "unavailable"),
    ],
)
async def test_a_failed_name_read_keeps_the_routes_status_and_a_whole_sentence(
    env: Env, reply: dict[str, str], http_status: int, code: str
) -> None:
    env.client.reply("GET", "/api/v1/corpora/{corpus_id}/delete-preview", reply, http_status)
    p = await env.propose(
        "delete_knowledge_base",
        {"corpus_id": str(KB), "kb_name": "Shree Labs"},
        confirm_name="Shree Labs",
    )
    res = await env.confirm(p.id, confirm_name="Shree Labs")
    assert res.status == "failed" and res.code == code
    assert res.http_status == (503 if code == "unavailable" else http_status)
    if code == "not_found":
        assert "**Shree Labs**" in res.message
    (audit,) = env.audit_rows("cob.action")
    assert audit.details["method"] == "DELETE" and audit.details["path"].endswith("{corpus_id}")
