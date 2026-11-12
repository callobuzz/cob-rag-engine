# cob-rag-client (JavaScript / TypeScript)

Zero-dependency client for [cob-rag-engine](../../README.md). Works in Node 18+, Deno and Bun
(any runtime with `fetch`). Types included.

```bash
npm install ./clients/js     # from a checkout of the engine
```

```js
import { CobRag } from "cob-rag-client";

const admin = new CobRag({ baseUrl: "http://127.0.0.1:8000", apiKey: process.env.RAG_ADMIN_API_KEY });
const tenant = await admin.createTenant("Acme Inc");                // tk_ key returned once
// Every corpus belongs to a tenant: the admin key names it (a tk_ key creates for itself).
const corpus = await admin.createCorpus("Acme help center", { tenant_id: tenant.id });  // ck_ key, once

const rag = new CobRag({ baseUrl: "http://127.0.0.1:8000", apiKey: corpus.api_key });
await rag.ingest(new Blob([await readFile("returns.md")]), { wait: true });

const { answer, citations } = await rag.query("How long do I have to return an item?");

for await (const token of rag.queryStream("Summarise the returns policy")) process.stdout.write(token);

// Or every event: sources (citations + query_id) first, then tokens, then done.
for await (const { event, data } of rag.queryStreamEvents("Summarise the returns policy")) {
  if (event === "sources") console.log(data.sources);
}

// With the knowledge base's assistant on (the default) the stream also has `meta` (label,
// route, language), `quick_replies` and `ui`, and `done` adds the label, route and the
// confidence threshold. `assistant` takes the conversation options (a preview needs a key
// with write permission):
for await (const { event, data } of rag.queryStreamEvents("hi", { assistant: { conversationId: chatId } })) {
  if (event === "meta") console.log(data.route, data.language);
}

const widget = await rag.createWidgetKey(corpus.id, "Acme site", ["https://acme.com"]);
console.log(widget.embed_snippet);   // paste into the website

console.log(await admin.usage({ group_by: "corpus" }));   // metered usage for billing

console.log((await admin.info()).features);   // what this engine offers
const docs = await admin.listDocuments({ corpus_id: corpus.id, search: "price" });
const jobs = await admin.listJobs({ corpus_id: corpus.id, status: "pending_review" });
```

## Operators: plans, tenants and settings

What the operator runs with the admin key. People use the dashboard to sign in; this client
stays key-based (it has no sign-in methods).

```js
// Plans: four optional limits each (null = unlimited). Also getPlan, updatePlan, deletePlan.
const starter = await admin.createPlan("Starter", { max_knowledge_bases: 1, monthly_questions: 1000 });
console.log((await admin.listPlans()).map((p) => [p.name, p.tenant_count]));

// Customers who signed up, and their lifecycle. Suspend and delete need the name typed exactly.
for (const t of await admin.listTenants({ status: "pending" })) {
  await admin.activateTenant(t.id, { planId: starter.id, note: "paid via UPI" });
}
await admin.setTenantPlan(tenant.id, starter.id);
console.log((await admin.tenantPlan(tenant.id)).usage);   // usage against the plan this month
await admin.suspendTenant(tenant.id, "Acme Inc", "unpaid");
await admin.reactivateTenant(tenant.id);

// A customer's team: invite its first owner yourself when there is no SMTP (send them the link).
const { accept_url } = await admin.inviteToTenant(tenant.id, "owner@acme.example");
const team = await admin.tenantTeam(tenant.id);   // also setTenantMemberRole, removeTenantMember, revokeTenantInvitation

// Sign-up mode and white-label branding; e-mail status; the audit trail.
await admin.updateSettings({ signup_mode: "approval", branding: { product_name: "Sahayak AI" } });
console.log((await admin.smtpStatus()).configured);
for (const row of (await admin.auditLog({ action: "tenant.", limit: 20 })).items) {
  console.log(row.created_at, row.action, row.actor_label, row.tenant_label);
}

await admin.deleteTenant(tenant.id, "Acme Inc");   // the tenant and everything it owns

// A tenant key sees its own plan and team (team, invite, setMemberRole, removeMember, ...).
console.log((await new CobRag({ apiKey: "tk_..." }).myPlan()).usage);   // questions this month
```

## Leads

Contact details a website visitor chose to share with the assistant (they are encrypted at rest and
need `RAG_ENCRYPTION_KEY` on the engine). Reading, updating and exporting leads needs a `write` key,
because contact data is more than a read-only agent key may see; deleting a lead and managing a
knowledge base's webhooks need `full`.

```js
const rag = new CobRag({ apiKey: "tk_..." });
const page = await rag.listLeads({ corpusId: corpus.id, status: "new", limit: 25 });   // q: "9876543210" finds one exactly
const lead = await rag.getLead(page.items[0].id);   // summary, notes, consent record, chat transcript
await rag.updateLead(lead.id, { status: "contacted", notes: "Rang twice" });
const csv = await rag.exportLeads({ corpusId: corpus.id, status: "new" });   // owners only; audited
await rag.deleteLead(lead.id);   // owners only; also removes the website chat it came from
// Send new leads to your own system (https, public hosts only; events: lead.created, handoff.requested)
const hook = await rag.createCorpusWebhook(corpus.id, { url: "https://crm.example.in/hooks/leads", events: ["lead.created"], secret: "a-long-shared-secret" });
await rag.updateCorpusWebhook(corpus.id, hook.id, { enabled: false });
```

Handoffs (a visitor offered a way to reach a person): the list needs a `read` key, one handoff (it
carries a call-back's phone and e-mail) and the status change need `write`.

```js
const waiting = await rag.listHandoffs({ corpusId: corpus.id, status: "new" });
const h = await rag.getHandoff(waiting.items[0].id);   // summary, masked chat, the lead of a call-back
await rag.updateHandoff(h.id, "handled");
```

## Named keys, permissions and deleting

A knowledge base and a customer each have any number of named keys, each with a permission:
`read` (search, ask, list, get), `write` (also add and change content) or `full` (also delete and
manage keys). Give each agent or integration its own, and revoke one without touching the rest.

```js
const rag = new CobRag({ apiKey: "tk_..." });   // a full tenant key, or the admin key
const made = await rag.createCorpusKey(corpus.id, "Agent - Claude Code");   // permission defaults to "read"
console.log(made.api_key);                       // shown once; only its prefix is kept
await rag.updateCorpusKey(corpus.id, made.id, { permission: "write" });
for (const k of await rag.listCorpusKeys(corpus.id)) console.log(k.name, k.permission, k.last_used_at);
await rag.rotateCorpusNamedKey(corpus.id, made.id);   // a new secret; the old one stops now
await rag.revokeCorpusKey(corpus.id, made.id);
// The same for a customer: listMyKeys / createMyKey / ... and, with the admin key,
// listTenantKeys(tenantId) / createTenantKey(tenantId, name, permission) / ...
```

A call a key's permission does not allow rejects with `CobRagError` whose `.code` is
`"key_permission_denied"`.

Deleting a knowledge base, a document or a customer **with a key** needs the item's exact name; the
preview methods say what would go and the name to send:

```js
console.log(await rag.corpusDeletePreview(corpus.id));   // { counts: { documents: 7, ... }, confirm_name: "Acme help center" }
await rag.deleteCorpus(corpus.id, "Acme help center");
await rag.deleteDocument(docId, "returns-policy.pdf", { corpus_id: corpus.id });
// Without the name, or with another, `.code` is "confirmation_required" / "confirm_mismatch".
```

Every knowledge base has an **assistant** (identity, voice, language, scope, guidance rules).
Read it, patch the draft, publish. Reads need a `read` key; draft edits, publish and restore need
`write`; publishing is reversible with `restoreAssistantVersion`:

```js
const state = await rag.getAssistant(corpus.id);   // { enabled: true, published: {...}, draft: null }
const draft = await rag.patchAssistantDraft(corpus.id, {
  identity: { business_name: "Shree Labs" },
  voice: { tone_preset: "warm" },
});
await rag.publishAssistant(corpus.id, { baseRevision: draft.draft_revision, note: "Warmer tone" });
console.log((await rag.compileAssistant(corpus.id, { sample: { language: "hi-Latn" } })).system_prompt);
```

Test sets and evaluation (reads need `read`; creating or changing sets, criteria, runs and ratings
need `write`; deleting a set needs `full`). A full run spends model money, so it needs the estimate:

```js
const est = await rag.estimateEvalRun(kb, setId); // { total_usd: 0.11, unpriced_models: [], ... }
const run = await rag.startEvalRun(kb, setId, { mode: "full", name: "baseline", confirmCostUsd: est.total_usd });
const detail = await rag.getEvalRun(kb, run.id); // poll until detail.status === "completed"
const failed = await rag.listEvalSamples(kb, run.id, { failedOnly: true });
```

`ck_`, `tk_` and admin keys are **secrets** — use this client server-side. To put a chatbot in a
browser, use the embeddable widget and its publishable `pk_` key.

Errors throw `CobRagError` with `.status`, `.detail` and `.code` (the engine's machine-readable code,
or `undefined`; a tenant past its plan's monthly
questions gets status 429 on `query`, `queryStream` and conversation messages).
