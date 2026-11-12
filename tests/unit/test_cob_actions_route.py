"""POST /api/v1/cob/actions/{id}/confirm|cancel through the real app and middleware."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from callobuzz_rag.db.models.cob_action_proposal import CobActionProposal
from callobuzz_rag.db.models.conversation import Conversation

KB = uuid.uuid4()
ARGS = {
    "corpus_id": str(KB),
    "kb_name": "Shree Labs",
    "name": "Main site",
    "allowed_origins": ["https://shreelabs.in"],
}


def card(cob: Any, user: Any, tenant: Any, *, tool: str = "create_widget_key") -> CobActionProposal:
    conv = cob.db.add(
        Conversation(corpus_id=cob.world.corpus.id, kind="cob", channel="cob", message_count=0)
    )
    now = datetime.now(UTC)
    return cob.db.add(
        CobActionProposal(
            id=uuid.uuid4(),
            user_id=user.id,
            tenant_id=tenant.id,
            conversation_id=conv.id,
            tool=tool,
            args=ARGS,
            summary="s",
            risk="low",
            status="pending",
            created_at=now,
            expires_at=now + timedelta(seconds=600),
        )
    )


def path(row: CobActionProposal, verb: str = "confirm") -> str:
    return f"/api/v1/cob/actions/{row.id}/{verb}"


async def test_an_owner_confirms_their_card(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.internal.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": "k"}, 201)
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("alice", path(row), {})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "done" and body["http_status"] == 201 and body["secret"] is None
    assert [(m, p) for m, p, _ in cob.world.internal.calls] == [
        ("POST", f"/api/v1/corpora/{KB}/widget-keys")
    ]
    again = await cob.post("alice", path(row), {})
    assert again.status_code == 409 and again.json()["code"] == "cob_proposal_used"


async def test_a_route_refusal_is_a_failed_result_not_an_http_error(cob) -> None:  # type: ignore[no-untyped-def]
    cob.world.internal.reply(
        "POST",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        {"detail": "Your plan allows 1 widget key.", "code": "widget_key_limit_reached"},
        409,
    )
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("alice", path(row), {})
    assert res.status_code == 200
    assert res.json()["status"] == "failed" and res.json()["code"] == "widget_key_limit_reached"
    assert res.json()["http_status"] == 409


async def test_a_member_cannot_confirm_an_owner_tool(cob) -> None:  # type: ignore[no-untyped-def]
    """A forged row for a member: the tool is not offered to a member, so nothing is called."""
    row = card(cob, cob.world.ravi, cob.world.shree)
    res = await cob.post("ravi", path(row), {})
    assert res.status_code == 409 and res.json()["code"] == "cob_proposal_scope_changed"
    assert cob.world.internal.calls == []


async def test_someone_elses_card_is_a_404(cob) -> None:  # type: ignore[no-untyped-def]
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("ravi", path(row), {})
    assert res.status_code == 404 and res.json()["code"] == "cob_proposal_not_found"
    assert (await cob.post("ravi", path(row, "cancel"))).status_code == 404


async def test_the_other_workspace_of_the_same_person_is_refused(cob) -> None:  # type: ignore[no-untyped-def]
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("alice_anand", path(row), {})
    assert res.status_code == 409 and res.json()["code"] == "cob_proposal_scope_changed"


async def test_cancel_then_confirm(cob) -> None:  # type: ignore[no-untyped-def]
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("alice", path(row, "cancel"))
    assert res.status_code == 200 and res.json()["status"] == "cancelled"
    after = await cob.post("alice", path(row), {})
    assert after.status_code == 409 and after.json()["code"] == "cob_proposal_used"


async def test_the_body_is_strict(cob) -> None:  # type: ignore[no-untyped-def]
    row = card(cob, cob.world.alice, cob.world.shree)
    res = await cob.post("alice", path(row), {"confirm_name": "x", "extra": 1})
    assert res.status_code == 422


async def test_switched_off_cob_answers_404(cob) -> None:  # type: ignore[no-untyped-def]
    row = card(cob, cob.world.alice, cob.world.shree)
    cob.app.state.settings = cob.app.state.settings.model_copy(update={"cob_enabled": False})
    res = await cob.post("alice", path(row), {})
    assert res.status_code == 404 and res.json()["code"] == "cob_disabled"
