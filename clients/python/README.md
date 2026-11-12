# cob-rag-client (Python)

Python client for [cob-rag-engine](../../README.md). Sync and async, streaming, typed.

```bash
pip install ./clients/python        # from a checkout of the engine
```

```python
from cob_rag_client import CobRag

admin = CobRag("http://127.0.0.1:8000", api_key="<RAG_ADMIN_API_KEY>")
tenant = admin.create_tenant("Acme Inc")  # returns a tk_ key once
# Every corpus belongs to a tenant: the admin key names it (a tk_ key creates for itself).
corpus = admin.create_corpus("Acme help center", tenant_id=tenant["id"])  # a ck_ key, once

rag = CobRag("http://127.0.0.1:8000", api_key=corpus["api_key"])
rag.ingest("returns-policy.pdf", wait=True)  # upload + wait for indexing

answer = rag.query("How long do I have to return an item?")
print(answer["answer"], answer["citations"])

for token in rag.query_stream("Summarise the returns policy"):
    print(token, end="", flush=True)

# Or every event: sources (citations + query_id) first, then tokens, then done.
for event in rag.query_stream_events("Summarise the returns policy"):
    if event["event"] == "sources":
        print(event["data"]["sources"])

# With the knowledge base's assistant on (the default) the stream also has `meta` (label,
# route, language), `quick_replies` and `ui`, and `done` adds the label, route and the
# confidence threshold. `assistant` takes the conversation options; a preview needs a key
# with write permission:
for event in rag.query_stream_events("hi", assistant={"conversation_id": chat_id}):
    if event["event"] == "meta":
        print(event["data"]["route"], event["data"]["language"])

# Per-request model override (bring your own key):
rag.query("…", llm={"provider": "anthropic", "model": "claude-sonnet-5", "api_key": "sk-ant-…"})

# Give the corpus a website chat widget:
key = rag.create_widget_key(corpus["id"], "Acme site", ["https://acme.com"])
print(key["embed_snippet"])

# Metered usage for billing:
print(admin.usage(group_by="corpus", start="2026-09-01"))

# What the engine offers, and a corpus's documents and jobs (admin/tenant keys name the corpus):
print(admin.info()["features"])
docs = admin.list_documents(corpus_id=corpus["id"], search="price")
print(admin.get_document(docs["items"][0]["id"], corpus_id=corpus["id"])["chunks"][:2])
print(admin.list_jobs(corpus_id=corpus["id"], status="pending_review")["total"])
```

## Operators: plans, tenants and settings

What the operator runs with the admin key. People use the dashboard to sign in; this client
stays key-based (it has no sign-in methods).

```python
admin = CobRag("http://127.0.0.1:8000", api_key="<RAG_ADMIN_API_KEY>")

# Plans: four optional limits each (None = unlimited). Also get_plan, update_plan, delete_plan.
starter = admin.create_plan("Starter", max_knowledge_bases=1, monthly_questions=1000)
print([(p["name"], p["tenant_count"]) for p in admin.list_plans()])

# Customers who signed up, and their lifecycle. Suspend and delete need the tenant's name typed
# exactly; delete removes the tenant and everything it owns.
for t in admin.list_tenants(status="pending"):
    admin.activate_tenant(t["id"], plan_id=starter["id"], note="paid via UPI")
admin.set_tenant_plan(tenant["id"], starter["id"])
print(admin.tenant_plan(tenant["id"])["usage"])  # usage against the plan this month
admin.suspend_tenant(tenant["id"], confirm_name="Acme Inc", reason="unpaid")
admin.reactivate_tenant(tenant["id"])

# A customer's team: invite its first owner yourself when there is no SMTP (send them the link).
link = admin.invite_to_tenant(tenant["id"], "owner@acme.example")["accept_url"]
team = admin.tenant_team(tenant["id"])  # also set_tenant_member_role, remove_tenant_member,
# revoke_tenant_invitation

# Sign-up mode and white-label branding; e-mail status; the audit trail.
admin.update_settings(signup_mode="approval", branding={"product_name": "Sahayak AI"})
print(admin.smtp_status()["configured"])
for row in admin.audit_log(action="tenant.", limit=20)["items"]:
    print(row["created_at"], row["action"], row["actor_label"], row["tenant_label"])

admin.delete_tenant(tenant["id"], confirm_name="Acme Inc")

# A tenant key sees its own plan and team (team, invite, set_member_role, remove_member, ...).
print(CobRag("http://127.0.0.1:8000", api_key="tk_...").my_plan()["usage"])  # questions this month
```

## Leads

Contact details a website visitor chose to share with the assistant (they are encrypted at rest and
need `RAG_ENCRYPTION_KEY` on the engine). Reading, updating and exporting leads needs a `write` key,
because contact data is more than a read-only agent key may see; deleting a lead and managing a
knowledge base's webhooks need `full`.

```python
rag = CobRag("http://127.0.0.1:8000", api_key="tk_...")
page = rag.list_leads(corpus_id=corpus["id"], status="new", limit=25)  # q="9876543210" finds one exactly
lead = rag.get_lead(page["items"][0]["id"])  # summary, notes, consent record, chat transcript
rag.update_lead(lead["id"], status="contacted", notes="Rang twice")
csv_text = rag.export_leads(corpus_id=corpus["id"], status="new")  # owners only; audited
rag.delete_lead(lead["id"])  # owners only; also removes the website chat it came from
# Send new leads to your own system (https, public hosts only; events: lead.created, handoff.requested)
hook = rag.create_corpus_webhook(corpus["id"], "https://crm.example.in/hooks/leads", ["lead.created"], "a-long-shared-secret")
rag.update_corpus_webhook(corpus["id"], hook["id"], enabled=False)
```

Handoffs (a visitor offered a way to reach a person): the list needs a `read` key, one handoff (it
carries a call-back's phone and e-mail) and the status change need `write`.

```python
waiting = rag.list_handoffs(corpus_id=corpus["id"], status="new")
h = rag.get_handoff(waiting["items"][0]["id"])  # summary, masked chat, the lead of a call-back
rag.update_handoff(h["id"], "handled")
```

## Named keys, permissions and deleting

A knowledge base and a customer each have any number of named keys, each with a permission:
`read` (search, ask, list, get), `write` (also add and change content) or `full` (also delete and
manage keys). Give each agent or integration its own, and revoke one without touching the rest.

```python
rag = CobRag("http://127.0.0.1:8000", api_key="tk_...")  # a full tenant key, or the admin key
made = rag.create_corpus_key(corpus["id"], "Agent - Claude Code")  # permission defaults to "read"
print(made["api_key"])  # shown once; only its prefix is kept
rag.update_corpus_key(corpus["id"], made["id"], permission="write")
for key in rag.list_corpus_keys(corpus["id"]):  # name, key_prefix, permission, last_used_at
    print(key["name"], key["permission"], key["last_used_at"])
rag.rotate_corpus_named_key(corpus["id"], made["id"])  # a new secret; the old one stops now
rag.revoke_corpus_key(corpus["id"], made["id"])
# The same for a customer: list_my_keys / create_my_key / ... and, with the admin key,
# list_tenant_keys(tenant_id) / create_tenant_key(tenant_id, name, permission) / ...
```

A call a key's permission does not allow raises `CobRagError` with `.code == "key_permission_denied"`.

Deleting a knowledge base, a document or a customer **with a key** needs the item's exact name; the
preview methods say what would go and the name to send:

```python
print(
    rag.corpus_delete_preview(corpus["id"])
)  # {"counts": {"documents": 7, ...}, "confirm_name": "Acme help center"}
rag.delete_corpus(corpus["id"], confirm_name="Acme help center")
rag.delete_document(doc_id, confirm_name="returns-policy.pdf", corpus_id=corpus["id"])
# Without confirm_name, or with another name, CobRagError.code is "confirmation_required" / "confirm_mismatch".
```

Every knowledge base has an **assistant** (identity, voice, language, scope, guidance rules).
Read it, patch the draft, publish. Reads need a `read` key; draft edits, publish and restore need
`write`; publishing is reversible with `restore_assistant_version`:

```python
state = rag.get_assistant(corpus["id"])  # {"enabled": True, "published": {...}, "draft": None}
draft = rag.patch_assistant_draft(
    corpus["id"],
    {"identity": {"business_name": "Shree Labs"}, "voice": {"tone_preset": "warm"}},
)
rag.publish_assistant(corpus["id"], base_revision=draft["draft_revision"], note="Warmer tone")
print(rag.compile_assistant(corpus["id"], sample={"language": "hi-Latn"})["system_prompt"])
```

Test sets and evaluation (reads need `read`; creating or changing sets, criteria, runs and ratings
need `write`; deleting a set needs `full`). A full run spends model money, so it needs the estimate:

```python
est = rag.estimate_eval_run(kb, set_id)  # {"total_usd": 0.11, "unpriced_models": [], ...}
run = rag.start_eval_run(kb, set_id, mode="full", name="baseline", confirm_cost_usd=est["total_usd"])
detail = rag.get_eval_run(kb, run["id"])  # poll until detail["status"] == "completed"
failed = rag.list_eval_samples(kb, run["id"], failed_only=True)  # answers, scores, the judge's reasons
```

Async: `AsyncCobRag` has the same core methods (`await rag.query(...)`, `async for t in rag.query_stream(...)`,
`async for e in rag.query_stream_events(...)`).

Errors raise `CobRagError` with `.status`, `.detail` and `.code` (the engine's machine-readable code,
or `None`; a tenant past its plan's monthly
questions gets status 429 on `query`, `query_stream` and conversation messages).
