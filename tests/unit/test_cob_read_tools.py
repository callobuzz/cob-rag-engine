"""COB's thirteen read tools: each card from recorded route answers, and what a card never shows."""

import uuid
from typing import Any

import pytest

from callobuzz_rag.cob import tools as _tools  # noqa: F401  (registers the read tools)
from callobuzz_rag.cob.tools import cards
from callobuzz_rag.cob.tools.registry import REGISTRY
from callobuzz_rag.cob.types import CobRole
from tests.unit.cob_tool_helpers import FakeToolContext

KB_A = {
    "id": str(uuid.uuid4()),
    "name": "Clinic",
    "document_count": 12,
    "created_at": "2026-09-01T00:00:00Z",
    "last_activity_at": "2026-10-02T08:00:00Z",
    "tenant_name": "Shree Labs",
}
KB_B = {
    "id": str(uuid.uuid4()),
    "name": "Clinic Hindi",
    "document_count": 3,
    "created_at": "2026-09-10T00:00:00Z",
    "last_activity_at": None,
    "tenant_name": "Shree Labs",
}
KB_C = {
    "id": str(uuid.uuid4()),
    "name": "Store",
    "document_count": 40,
    "created_at": "2026-08-10T00:00:00Z",
    "last_activity_at": "2026-10-03T00:00:00Z",
    "tenant_name": "Shree Labs",
}


def corpora(*kbs: dict[str, Any]) -> dict[str, Any]:
    return {"items": list(kbs), "count": len(kbs), "total": len(kbs), "offset": 0, "limit": 100}


async def run(name: str, ctx: FakeToolContext, **args: Any) -> Any:
    tool = REGISTRY[name]
    return await tool.run(ctx.for_tool(tool), tool.args_model(**args))  # type: ignore[attr-defined]


@pytest.fixture
def owner() -> FakeToolContext:
    return FakeToolContext(CobRole.OWNER)


@pytest.fixture
def member() -> FakeToolContext:
    return FakeToolContext(CobRole.MEMBER)


@pytest.fixture
def admin() -> FakeToolContext:
    return FakeToolContext(CobRole.ADMIN)


def test_the_thirteen_tools_exist() -> None:
    assert {n for n, t in REGISTRY.items() if t.kind == "read"} == {
        "plan_usage", "knowledge_bases", "documents", "job_failure", "api_keys", "widget_keys",
        "assistant_status", "leads_handoffs", "my_costs", "engine_health", "customers",
        "platform_costs", "website_status",
    }  # fmt: skip


# ----------------------------------------------------------------------------- cards


def test_cell_text_cannot_break_a_table() -> None:
    out = cards.table(["Name"], [["a|b **x** [l](u)\nnext"]])
    row = out.splitlines()[2]
    assert row.count("|") - row.count("\\|") == 2  # only the two outer bars are real
    assert "\n" not in row and "\\*\\*x\\*\\*" in row


def test_table_shows_ten_rows_and_counts_the_rest() -> None:
    out = cards.table(["N"], [[str(i)] for i in range(14)], more=2, language="en")
    assert len(out.splitlines()) == 2 + 10 + 2
    assert "...and 6 more." in out


def test_numbers_group_like_the_dashboard() -> None:
    assert cards.en_in(1234567) == "12,34,567"
    assert cards.en_in(999) == "999"
    assert cards.en_in(1000) == "1,000"
    assert cards.en_in(12345.5, 2) == "12,345.50"


def test_money_shows_rupees_then_dollars() -> None:
    assert cards.money(14.8, 84.0) == "₹1,243 ($14.80)"
    assert cards.money(0.00004, 84.0) == "< ₹0.01 (< $0.0001)"
    assert cards.money(2.0, None) == "$2.00"
    assert cards.money(None, 84.0) == "price unknown"


# ------------------------------------------------------------------------ plan_usage


async def test_plan_usage_card(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/tenants/me/plan",
        {
            "plan": {"name": "Pilot", "monthly_questions": 300},
            "limits": {"monthly_questions": 300, "max_documents": 50, "max_knowledge_bases": 3, "max_widget_keys": None},
            "usage": {"questions": 212, "documents": 12, "knowledge_bases": 2, "widget_keys": 1},
            "resets_at": "2026-11-01T00:00:00Z",
        },
    )  # fmt: skip
    owner.client.reply(
        "GET", "/api/v1/usage/consumption", {"questions": 212, "question_limit": 300}
    )
    res = await run("plan_usage", owner)
    assert "212 of 300" in res.markdown and "Pilot" in res.markdown
    assert "12 of 50" in res.markdown and "1 (no limit)" in res.markdown
    assert "1 Nov 2026" in res.markdown
    assert res.link is not None and res.link.route == "/plan"


async def test_plan_usage_speaks_hindi() -> None:
    ctx = FakeToolContext(CobRole.OWNER, language="hi")
    ctx.client.reply(
        "GET",
        "/api/v1/tenants/me/plan",
        {
            "plan": {"name": "Pilot"},
            "limits": {"monthly_questions": 300},
            "usage": {"questions": 5},
        },
    )
    res = await run("plan_usage", ctx)
    assert "सवाल" in res.markdown and "5 / 300" in res.markdown
    assert res.link is not None and "खोलिए" in res.link.label


async def test_a_failing_route_gets_an_honest_line_and_the_link(owner: FakeToolContext) -> None:
    res = await run("plan_usage", owner)  # no recorded answer: a timeout
    assert res.markdown == "I couldn't read that right now."
    assert res.link is not None and res.link.route == "/plan" and not res.ok


# ----------------------------------------------------------------- knowledge_bases


async def test_knowledge_bases_card(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    res = await run("knowledge_bases", owner)
    assert "**2** knowledge bases" in res.markdown
    assert "| Clinic | 12 | 1 Sep 2026 |" in res.markdown
    assert "Shree Labs" not in res.markdown  # the workspace column is for the operator only
    assert res.link is not None and res.link.route == "/kbs"


async def test_the_operator_sees_each_workspace(admin: FakeToolContext) -> None:
    admin.client.reply("GET", "/api/v1/corpora", corpora(KB_A))
    res = await run("knowledge_bases", admin)
    assert "Shree Labs" in res.markdown


# ---------------------------------------------------------------------- documents


async def test_documents_across_all_knowledge_bases(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    res = await run("documents", owner)
    assert "**52** documents across **2** knowledge bases" in res.markdown
    assert res.link is not None and res.link.route == "/documents"


async def test_documents_of_the_knowledge_base_named_in_the_question() -> None:
    ctx = FakeToolContext(CobRole.OWNER, question="how many documents in my clinic KB?")
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    ctx.client.reply(
        "GET",
        "/api/v1/documents",
        {
            "count": 1,
            "items": [
                {"ingested_at": "2026-10-02T08:00:00Z"},
                {"ingested_at": "2026-09-20T08:00:00Z"},
            ],
        },
    )
    ctx.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {"items": [{"file_name": "scan.pdf", "finished_at": "2026-10-01T00:00:00Z", "errors": {"code": "no_text_found", "error": "x"}}], "total": 1},
    )  # fmt: skip
    res = await run("documents", ctx)
    assert "**Clinic** has **12** documents" in res.markdown
    assert "2 Oct 2026" in res.markdown and "scan.pdf" in res.markdown
    assert "no readable text" in res.markdown
    assert res.link is not None and res.link.route == f"/documents?kb={KB_A['id']}"
    docs_call = next(c for c in ctx.client.calls if c[1] == "/api/v1/documents")
    assert docs_call[2]["corpus_id"] == KB_A["id"]


async def test_ambiguous_kb_asks_with_quick_replies() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_B, KB_C))
    res = await run("documents", ctx, kb="cli")
    assert "Which knowledge base" in res.markdown
    assert {q.label for q in res.quick_replies} == {"Clinic", "Clinic Hindi"}
    assert res.data.get("clarify") is True


async def test_an_unknown_kb_name_is_not_found(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A))
    res = await run("documents", owner, kb="Warehouse")
    assert res.markdown == "I couldn't find **Warehouse**." and not res.ok


async def test_the_longest_matching_name_wins_in_the_question() -> None:
    ctx = FakeToolContext(CobRole.OWNER, question="documents in clinic hindi please")
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_B))
    ctx.client.reply("GET", "/api/v1/documents", {"count": 0, "items": []})
    ctx.client.reply("GET", "/api/v1/ingest/jobs", {"items": [], "total": 0})
    res = await run("documents", ctx)
    assert "**Clinic Hindi** has **3**" in res.markdown


# ------------------------------------------------------------------- job_failure


async def test_job_failure_explains_known_errors(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {
            "items": [
                {"id": "j1", "file_name": "old.docx", "finished_at": "2026-09-01T00:00:00Z", "errors": {"error": "boom"}},
                {"id": "j2", "file_name": "scan.pdf", "finished_at": "2026-10-03T10:00:00Z", "errors": {"code": "no_text_found", "error": "We couldn't find readable text in this file."}},
            ],
            "total": 2,
        },
    )  # fmt: skip
    res = await run("job_failure", owner)
    assert "**scan.pdf** failed on 3 Oct 2026" in res.markdown
    assert "scanned PDF" in res.markdown and "OCR" in res.markdown
    assert "1 more uploads failed: old.docx" in res.markdown
    assert res.link is not None and res.link.route == "/add?tab=jobs&status=failed"


async def test_job_failure_by_file_name(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {"items": [{"id": "j1", "file_name": "old.docx", "finished_at": "2026-09-01T00:00:00Z", "errors": {"error": "Failed to parse old.docx: bad zip", "error_type": "CorruptedFileError"}}], "total": 1},
    )  # fmt: skip
    res = await run("job_failure", owner, job="old")
    assert "damaged" in res.markdown
    missing = await run("job_failure", owner, job="nothing-like-it")
    assert not missing.ok and "I couldn't find" in missing.markdown


async def test_job_failure_by_id_reads_that_job(owner: FakeToolContext) -> None:
    job_id = str(uuid.uuid4())
    owner.client.reply(
        "GET",
        "/api/v1/ingest/jobs/{job_id}",
        {"id": job_id, "file_name": "a.pdf", "status": "failed", "created_at": "2026-10-01T00:00:00Z", "errors": {"code": "document_limit_reached"}},
    )  # fmt: skip
    owner.question = f"what went wrong with job {job_id}?"
    res = await run("job_failure", owner, job=job_id)
    assert "document limit is full" in res.markdown


async def test_job_failure_ignores_an_id_the_person_did_not_type(
    owner: FakeToolContext,
) -> None:
    job_id = str(uuid.uuid4())
    owner.question = "why did my upload fail?"
    owner.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {"items": [{"id": "j1", "file_name": "a.pdf", "finished_at": "2026-10-03T10:00:00Z", "errors": {"error": "boom"}}], "total": 1},
    )  # fmt: skip
    res = await run("job_failure", owner, job=job_id)
    assert not res.ok
    assert all(c[1] == "/api/v1/ingest/jobs" for c in owner.client.calls)


async def test_job_failure_when_nothing_failed(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/ingest/jobs", {"items": [], "total": 0})
    res = await run("job_failure", owner)
    assert "No upload has failed" in res.markdown


async def test_an_unknown_error_gets_the_generic_advice(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/ingest/jobs",
        {"items": [{"id": "j", "file_name": "x.pdf", "finished_at": "2026-10-03T00:00:00Z", "errors": {"error": "weird"}}], "total": 1},
    )  # fmt: skip
    res = await run("job_failure", owner)
    assert "Something went wrong" in res.markdown and "weird" in res.markdown


# -------------------------------------------------------------------------- keys


async def test_api_keys_never_show_secrets(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/tenants/me/keys",
        {"keys": [{"name": "Zapier", "permission": "read", "key_prefix": "tk_ab12", "last_used_at": None, "key_hash": "$2b$12$abc", "created_by": "ravi@shree.example"}]},
    )  # fmt: skip
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A))
    owner.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/keys",
        {"keys": [{"name": "Site", "permission": "write", "key_prefix": "ck_zz99", "last_used_at": "2026-10-02T00:00:00Z", "created_by": "admin key"}]},
    )  # fmt: skip
    res = await run("api_keys", owner)
    assert "Zapier" in res.markdown and "Site" in res.markdown
    for secret in ("$2b$", "tk_ab12", "ck_zz99", "ravi@shree.example", "@"):
        assert secret not in res.markdown
    assert "a team member" in res.markdown and "admin key" in res.markdown
    assert "Never" in res.markdown and "2 Oct 2026" in res.markdown


async def test_a_named_knowledge_base_links_to_its_key_tab() -> None:
    ctx = FakeToolContext(CobRole.OWNER)
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    ctx.client.reply("GET", "/api/v1/corpora/{corpus_id}/keys", {"keys": []})
    res = await run("api_keys", ctx, kb="Store")
    assert res.link is not None and res.link.route == f"/kbs/{KB_C['id']}?tab=key"
    assert not any(c[1] == "/api/v1/tenants/me/keys" for c in ctx.client.calls)


async def test_owner_only_route_refused_politely(member: FakeToolContext) -> None:
    member.client.reply(
        "GET", "/api/v1/tenants/me/keys", {"detail": "Only an owner can do that.", "code": "owner_required"}, status=403
    )  # fmt: skip
    res = await run("api_keys", member)
    assert res.markdown == "Only an owner can see that." and res.link is None


async def test_widget_keys_card(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A))
    owner.client.reply(
        "GET",
        "/api/v1/corpora/{corpus_id}/widget-keys",
        [{"name": "Main site", "public_key": "pk_secretish", "embed_snippet": "<script>", "allowed_origins": ["https://a.com", "https://b.com", "https://c.com"], "rate_limit_per_minute": 20, "daily_message_limit": 500, "is_active": True}],
    )  # fmt: skip
    res = await run("widget_keys", owner)
    assert "Main site" in res.markdown and "https://a.com, https://b.com +1" in res.markdown
    assert "20/min, 500/day" in res.markdown
    assert "pk_secretish" not in res.markdown and "<script>" not in res.markdown
    assert res.link is not None and res.link.route == "/widget"


# --------------------------------------------------------------- assistant_status


def _assistant(enabled: bool, *, published: bool = True, draft: bool = False) -> dict[str, Any]:
    config = {
        "language": {"supported": ["en", "hi"]},
        "leads": {"enabled": True},
        "handoff": {"enabled": False},
    }
    version = {"version": 4, "published_at": "2026-10-01T00:00:00Z", "config": config}
    return {
        "enabled": enabled,
        "published": version if published else None,
        "draft": {"config": config} if draft else None,
    }


async def test_assistant_status_for_one_knowledge_base() -> None:
    ctx = FakeToolContext(CobRole.OWNER, question="is my clinic assistant on?")
    ctx.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    ctx.client.reply("GET", "/api/v1/corpora/{corpus_id}/assistant", _assistant(True, draft=True))
    res = await run("assistant_status", ctx)
    assert "assistant of **Clinic** is on" in res.markdown
    assert "v4 on 1 Oct 2026" in res.markdown and "en, hi" in res.markdown
    assert "**Leads:** On" in res.markdown and "**Handoff:** Off" in res.markdown
    assert res.link is not None and res.link.route == f"/kbs/{KB_A['id']}?tab=assistant"


async def test_assistant_status_across_knowledge_bases(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/corpora", corpora(KB_A, KB_C))
    owner.client.reply(
        "GET", "/api/v1/corpora/{corpus_id}/assistant", _assistant(False, published=False)
    )
    res = await run("assistant_status", owner)
    assert "on for **0** of **2**" in res.markdown and "None" in res.markdown


# --------------------------------------------------------------- leads_handoffs


async def test_leads_card_has_counts_only(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/leads",
        {"items": [{"contact": {"name": "Priya", "phone": "+919812345678", "email": "p@x.in"}, "name": "Priya", "phone": "+919812345678", "status": "new", "created_at": "2026-10-02T00:00:00Z"}], "total": 1},
    )  # fmt: skip
    owner.client.reply("GET", "/api/v1/handoffs", {"items": [], "total": 0})
    res = await run("leads_handoffs", owner)
    for private in ("+91", "Priya", "p@x.in"):
        assert private not in res.markdown
    owner.client.reply("GET", "/api/v1/leads", {"items": [], "total": 1}, when={"status": "new"})
    res = await run("leads_handoffs", owner)
    assert (
        "**1** leads" in res.markdown and "new 1" in res.markdown and "2 Oct 2026" in res.markdown
    )
    assert res.link is not None and res.link.route == "/leads"
    month_call = next(
        c for c in owner.client.calls if c[1] == "/api/v1/leads" and c[2].get("date_from")
    )
    assert month_call[2]["date_from"] == "2026-10-01" and month_call[2]["limit"] == 1


async def test_leads_status_line_adds_up_to_the_monthly_total(owner: FakeToolContext) -> None:
    owner.client.reply("GET", "/api/v1/leads", {"items": [], "total": 250})
    for status, n in (("new", 100), ("contacted", 80), ("won", 50), ("lost", 20), ("spam", 0)):
        owner.client.reply(
            "GET", "/api/v1/leads", {"items": [], "total": n}, when={"status": status}
        )
    owner.client.reply("GET", "/api/v1/handoffs", {"items": [], "total": 0})
    res = await run("leads_handoffs", owner)
    assert "**250** leads" in res.markdown
    assert "contacted 80, lost 20, new 100, won 50" in res.markdown


# --------------------------------------------------------------------- my_costs


async def test_my_costs_by_source_with_cob(owner: FakeToolContext) -> None:
    owner.client.reply(
        "GET",
        "/api/v1/usage",
        {
            "rows": [
                {"key": "cob", "label": "cob", "requests": 7, "prompt_tokens": 1000, "completion_tokens": 234, "cost_usd": 0.02, "cost_known": True},
                {"key": "widget:abc", "label": "Main", "requests": 40, "prompt_tokens": 20000, "completion_tokens": 5000, "cost_usd": 0.5, "cost_known": True},
            ],
            "totals": {"requests": 47, "cost_usd": 0.52, "cost_known": True},
            "usd_inr_rate": 84.0,
        },
    )  # fmt: skip
    res = await run("my_costs", owner)
    assert "| COB | 7 | 1,234 |" in res.markdown
    assert "Website widget" in res.markdown and "₹44 ($0.52)" in res.markdown
    assert res.link is not None and res.link.route == "/usage"


# ----------------------------------------------------------------- operator tools


async def test_engine_health_names_what_is_down(admin: FakeToolContext) -> None:
    admin.client.reply(
        "GET",
        "/api/v1/health/ready",
        {"status": "not_ready", "services": {"postgres": True, "redis": True, "qdrant": False}},
        status=503,
    )
    admin.client.reply(
        "GET",
        "/api/v1/info",
        {"version": "1.4.0", "llm": {"model": "gpt-x", "fast_model": "mini"}, "embedding": {"model": "bge"}, "features": {"accounts": True, "web_search": {"enabled": False}, "cob": {"enabled": True}}},
    )  # fmt: skip
    res = await run("engine_health", admin)
    assert "Something is down: qdrant" in res.markdown and "| qdrant | Down |" in res.markdown
    assert "1.4.0" in res.markdown and "accounts, cob" in res.markdown
    assert res.link is not None and res.link.route == "/admin/engine"


async def test_customers_card_has_no_owner_emails(admin: FakeToolContext) -> None:
    admin.client.reply(
        "GET",
        "/api/v1/admin/tenants",
        {
            "items": [
                {"name": "Operator", "status": "active", "is_operator": True, "plan_name": "Internal", "owner_emails": ["op@x.in"]},
                {"name": "Shree Labs", "status": "active", "plan_name": "Pilot", "questions_this_month": 212, "owner_emails": ["a@shree.example"], "created_at": "2026-09-01T00:00:00Z"},
                {"name": "Anand Foods", "status": "pending", "plan_name": "Standard", "questions_this_month": 0, "owner_emails": ["b@anand.example"], "created_at": "2026-10-03T00:00:00Z"},
            ],
            "total": 3,
        },
    )  # fmt: skip
    res = await run("customers", admin)
    assert (
        "**2** customer workspaces" in res.markdown
        and "**1** waiting for approval: **Anand Foods**" in res.markdown
    )
    assert "Operator" not in res.markdown and "@" not in res.markdown
    assert res.link is not None and res.link.route == "/admin/customers?status=pending"
    assert res.markdown.index("Anand Foods") < res.markdown.index("| Shree Labs")


async def test_platform_costs_card(admin: FakeToolContext) -> None:
    admin.client.reply(
        "GET",
        "/api/v1/admin/costs",
        {
            "today": {"cost_usd": 1.0, "cost_known": True},
            "month": {"cost_usd": 20.0, "cost_known": True},
            "by_tenant": [{"label": "Shree Labs", "cost_usd": 12.0, "cost_known": True}, {"label": "Anand Foods", "cost_usd": 8.0, "cost_known": True}],
            "by_model": [{"key": "gpt-x", "label": None, "cost_usd": 20.0, "cost_known": True}],
            "usd_inr_rate": 80.0,
        },
    )  # fmt: skip
    res = await run("platform_costs", admin)
    assert "₹80 ($1.00) today" in res.markdown and "₹1,600 ($20.00) this month" in res.markdown
    assert "| Shree Labs | ₹960 ($12.00) |" in res.markdown and "gpt-x" in res.markdown
    assert res.link is not None and res.link.route == "/admin/costs"
