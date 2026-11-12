"""The 18 action tools: what each proposes (title, summary, args, risk, typed name) and what
each calls once confirmed, against a fake in-process client that answers from recorded JSON."""

import uuid
from typing import Any

import pytest

from callobuzz_rag.cob.tools import REGISTRY
from callobuzz_rag.cob.tools.actions.common import ProposalDraft, origin_of, site_in
from callobuzz_rag.cob.tools.base import ToolResult
from callobuzz_rag.cob.tools.http import ToolRouteNotAllowed
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_tool_helpers import FakeToolContext

SHREE = {"id": str(uuid.uuid4()), "name": "Shree Labs"}
CLINIC = {"id": str(uuid.uuid4()), "name": "Clinic"}
KEY_ID = str(uuid.uuid4())


def setup(
    tool: str,
    question: str = "",
    *,
    role: CobRole = CobRole.OWNER,
    kbs: list[dict[str, Any]] | None = None,
    language: str = "en",
) -> tuple[Any, Any, FakeToolContext]:
    fake = FakeToolContext(role, language=language, question=question)
    fake.client.reply("GET", "/api/v1/corpora", {"items": kbs if kbs is not None else [SHREE]})
    t = REGISTRY[tool]
    return t, fake.for_tool(t), fake


async def propose(tool: str, question: str, args: dict[str, Any] | None = None, **kw: Any):  # type: ignore[no-untyped-def]
    t, ctx, fake = setup(tool, question, **kw)
    out = await t.propose(ctx, t.args_model.model_validate(args or {}))
    return t, ctx, fake, out


def writes(fake: FakeToolContext) -> list[tuple[str, str]]:
    return [(m, p) for m, p, _ in fake.client.calls if m != "GET"]


# -------------------------------------------------------------- create_knowledge_base


async def test_create_knowledge_base_proposes_and_creates() -> None:
    t, ctx, fake, out = await propose(
        "create_knowledge_base", "create a knowledge base called Dental"
    )
    assert isinstance(out, ProposalDraft)
    assert out.title == "Create a knowledge base" and out.risk == "low"
    assert out.exec_args == {"name": "Dental"} and out.confirm_name is None
    assert "**Dental**" in out.summary and out.args == [("Name", "Dental")]
    new_id = str(uuid.uuid4())
    fake.client.reply("POST", "/api/v1/corpora", {"id": new_id, "api_key": "ck_SECRET"}, 201)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and writes(fake) == [("POST", "/api/v1/corpora")]
    assert fake.client.bodies[-1] == {"name": "Dental"}
    assert done.secret is None and "ck_SECRET" not in done.message
    assert done.link and done.link.route == f"/kbs/{new_id}"


async def test_create_knowledge_base_asks_for_a_name() -> None:
    _, _, fake, out = await propose("create_knowledge_base", "create a knowledge base")
    assert isinstance(out, ToolResult) and "called" in out.markdown
    assert writes(fake) == []


# ------------------------------------------------------------------ create_widget_key


async def test_create_widget_key_resolves_the_site_and_the_only_knowledge_base() -> None:
    t, ctx, fake, out = await propose("create_widget_key", "create a widget key for shreelabs.in")
    assert isinstance(out, ProposalDraft)
    assert out.exec_args == {
        "corpus_id": SHREE["id"],
        "kb_name": "Shree Labs",
        "name": "shreelabs.in",
        "allowed_origins": ["https://shreelabs.in"],
    }
    assert out.risk == "low" and "https://shreelabs.in" in out.summary
    fake.client.reply("POST", "/api/v1/corpora/{corpus_id}/widget-keys", {"id": KEY_ID}, 201)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and writes(fake) == [("POST", f"/api/v1/corpora/{SHREE['id']}/widget-keys")]
    assert fake.client.bodies[-1] == {
        "name": "shreelabs.in",
        "allowed_origins": ["https://shreelabs.in"],
    }


async def test_create_widget_key_without_a_site_asks() -> None:
    _, _, _, out = await propose("create_widget_key", "create a widget key")
    assert isinstance(out, ToolResult) and "website" in out.markdown


async def test_ambiguous_knowledge_bases_ask_with_quick_replies_and_propose_nothing() -> None:
    _, _, fake, out = await propose(
        "create_widget_key", "create a widget key for shop.in", kbs=[SHREE, CLINIC]
    )
    assert isinstance(out, ToolResult)
    assert {q.label for q in out.quick_replies} == {"Shree Labs", "Clinic"}
    assert writes(fake) == []


async def test_a_named_knowledge_base_is_found_in_the_words() -> None:
    _, _, _, out = await propose(
        "create_widget_key", "create a widget key for shop.in on the Clinic kb", kbs=[SHREE, CLINIC]
    )
    assert isinstance(out, ProposalDraft) and out.exec_args["kb_name"] == "Clinic"


def test_sites_are_found_in_plain_words() -> None:
    assert site_in("create a widget key for shreelabs.in") == "https://shreelabs.in"
    assert site_in("for https://Shop.Example.com/page please") == "https://shop.example.com"
    assert site_in("mail me at bob@shop.in") is None
    assert origin_of("*") is None and origin_of("nodots") is None
    assert origin_of("http://localhost:3000") == "http://localhost:3000"


# ----------------------------------------------------------------- add_allowed_site


async def test_add_allowed_site_keeps_the_existing_sites() -> None:
    t, ctx, fake = setup("add_allowed_site", "add shop.in to my widget key")
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        [{"id": KEY_ID, "name": "Main", "allowed_origins": ["https://shreelabs.in"]}],
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft)
    assert out.exec_args["site"] == "https://shop.in" and out.exec_args["key_id"] == KEY_ID
    fake.client.reply(
        "PATCH", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}", {"id": KEY_ID}, 200
    )
    done = await t.execute(ctx, out.exec_args)
    assert done.ok
    assert fake.client.bodies[-1] == {
        "allowed_origins": ["https://shreelabs.in", "https://shop.in"]
    }


async def test_a_site_that_is_already_allowed_proposes_nothing() -> None:
    t, ctx, fake = setup("add_allowed_site", "add shreelabs.in to my widget key")
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        [{"id": KEY_ID, "name": "Main", "allowed_origins": ["https://shreelabs.in"]}],
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and "already allowed" in out.markdown


async def test_two_widget_keys_and_no_name_asks_which() -> None:
    t, ctx, fake = setup("add_allowed_site", "add shop.in to my widget key")
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        [
            {"id": KEY_ID, "name": "Main", "allowed_origins": []},
            {"id": str(uuid.uuid4()), "name": "Blog", "allowed_origins": []},
        ],
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and {q.label for q in out.quick_replies} == {"Main", "Blog"}


# ------------------------------------------------------------------------- team


async def test_invite_member_reads_the_e_mail_and_the_role() -> None:
    t, ctx, fake, out = await propose("invite_member", "invite Bob@Shop.in as an owner")
    assert isinstance(out, ProposalDraft)
    assert out.exec_args == {"email": "bob@shop.in", "role": "owner"}
    fake.client.reply("POST", "/api/v1/tenants/me/members", {"id": "i"}, 201)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and fake.client.bodies[-1] == {"email": "bob@shop.in", "role": "owner"}


async def test_invite_member_defaults_to_member_and_asks_for_the_address() -> None:
    _, _, _, out = await propose("invite_member", "invite bob@shop.in to my team")
    assert isinstance(out, ProposalDraft) and out.exec_args["role"] == "member"
    _, _, _, ask = await propose("invite_member", "invite a teammate")
    assert isinstance(ask, ToolResult) and "e-mail" in ask.markdown


async def test_change_member_role_finds_the_teammate() -> None:
    t, ctx, fake = setup("change_member_role", "make bob@shop.in an owner")
    uid = str(uuid.uuid4())
    fake.client.reply(
        "GET",
        "/api/v1/tenants/me/members",
        {"members": [{"user_id": uid, "email": "bob@shop.in", "name": "Bob", "role": "member"}]},
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.risk == "medium"
    assert out.exec_args == {"user_id": uid, "who": "bob@shop.in", "role": "owner"}
    fake.client.reply("PATCH", "/api/v1/tenants/me/members/{user_id}", {"user_id": uid}, 200)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and writes(fake) == [("PATCH", f"/api/v1/tenants/me/members/{uid}")]
    assert fake.client.bodies[-1] == {"role": "owner"}


async def test_change_member_role_refuses_what_it_cannot_find_or_already_is() -> None:
    t, ctx, fake = setup("change_member_role", "make bob@shop.in an owner")
    fake.client.reply(
        "GET",
        "/api/v1/tenants/me/members",
        {"members": [{"user_id": "u", "email": "bob@shop.in", "name": "Bob", "role": "owner"}]},
    )
    same = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(same, ToolResult) and "already" in same.markdown
    t2, ctx2, fake2 = setup("change_member_role", "make zed@shop.in an owner")
    fake2.client.reply("GET", "/api/v1/tenants/me/members", {"members": []})
    gone = await t2.propose(ctx2, t2.args_model.model_validate({}))
    assert isinstance(gone, ToolResult) and "couldn't find" in gone.markdown


# -------------------------------------------------------------------------- keys


def key_replies(fake: FakeToolContext) -> None:
    fake.client.reply(
        "GET", "/api/v1/tenants/me/keys", {"keys": [{"id": KEY_ID, "name": "Zapier"}]}
    )
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/keys", {"keys": []})
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/widget-keys", [])


async def test_rotate_key_by_name_and_the_secret_comes_back() -> None:
    t, ctx, fake = setup("rotate_key", "rotate my Zapier key")
    key_replies(fake)
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.risk == "medium"
    assert out.exec_args["scope"] == "workspace" and out.exec_args["key_id"] == KEY_ID
    fake.client.reply(
        "POST", "/api/v1/tenants/me/keys/{key_id}/rotate", {"id": KEY_ID, "api_key": "tk_NEW"}, 200
    )
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and done.secret == "tk_NEW" and "tk_NEW" not in done.message
    assert writes(fake) == [("POST", f"/api/v1/tenants/me/keys/{KEY_ID}/rotate")]


async def test_rotate_key_unknown_name_is_not_found() -> None:
    t, ctx, fake = setup("rotate_key", "rotate the Slack key")
    key_replies(fake)
    out = await t.propose(ctx, t.args_model.model_validate({"key": "Slack"}))
    assert isinstance(out, ToolResult) and "couldn't find" in out.markdown


async def test_delete_key_is_typed_and_reads_the_current_name() -> None:
    t, ctx, fake = setup("delete_key", "delete the Zapier key")
    key_replies(fake)
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.risk == "high" and out.confirm_name == "Zapier"
    assert await t.current_name(ctx, out.exec_args) == "Zapier"
    fake.client.reply("DELETE", "/api/v1/tenants/me/keys/{key_id}", None, 204)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and writes(fake) == [("DELETE", f"/api/v1/tenants/me/keys/{KEY_ID}")]


async def test_delete_key_of_a_widget_key_uses_the_widget_route() -> None:
    t, ctx, fake = setup("delete_key", "delete the Blog key")
    fake.client.reply("GET", "/api/v1/tenants/me/keys", {"keys": []})
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/keys", {"keys": []})
    fake.client.reply(
        "GET", "/api/v1/corpora/{corpus_id}/widget-keys", [{"id": KEY_ID, "name": "Blog"}]
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["scope"] == "widget"
    fake.client.reply("DELETE", "/api/v1/corpora/{corpus_id}/widget-keys/{key_id}", None, 204)
    await t.execute(ctx, out.exec_args)
    assert writes(fake) == [("DELETE", f"/api/v1/corpora/{SHREE['id']}/widget-keys/{KEY_ID}")]


async def test_a_key_in_a_late_knowledge_base_is_found_and_checked_directly() -> None:
    many = [{"id": str(uuid.uuid4()), "name": f"Shop number {i:02d}"} for i in range(12)]
    t, ctx, fake = setup("delete_key", "delete the Blog key", kbs=many)
    fake.client.reply("GET", "/api/v1/tenants/me/keys", {"keys": []})
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/keys", {"keys": []})
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/widget-keys", [])
    fake.client.reply(
        "GET", f"/api/v1/corpora/{many[11]['id']}/widget-keys", [{"id": KEY_ID, "name": "Blog"}]
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["corpus_id"] == many[11]["id"]
    fake.client.calls.clear()
    assert await t.current_name(ctx, out.exec_args) == "Blog"
    assert [p for _, p, _ in fake.client.calls] == [f"/api/v1/corpora/{many[11]['id']}/widget-keys"]


async def test_a_named_knowledge_base_lists_only_its_own_keys() -> None:
    many = [{"id": str(uuid.uuid4()), "name": f"Shop number {i:02d}"} for i in range(5)]
    t, ctx, fake = setup("rotate_key", "rotate the Zapier key of Shop number 03", kbs=many)
    fake.client.reply("GET", "/api/v1/tenants/me/keys", {"keys": []})
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/keys", {"keys": []})
    fake.client.reply(
        "GET",
        f"/api/v1/corpora/{many[3]['id']}/keys",
        {"keys": [{"id": KEY_ID, "name": "Zapier"}]},
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["kb_name"] == "Shop number 03"
    keyed = [p for _, p, _ in fake.client.calls if p.endswith("/keys") and "corpora/" in p]
    assert keyed == [f"/api/v1/corpora/{many[3]['id']}/keys"]


# -------------------------------------------------------------------- assistant


async def test_toggle_assistant_reads_on_and_off() -> None:
    t, ctx, fake, off = await propose("toggle_assistant", "turn off the assistant")
    assert isinstance(off, ProposalDraft) and off.exec_args["enabled"] is False
    assert off.title == "Switch the assistant off" and off.risk == "medium"
    _, _, _, on = await propose("toggle_assistant", "switch the assistant on")
    assert isinstance(on, ProposalDraft) and on.exec_args["enabled"] is True
    _, _, _, unsure = await propose("toggle_assistant", "change the assistant")
    assert isinstance(unsure, ToolResult) and "on or off" in unsure.markdown
    fake.client.reply("POST", "/api/v1/corpora/{corpus_id}/assistant/enabled", {"version": 2}, 200)
    await t.execute(ctx, off.exec_args)
    assert fake.client.bodies[-1] == {"enabled": False}


async def test_publish_assistant_pins_the_draft_revision() -> None:
    t, ctx, fake = setup("publish_assistant", "publish my assistant")
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/assistant",
        {"published": None, "draft": {"draft_revision": 4}},
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["base_revision"] == 4
    fake.client.reply("POST", "/api/v1/corpora/{corpus_id}/assistant/publish", {"version": 7}, 200)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and "version 7" in done.message
    assert fake.client.bodies[-1] == {"base_revision": 4}


async def test_publish_assistant_without_a_draft_proposes_nothing() -> None:
    t, ctx, fake = setup("publish_assistant", "publish my assistant")
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/assistant", {"draft": None})
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and "no unpublished changes" in out.markdown


async def test_switch_reply_language_patches_then_publishes() -> None:
    t, ctx, fake = setup("switch_reply_language", "make the assistant reply in Hindi")
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/assistant", {"draft": None})
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["language"] == "hi"
    fake.client.reply(
        "PATCH", "/api/v1/corpora/{corpus_id}/assistant/draft", {"draft_revision": 1}, 200
    )
    fake.client.reply("POST", "/api/v1/corpora/{corpus_id}/assistant/publish", {"version": 3}, 200)
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and "Hindi" in done.message
    assert writes(fake) == [
        ("PATCH", f"/api/v1/corpora/{SHREE['id']}/assistant/draft"),
        ("POST", f"/api/v1/corpora/{SHREE['id']}/assistant/publish"),
    ]
    assert fake.client.bodies[-2] == {
        "patch": {"language": {"primary": "hi", "reply_policy": "fixed"}}
    }
    assert fake.client.bodies[-1] == {"base_revision": 1}


async def test_switch_reply_language_with_a_draft_pending_only_links() -> None:
    t, ctx, fake = setup("switch_reply_language", "make the assistant reply in Hindi")
    fake.client.reply(
        "GET", "/api/v1/corpora/{corpus_id}/assistant", {"draft": {"draft_revision": 2}}
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and "unpublished changes" in out.markdown
    assert out.link and out.link.route == f"/kbs/{SHREE['id']}?tab=assistant"
    assert writes(fake) == []


async def test_switch_reply_language_never_publishes_a_draft_that_appeared_since() -> None:
    t, ctx, fake = setup("switch_reply_language", "")
    fake.client.reply(
        "GET", "/api/v1/corpora/{corpus_id}/assistant", {"draft": {"draft_revision": 2}}
    )
    done = await t.execute(
        ctx, {"corpus_id": SHREE["id"], "kb_name": "Shree Labs", "language": "hi"}
    )
    assert not done.ok and done.code == "draft_pending" and writes(fake) == []


async def test_a_failed_publish_after_the_patch_says_the_draft_is_saved() -> None:
    t, ctx, fake = setup("switch_reply_language", "")
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/assistant", {"draft": None})
    fake.client.reply(
        "PATCH", "/api/v1/corpora/{corpus_id}/assistant/draft", {"draft_revision": 1}, 200
    )
    fake.client.reply(
        "POST",
        "/api/v1/corpora/{corpus_id}/assistant/publish",
        {"detail": "The draft changed.", "code": "draft_conflict"},
        409,
    )
    done = await t.execute(
        ctx, {"corpus_id": SHREE["id"], "kb_name": "Shree Labs", "language": "en"}
    )
    assert not done.ok and done.code == "draft_conflict" and "saved as a draft" in done.message


async def test_guided_setup_is_a_link_only() -> None:
    t, _, fake, out = await propose("start_guided_setup", "start guided setup for my assistant")
    assert isinstance(out, ToolResult)
    assert out.link and out.link.route == f"/kbs/{SHREE['id']}/setup"
    assert writes(fake) == []
    with pytest.raises(NotImplementedError):
        await t.execute(None, {})


# ------------------------------------------------------------------ ingestion


async def test_retry_ingestion_picks_the_failed_file() -> None:
    t, ctx, fake = setup("retry_ingestion", "retry the menu.pdf upload", role=CobRole.MEMBER)
    job = str(uuid.uuid4())
    fake.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {
            "items": [
                {"id": job, "file_name": "menu.pdf", "mode": "auto", "corpus_id": SHREE["id"]},
                {"id": "r", "file_name": None, "mode": "records", "corpus_id": SHREE["id"]},
            ]
        },
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["job_id"] == job
    assert fake.client.calls[-1][2]["status"] == "failed"
    fake.client.reply(
        "POST", "/api/v1/ingest/jobs/{job_id}/retry", {"job_id": "n", "status": "queued"}, 202
    )
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and writes(fake) == [("POST", f"/api/v1/ingest/jobs/{job}/retry")]


async def test_retry_ingestion_skips_an_upload_that_already_runs_again() -> None:
    t, ctx, fake = setup("retry_ingestion", "retry the upload")
    fake.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {
            "items": [
                {"id": "a", "file_name": "menu.pdf", "mode": "auto", "retried": True},
                {"id": "b", "file_name": "faq.pdf", "mode": "auto", "retried": False},
            ]
        },
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["job_id"] == "b"


async def test_retry_ingestion_with_nothing_failed() -> None:
    t, ctx, fake = setup("retry_ingestion", "retry the upload")
    fake.client.reply("GET", "/api/v1/ingest/jobs", {"items": []})
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and "no failed uploads" in out.markdown


# ---------------------------------------------------------------- destructive


async def test_delete_knowledge_base_needs_the_name_and_types_it() -> None:
    t, ctx, fake = setup("delete_knowledge_base", "delete the Shree Labs knowledge base")
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/delete-preview",
        {"confirm_name": "Shree Labs", "counts": {"documents": 12}},
    )
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.risk == "high"
    assert out.confirm_name == "Shree Labs" and "12" in out.summary
    assert await t.current_name(ctx, out.exec_args) == "Shree Labs"
    fake.client.reply("DELETE", "/api/v1/corpora/{corpus_id}", None, 204)
    done = await t.execute(ctx, {**out.exec_args, "confirm_name": "Shree Labs"})
    assert done.ok
    assert fake.client.calls[-1] == (
        "DELETE", f"/api/v1/corpora/{SHREE['id']}", {"confirm_name": "Shree Labs"}
    )  # fmt: skip


async def test_delete_knowledge_base_is_never_guessed_from_the_screen() -> None:
    _, _, fake, out = await propose("delete_knowledge_base", "delete my knowledge base")
    assert isinstance(out, ToolResult) and "Which knowledge base" in out.markdown
    assert writes(fake) == []


async def test_delete_document_finds_the_file() -> None:
    t, ctx, fake = setup("delete_document", "delete menu.pdf")
    doc = str(uuid.uuid4())
    fake.client.reply(
        "GET", "/api/v1/documents", {"items": [{"id": doc, "file_name": "menu.pdf"}], "count": 1}
    )
    fake.client.reply(
        "GET", "/api/v1/documents/{document_id}/delete-preview", {"confirm_name": "menu.pdf"}
    )
    out = await t.propose(ctx, t.args_model.model_validate({"file": "menu.pdf"}))
    assert isinstance(out, ProposalDraft) and out.confirm_name == "menu.pdf"
    fake.client.reply("DELETE", "/api/v1/documents/{document_id}", None, 204)
    await t.execute(ctx, {**out.exec_args, "confirm_name": "menu.pdf"})
    assert fake.client.calls[-1] == (
        "DELETE", f"/api/v1/documents/{doc}", {"corpus_id": SHREE["id"], "confirm_name": "menu.pdf"}
    )  # fmt: skip


async def test_delete_document_asks_which_file() -> None:
    _, _, _, out = await propose("delete_document", "delete a document")
    assert isinstance(out, ToolResult) and "file name" in out.markdown


# ------------------------------------------------------------------- customers

ANAND = {
    "id": str(uuid.uuid4()),
    "name": "Anand Dental Care",
    "status": "pending",
    "corpus_count": 2,
}
PILOT = {"id": str(uuid.uuid4()), "name": "Pilot", "is_active": True}


def admin_setup(tool: str, question: str) -> tuple[Any, Any, FakeToolContext]:
    fake = FakeToolContext(CobRole.ADMIN, question=question)
    fake.client.reply("GET", "/api/v1/admin/tenants", {"items": [ANAND]})
    fake.client.reply("GET", "/api/v1/admin/plans", {"items": [PILOT]})
    t = REGISTRY[tool]
    return t, fake.for_tool(t), fake


async def test_approve_customer_with_a_plan() -> None:
    t, ctx, fake = admin_setup("approve_customer", "approve Anand Dental Care on Pilot")
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.risk == "medium"
    assert out.exec_args["plan_id"] == PILOT["id"] and out.exec_args["tenant_id"] == ANAND["id"]
    assert fake.client.calls[0][2].get("status") == "pending"
    fake.client.reply(
        "POST", "/api/v1/admin/tenants/{tenant_id}/activate", {"id": ANAND["id"]}, 200
    )
    done = await t.execute(ctx, out.exec_args)
    assert done.ok and fake.client.bodies[-1] == {"plan_id": PILOT["id"]}


async def test_a_named_customer_is_searched_for_not_only_the_newest_100() -> None:
    t, ctx, fake = admin_setup("suspend_customer", "suspend Anand Dental Care")
    fake.client.reply("GET", "/api/v1/admin/tenants", {"items": []})
    fake.client.reply(
        "GET", "/api/v1/admin/tenants", {"items": [ANAND]}, when={"search": "Anand Dental Care"}
    )
    out = await t.propose(ctx, t.args_model.model_validate({"customer": "Anand Dental Care"}))
    assert isinstance(out, ProposalDraft) and out.exec_args["tenant_id"] == ANAND["id"]
    assert fake.client.calls[0][2].get("search") == "Anand Dental Care"


async def test_approve_customer_without_a_plan_keeps_the_one_it_has() -> None:
    t, ctx, fake = admin_setup("approve_customer", "approve Anand Dental Care")
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["plan_id"] is None
    fake.client.reply(
        "POST", "/api/v1/admin/tenants/{tenant_id}/activate", {"id": ANAND["id"]}, 200
    )
    await t.execute(ctx, out.exec_args)
    assert fake.client.bodies[-1] == {}


async def test_change_customer_plan_needs_a_plan() -> None:
    t, ctx, fake = admin_setup("change_customer_plan", "move Anand Dental Care to Pilot")
    out = await t.propose(ctx, t.args_model.model_validate({}))
    assert isinstance(out, ProposalDraft) and out.exec_args["plan_name"] == "Pilot"
    fake.client.reply("POST", "/api/v1/admin/tenants/{tenant_id}/plan", {"id": ANAND["id"]}, 200)
    await t.execute(ctx, out.exec_args)
    assert fake.client.bodies[-1] == {"plan_id": PILOT["id"]}
    t2, ctx2, _ = admin_setup("change_customer_plan", "change Anand Dental Care plan")
    ask = await t2.propose(ctx2, t2.args_model.model_validate({}))
    assert isinstance(ask, ToolResult) and ask.quick_replies


async def test_suspend_and_delete_customer_are_typed() -> None:
    for name, method, body in [
        ("suspend_customer", "POST", {"confirm_name": "Anand Dental Care"}),
        ("delete_customer", "DELETE", None),
    ]:
        t, ctx, fake = admin_setup(name, f"{name.split('_')[0]} Anand Dental Care")
        fake.client.reply("GET", "/api/v1/admin/tenants/{tenant_id}", {"name": "Anand Dental Care"})
        out = await t.propose(ctx, t.args_model.model_validate({}))
        assert isinstance(out, ProposalDraft) and out.risk == "high"
        assert out.confirm_name == "Anand Dental Care"
        assert await t.current_name(ctx, out.exec_args) == "Anand Dental Care"
        fake.client.reply(method, "/api/v1/admin/tenants/{tenant_id}/suspend", {"id": "t"}, 200)
        fake.client.reply(method, "/api/v1/admin/tenants/{tenant_id}", None, 204)
        await t.execute(ctx, {**out.exec_args, "confirm_name": "Anand Dental Care"})
        last = fake.client.calls[-1]
        assert last[0] == method and last[1].startswith(f"/api/v1/admin/tenants/{ANAND['id']}")
        if body is not None:
            assert fake.client.bodies[-1] == body
        else:
            assert last[2] == {"confirm_name": "Anand Dental Care"}


async def test_no_pending_customer_to_approve() -> None:
    fake = FakeToolContext(CobRole.ADMIN, question="approve someone")
    fake.client.reply("GET", "/api/v1/admin/tenants", {"items": []})
    t = REGISTRY["approve_customer"]
    out = await t.propose(fake.for_tool(t), t.args_model.model_validate({}))
    assert isinstance(out, ToolResult) and "waiting for approval" in out.markdown


# ------------------------------------------------------------- the safety rails


async def test_a_tool_cannot_call_a_route_it_did_not_declare() -> None:
    _, ctx, _ = setup("create_widget_key", "")
    with pytest.raises(ToolRouteNotAllowed):
        await ctx.client.request("DELETE", f"/api/v1/corpora/{SHREE['id']}")


async def test_a_route_refusal_is_the_outcome_not_an_exception() -> None:
    t, ctx, fake = setup("create_knowledge_base", "")
    fake.client.reply(
        "POST",
        "/api/v1/corpora",
        {"detail": "Your plan allows 1 knowledge base.", "code": "kb_limit_reached"},
        409,
    )
    done = await t.execute(ctx, {"name": "Dental"})
    assert not done.ok and done.status == 409 and done.code == "kb_limit_reached"
    assert done.message == "Your plan allows 1 knowledge base."


async def test_cards_speak_hindi_and_hinglish() -> None:
    _, _, _, hi = await propose(
        "create_knowledge_base", "create a knowledge base called Dental", language="hi"
    )
    assert isinstance(hi, ProposalDraft) and hi.title == "नॉलेज बेस बनाएँ"
    _, _, _, hl = await propose(
        "create_knowledge_base", "create a knowledge base called Dental", language="hi-Latn"
    )
    assert isinstance(hl, ProposalDraft) and hl.title == "Knowledge base banayein"


async def test_add_allowed_site_never_overwrites_when_the_sites_cannot_be_read() -> None:
    t, ctx, fake = setup("add_allowed_site", "add shop.in to my widget key")
    exec_args = {
        "corpus_id": SHREE["id"],
        "kb_name": "Shree Labs",
        "key_id": KEY_ID,
        "key_name": "Main",
        "site": "https://shop.in",
    }
    # The list of keys fails: nothing is written.
    fake.client.reply("GET", "/api/v1/corpora/{corpus_id}/widget-keys", {"detail": "x"}, 500)
    done = await t.execute(ctx, exec_args)
    assert not done.ok and done.code == "unavailable" and done.status == 503
    assert writes(fake) == []
    # The key is no longer listed: nothing is written either.
    fake.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        [{"id": str(uuid.uuid4()), "name": "Other", "allowed_origins": []}],
    )
    done = await t.execute(ctx, exec_args)
    assert not done.ok and done.code == "not_found" and "**Main**" in done.message
    assert writes(fake) == []
