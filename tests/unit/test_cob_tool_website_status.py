"""COB's website_status card: what the assistant learned from the site, in plain words."""

import uuid
from typing import Any

import pytest

from callobuzz_rag.cob import tools as _tools  # noqa: F401  (registers the read tools)
from callobuzz_rag.cob.tools.registry import REGISTRY
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_tool_helpers import NOW, FakeToolContext

KB = {"id": str(uuid.uuid4()), "name": "Shree Dental", "document_count": 40}
KB2 = {"id": str(uuid.uuid4()), "name": "Shree Ortho", "document_count": 4}


def corpora(*kbs: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(kbs), "count": len(kbs), "total": len(kbs), "offset": 0, "limit": 100}


def website(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "settings": {"enabled": True, "exclude_patterns": [], "refresh_mode": "daily"},
        "plan": {"learning_enabled": True, "pages_cap": 200, "pages_learned": 42},
        "sources": [
            {
                "id": str(uuid.uuid4()),
                "origin": "https://shreedental.in",
                "scope": "shreedental.in",
                "status": "ready",
                "pages_found": 45,
                "pages_learned": 42,
                "pages_skipped": 3,
                "last_run_finished_at": "2026-10-04T07:00:00Z",
            }
        ],
        "learn_key": {"set": True, "hint": "ab12"},
        "unlearned_origins": [],
    }
    body.update(over)
    return body


def skipped_pages() -> dict[str, Any]:
    reasons = {"private_page": 2, "duplicate": 1}
    return {"items": [], "total": 3, "counts": {"skipped": 3}, "reasons": reasons}


async def run(ctx: FakeToolContext, **args: Any) -> Any:
    tool = REGISTRY["website_status"]
    return await tool.run(ctx.for_tool(tool), tool.args_model(**args))  # type: ignore[attr-defined]


def wire(ctx: FakeToolContext, kb: dict[str, Any], body: dict[str, Any]) -> None:
    ctx.client.reply("GET", "/api/v1/corpora", corpora(kb))
    ctx.client.reply("GET", f"/api/v1/corpora/{kb['id']}/website", body)
    ctx.client.reply("GET", f"/api/v1/corpora/{kb['id']}/website/pages", skipped_pages())


def test_registered_for_the_four_workspace_roles() -> None:
    tool = REGISTRY["website_status"]
    assert tool.kind == "read"
    assert tool.roles == frozenset(
        {CobRole.OWNER, CobRole.MEMBER, CobRole.OPERATOR_OWNER, CobRole.ADMIN}
    )
    assert {(r.method, r.path) for r in tool.routes} == {
        ("GET", "/api/v1/corpora"),
        ("GET", "/api/v1/corpora/{corpus_id}/website"),
        ("GET", "/api/v1/corpora/{corpus_id}/website/pages"),
    }


async def test_owner_card_in_english() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    wire(ctx, KB, website())
    out = await run(ctx)
    assert out.ok
    assert "shreedental.in" in out.markdown and "42 of 45" in out.markdown
    assert "Up to date" in out.markdown
    assert "2 h ago" in out.markdown  # NOW is 09:00, last run 07:00
    assert "Login or private page" in out.markdown and "2" in out.markdown
    assert "Every day" in out.markdown
    assert out.link is not None and out.link.route == "/knowledge?tab=website"
    assert "ab12" not in out.markdown  # the key hint is not for a card


async def test_card_in_hinglish() -> None:
    ctx = FakeToolContext(CobRole.MEMBER, language="hi-Latn")
    wire(ctx, KB, website())
    out = await run(ctx)
    assert "shreedental.in" in out.markdown and "pages seekhe gaye" in out.markdown
    assert out.link is not None and out.link.route == "/knowledge?tab=website"


async def test_no_site_yet_sends_an_owner_to_the_wizard_and_a_member_to_knowledge() -> None:
    owner = FakeToolContext(CobRole.OWNER)
    wire(owner, KB, website(sources=[]))
    out = await run(owner)
    assert "isn't reading a website yet" in out.markdown
    assert out.link is not None and out.link.route == "/welcome"
    member = FakeToolContext(CobRole.MEMBER)
    wire(member, KB, website(sources=[]))
    out = await run(member)
    assert out.link is not None and out.link.route == "/knowledge"


async def test_learning_off_by_plan_is_said_plainly() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    wire(
        ctx,
        KB,
        website(
            plan={"learning_enabled": False, "pages_cap": 0, "pages_learned": 0, "reason": "plan"}
        ),
    )
    out = await run(ctx)
    assert "isn't part of your plan" in out.markdown


async def test_two_knowledge_bases_without_a_name_ask_which() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB, KB2))
    out = await run(ctx)
    assert out.data == {"clarify": True}
    assert [c[2] for c in ctx.client.calls] == [{"limit": 100}]  # only the list was read


async def test_a_plan_refusal_becomes_the_standard_failure_card() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB))
    ctx.client.reply(
        "GET",
        f"/api/v1/corpora/{KB['id']}/website",
        {"detail": "Not in your plan", "code": "feature_not_in_plan"},
        status=403,
    )
    out = await run(ctx)
    assert not out.ok and out.data == {"failure": "forbidden"}


async def test_only_declared_routes_are_called() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    wire(ctx, KB, website())
    await run(ctx)
    paths = {c[1] for c in ctx.client.calls}
    assert paths <= {
        "/api/v1/corpora",
        f"/api/v1/corpora/{KB['id']}/website",
        f"/api/v1/corpora/{KB['id']}/website/pages",
    }
    assert NOW  # the context clock is the card's "now"


@pytest.mark.parametrize("status", ["learning", "paused", "blocked"])
async def test_every_status_has_words(status: str) -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    body = website()
    body["sources"][0]["status"] = status
    wire(ctx, KB, body)
    out = await run(ctx)
    assert "wst_" not in out.markdown and "{" not in out.markdown


async def test_reasons_come_from_the_server_counts_not_a_first_page_sample() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    body = website()
    body["sources"][0]["pages_skipped"] = 460
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB))
    ctx.client.reply("GET", f"/api/v1/corpora/{KB['id']}/website", body)
    counts = {"over_limit": 400, "owner": 40, "redirect_off_site": 15, "weird_new": 5}
    ctx.client.reply(
        "GET",
        f"/api/v1/corpora/{KB['id']}/website/pages",
        {"items": [], "total": 460, "counts": {}, "reasons": counts},
    )
    out = await run(ctx)
    assert "Beyond your plan's page limit (400)" in out.markdown
    assert "You left this page out (40)" in out.markdown  # owner-excluded pages are listed
    assert "Moved to another website (15)" in out.markdown
    assert "Other reasons" not in out.markdown  # only the top three show


async def test_unknown_and_off_site_reasons_have_words() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    reasons = {"out_of_scope": 3, "canonical_off_site": 2, "weird_new": 1}
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB))
    ctx.client.reply("GET", f"/api/v1/corpora/{KB['id']}/website", website())
    ctx.client.reply(
        "GET",
        f"/api/v1/corpora/{KB['id']}/website/pages",
        {"items": [], "total": 6, "counts": {}, "reasons": reasons},
    )
    out = await run(ctx)
    assert "Not part of your website (3)" in out.markdown
    assert "Belongs to another website (2)" in out.markdown
    assert "Other reasons (1)" in out.markdown


@pytest.mark.parametrize("reason", ["engine", "system", "tenant"])
async def test_learning_off_for_a_non_plan_reason_is_not_blamed_on_the_plan(reason: str) -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    plan = {"learning_enabled": False, "pages_cap": 0, "pages_learned": 0, "reason": reason}
    wire(ctx, KB, website(plan=plan))
    out = await run(ctx)
    assert "isn't available for your account right now" in out.markdown
    assert "your plan" not in out.markdown
