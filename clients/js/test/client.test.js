import assert from "node:assert/strict";
import { test } from "node:test";

import { CobRag, CobRagError } from "../index.js";

// Every request any test sent, for the strictness check at the end of the file.
const allCalls = [];

function mockFetch(routes) {
  const calls = [];
  const fn = async (url, init) => {
    const u = new URL(url);
    calls.push({ url: u, init });
    allCalls.push({ url: u, init });
    const handler = routes[`${init.method} ${u.pathname}`];
    if (!handler) return new Response(JSON.stringify({ detail: "Not found" }), { status: 404 });
    return handler(u, init, calls);
  };
  fn.calls = calls;
  return fn;
}

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

test("query sends key, body and LLM override headers", async () => {
  const fetch = mockFetch({
    "POST /api/v1/query": (_u, init) => json({ answer: "echo:" + JSON.parse(init.body).query, citations: [] }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "ck_t", fetch });
  const res = await rag.query("refunds?", { corpusId: "c1", llm: { provider: "openai", model: "gpt-4o-mini" } });
  assert.equal(res.answer, "echo:refunds?");
  const { init } = fetch.calls[0];
  assert.equal(init.headers["X-API-Key"], "ck_t");
  assert.equal(init.headers["X-LLM-Provider"], "openai");
  assert.deepEqual(JSON.parse(init.body), { query: "refunds?", corpus_id: "c1" });
});

const SOURCES = { query_id: "q1", sources: [{ index: 1, file_name: "returns.pdf" }] };
const streamOf = (chunks) => () =>
  new Response(new ReadableStream({
    start(c) { for (const s of chunks) c.enqueue(new TextEncoder().encode(s)); c.close(); },
  }));
// Split mid-line and mid-event on purpose: the parser must reassemble both.
const EVENT_CHUNKS = [
  `event: sources\ndata: ${JSON.stringify(SOURCES)}\n\nevent: tok`,
  'en\ndata: {"content": "Hel"}\n\nevent: token\nda',
  'ta: {"content": "lo"}\n\n',
  'event: done\ndata: {"query_id": "q1", "model": "m", "usage": {"total_tokens": 3}}\n\n',
];

test("queryStream yields tokens across chunk boundaries until done", async () => {
  const fetch = mockFetch({ "POST /api/v1/query/stream": streamOf(EVENT_CHUNKS) });
  const rag = new CobRag({ fetch });
  const out = [];
  for await (const t of rag.queryStream("hi")) out.push(t);
  assert.deepEqual(out, ["Hel", "lo"]);
});

test("queryStreamEvents yields sources first and done last", async () => {
  const fetch = mockFetch({ "POST /api/v1/query/stream": streamOf(EVENT_CHUNKS) });
  const rag = new CobRag({ fetch });
  const events = [];
  for await (const e of rag.queryStreamEvents("hi")) events.push(e);
  assert.deepEqual(events.map((e) => e.event), ["sources", "token", "token", "done"]);
  assert.deepEqual(events[0].data, SOURCES);
  assert.equal(events.at(-1).data.query_id, "q1");
});

test("queryStream skips stage events and events it does not know", async () => {
  const fetch = mockFetch({
    "POST /api/v1/query/stream": streamOf([
      'event: stage\ndata: {"stage": "understand", "elapsed_ms": 0}\n\n',
      'event: stage\ndata: {"stage": "search", "elapsed_ms": 40}\n\n',
      `event: sources\ndata: ${JSON.stringify(SOURCES)}\n\n`,
      "event: future_thing\ndata: {}\n\n",
      'event: token\ndata: {"content": "Hi"}\n\n',
      'event: done\ndata: {"query_id": "q1", "timings": {"write": 5}}\n\n',
    ]),
  });
  const rag = new CobRag({ fetch });
  const out = [];
  for await (const t of rag.queryStream("hi")) out.push(t);
  assert.deepEqual(out, ["Hi"]);
  const events = [];
  for await (const e of rag.queryStreamEvents("hi")) events.push(e.event);
  assert.deepEqual(events, ["stage", "stage", "sources", "future_thing", "token", "done"]);
});

test("queryStream throws on an error event", async () => {
  const fetch = mockFetch({
    "POST /api/v1/query/stream": streamOf([
      'event: token\ndata: {"content": "Hel"}\n\n',
      'event: error\ndata: {"detail": "Generation failed part-way"}\n\n',
    ]),
  });
  const rag = new CobRag({ fetch });
  const out = [];
  await assert.rejects(
    (async () => { for await (const t of rag.queryStream("hi")) out.push(t); })(),
    (e) => e instanceof CobRagError && e.detail === "Generation failed part-way",
  );
  assert.deepEqual(out, ["Hel"]);
});

test("ingest uploads multipart and waits for completion", async () => {
  let polls = 0;
  const fetch = mockFetch({
    "POST /api/v1/ingest": (_u, init) => {
      assert.ok(init.body instanceof FormData);
      assert.equal(init.body.get("corpus_id"), "c1");
      assert.equal(init.body.get("file").name, "returns.md");
      return json({ job_id: "j1", status: "queued" }, 202);
    },
    "GET /api/v1/ingest/jobs/j1": () => json({ status: ++polls >= 2 ? "completed" : "processing" }),
  });
  const rag = new CobRag({ fetch });
  const job = await rag.ingest({ name: "returns.md", data: "30 days" }, { corpusId: "c1", wait: true, pollMs: 1 });
  assert.equal(job.status, "completed");
});

test("deletes with a key send confirm_name; refusals carry their code", async () => {
  const fetch = mockFetch({
    "DELETE /api/v1/corpora/c1": (u) =>
      u.searchParams.get("confirm_name") === "Support FAQ"
        ? new Response(null, { status: 204 })
        : json({ detail: "Needs confirm_name", code: "confirmation_required", confirm_name: "Support FAQ" }, 422),
    "DELETE /api/v1/documents/d1": (u) =>
      u.searchParams.get("confirm_name") === "refund-policy.pdf"
        ? new Response(null, { status: 204 })
        : json({ detail: "does not match", code: "confirm_mismatch" }, 422),
    "GET /api/v1/corpora/c1/delete-preview": () => json({ kind: "knowledge_base", counts: { documents: 7 } }),
    "GET /api/v1/documents/d1/delete-preview": () => json({ kind: "document", counts: { chunks: 12 } }),
    "GET /api/v1/admin/tenants/t1/delete-preview": () => json({ kind: "customer", counts: { api_keys: 2 } }),
  });
  const rag = new CobRag({ fetch });
  await assert.rejects(rag.deleteCorpus("c1"), (e) => e.status === 422 && e.code === "confirmation_required");
  assert.equal(await rag.deleteCorpus("c1", "Support FAQ"), null);
  assert.equal(fetch.calls.at(-1).url.searchParams.get("confirm_name"), "Support FAQ");
  await assert.rejects(rag.deleteDocument("d1", "refund", { corpus_id: "c1" }), (e) => e.code === "confirm_mismatch");
  assert.equal(await rag.deleteDocument("d1", "refund-policy.pdf", { corpus_id: "c1" }), null);
  assert.deepEqual(Object.fromEntries(fetch.calls.at(-1).url.searchParams), {
    corpus_id: "c1",
    confirm_name: "refund-policy.pdf",
  });
  assert.equal((await rag.corpusDeletePreview("c1")).counts.documents, 7);
  assert.equal((await rag.documentDeletePreview("d1", { corpus_id: "c1" })).counts.chunks, 12);
  assert.equal((await rag.tenantDeletePreview("t1")).kind, "customer");
});

test("a permission refusal is a CobRagError with its code; an error without one has none", async () => {
  const fetch = mockFetch({
    "POST /api/v1/ingest/records": () =>
      json({ detail: "This API key has read permission; this needs write", code: "key_permission_denied", required: "write" }, 403),
    "GET /api/v1/corpora/missing": () => json({ detail: "Corpus not found" }, 404),
  });
  const rag = new CobRag({ apiKey: "ck_read", fetch });
  await assert.rejects(rag.ingestRecords([{ id: "a", text: "x" }]), (e) => e.status === 403 && e.code === "key_permission_denied");
  await assert.rejects(rag.getCorpus("missing"), (e) => e.status === 404 && e.code === undefined);
});

test("named key calls", async () => {
  const hit = (body, status = 200) => (_u, init) => json(typeof body === "function" ? body(init) : body, status);
  const fetch = mockFetch({
    "GET /api/v1/corpora/c1/keys": hit({ keys: [{ id: "k1", permission: "read" }] }),
    "POST /api/v1/corpora/c1/keys": hit((init) => ({ id: "k2", api_key: "ck_abcd_new", ...JSON.parse(init.body) }), 201),
    "PATCH /api/v1/corpora/c1/keys/k1": hit((init) => JSON.parse(init.body)),
    "POST /api/v1/corpora/c1/keys/k1/rotate": hit({ id: "k1", api_key: "ck_abcd_rot" }),
    "DELETE /api/v1/corpora/c1/keys/k1": () => new Response(null, { status: 204 }),
    "GET /api/v1/tenants/me/keys": hit({ keys: [{ id: "k1" }] }),
    "POST /api/v1/tenants/me/keys": hit((init) => ({ id: "k3", ...JSON.parse(init.body) }), 201),
    "PATCH /api/v1/tenants/me/keys/k1": hit((init) => JSON.parse(init.body)),
    "POST /api/v1/tenants/me/keys/k1/rotate": hit({ id: "k1", api_key: "tk_new" }),
    "DELETE /api/v1/tenants/me/keys/k1": () => new Response(null, { status: 204 }),
    "GET /api/v1/admin/tenants/t1/keys": hit({ keys: [{ id: "k1" }] }),
    "POST /api/v1/admin/tenants/t1/keys": hit((init) => ({ id: "k4", ...JSON.parse(init.body) }), 201),
    "PATCH /api/v1/admin/tenants/t1/keys/k1": hit((init) => JSON.parse(init.body)),
    "POST /api/v1/admin/tenants/t1/keys/k1/rotate": hit({ id: "k1", api_key: "tk_rot" }),
    "DELETE /api/v1/admin/tenants/t1/keys/k1": () => new Response(null, { status: 204 }),
  });
  const rag = new CobRag({ fetch });
  assert.deepEqual(await rag.listCorpusKeys("c1"), [{ id: "k1", permission: "read" }]);
  const made = await rag.createCorpusKey("c1", "Agent - Cursor");
  assert.equal(made.api_key, "ck_abcd_new");
  assert.deepEqual(JSON.parse(fetch.calls.at(-1).init.body), { name: "Agent - Cursor", permission: "read" });
  assert.equal((await rag.createCorpusKey("c1", "Writer", "write")).permission, "write");
  assert.deepEqual(await rag.updateCorpusKey("c1", "k1", { permission: "full" }), { permission: "full" });
  assert.equal((await rag.rotateCorpusNamedKey("c1", "k1")).api_key, "ck_abcd_rot");
  assert.equal(await rag.revokeCorpusKey("c1", "k1"), null);

  assert.equal((await rag.listMyKeys())[0].id, "k1");
  assert.equal((await rag.createMyKey("n8n", "write")).permission, "write");
  assert.deepEqual(await rag.updateMyKey("k1", { name: "Renamed" }), { name: "Renamed" });
  assert.equal((await rag.rotateMyNamedKey("k1")).api_key, "tk_new");
  assert.equal(await rag.revokeMyKey("k1"), null);

  assert.equal((await rag.listTenantKeys("t1"))[0].id, "k1");
  assert.equal((await rag.createTenantKey("t1", "Ops", "full")).permission, "full");
  assert.deepEqual(await rag.updateTenantKey("t1", "k1", { name: "Ops 2", permission: "write" }), { name: "Ops 2", permission: "write" });
  assert.equal((await rag.rotateTenantNamedKey("t1", "k1")).api_key, "tk_rot");
  assert.equal(await rag.revokeTenantKey("t1", "k1"), null);
});

test("errors carry status and detail; 204 resolves to null; usage passes params", async () => {
  const fetch = mockFetch({
    "DELETE /api/v1/corpora/c1": () => new Response(null, { status: 204 }),
    "GET /api/v1/usage": (u) => json({ params: Object.fromEntries(u.searchParams) }),
    "GET /api/v1/corpora": () => json({ items: [{ id: "c1" }], count: 1 }),
  });
  const rag = new CobRag({ fetch });
  await assert.rejects(rag.getCorpus("missing"), (e) => e instanceof CobRagError && e.status === 404 && e.detail === "Not found");
  assert.equal(await rag.deleteCorpus("c1"), null);
  assert.deepEqual(await rag.listCorpora(), [{ id: "c1" }]);
  await rag.listCorpora({ offset: 100, limit: 50, tenantId: "t1" });
  const listed = fetch.calls.at(-1).url.searchParams;
  assert.deepEqual(Object.fromEntries(listed), { offset: "100", limit: "50", tenant_id: "t1" });
  assert.deepEqual((await rag.usage({ group_by: "source", corpus_id: undefined })).params, { group_by: "source" });
});

test("createCorpus sends the tenant the admin key must name", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora": (_u, init) => {
      const body = JSON.parse(init.body);
      if (!body.tenant_id) {
        return json({ detail: "tenant_id is required: every knowledge base belongs to a tenant", code: "tenant_required" }, 422);
      }
      return json({ id: "c9", api_key: "ck_new", ...body }, 201);
    },
  });
  const rag = new CobRag({ fetch });
  const corpus = await rag.createCorpus("Help", { tenant_id: "t1", description: "d" });
  assert.deepEqual(JSON.parse(fetch.calls.at(-1).init.body), { name: "Help", tenant_id: "t1", description: "d" });
  assert.equal(corpus.tenant_id, "t1");
  await assert.rejects(rag.createCorpus("Orphan"), (e) => e instanceof CobRagError && e.status === 422);
});

test("ingestRecords posts JSON records and waits", async () => {
  const fetch = mockFetch({
    "POST /api/v1/ingest/records": (_u, init) => {
      const body = JSON.parse(init.body);
      assert.deepEqual(body, { records: [{ id: "faq-1", text: "30 days" }], corpus_id: "c1" });
      return json({ job_id: "j2", status: "queued", records: 1 }, 202);
    },
    "GET /api/v1/ingest/jobs/j2": () => json({ status: "completed", records: { created: 1 } }),
  });
  const rag = new CobRag({ fetch });
  const job = await rag.ingestRecords([{ id: "faq-1", text: "30 days" }], { corpusId: "c1", wait: true, pollMs: 1 });
  assert.deepEqual(job.records, { created: 1 });
});

test("dashboard reads: identity, jobs and documents send their params", async () => {
  const seen = [];
  const fetch = async (url, init) => {
    const u = new URL(url);
    seen.push({ method: init.method, path: u.pathname, params: Object.fromEntries(u.searchParams) });
    return json({ ok: true });
  };
  const rag = new CobRag({ baseUrl: "http://engine", apiKey: "tk_t", fetch });
  await rag.whoami();
  await rag.info();
  await rag.ownTenant();
  await rag.rotateOwnTenantKey();
  await rag.listJobs({ status: "failed", corpus_id: "c1" });
  await rag.listDocuments({ corpus_id: "c1", search: "price", limit: undefined });
  await rag.getDocument("d1", { corpus_id: "c1", chunk_limit: 10 });
  assert.deepEqual(seen.map((s) => `${s.method} ${s.path}`), [
    "GET /api/v1/whoami",
    "GET /api/v1/info",
    "GET /api/v1/tenants/me",
    "POST /api/v1/tenants/me/rotate-key",
    "GET /api/v1/ingest/jobs",
    "GET /api/v1/documents",
    "GET /api/v1/documents/d1",
  ]);
  assert.deepEqual(seen[4].params, { status: "failed", corpus_id: "c1" });
  assert.deepEqual(seen[5].params, { corpus_id: "c1", search: "price" });
  assert.deepEqual(seen[6].params, { corpus_id: "c1", chunk_limit: "10" });
});

test("retry a failed ingest job", async () => {
  const fetch = mockFetch({
    "POST /api/v1/ingest/jobs/j1/retry": () => json({ job_id: "j2", status: "queued" }),
  });
  const rag = new CobRag({ apiKey: "tk_t", fetch });
  assert.deepEqual(await rag.retryIngestJob("j1"), { job_id: "j2", status: "queued" });
  assert.equal(fetch.calls.at(-1).init.method, "POST");
});

test("engine settings and public branding", async () => {
  const fetch = mockFetch({
    "GET /api/v1/admin/settings": () => json({ signup_mode: "approval", branding: {} }),
    "PATCH /api/v1/admin/settings": (_u, init) => json({ sent: JSON.parse(init.body) }),
    "GET /api/v1/public/branding": () => json({ product_name: "Sahayak AI", powered_by: { text: "cob-rag-engine by Callobuzz" } }),
  });
  const rag = new CobRag({ apiKey: "admin", fetch });
  assert.equal((await rag.getSettings()).signup_mode, "approval");
  const changes = { signup_mode: "closed", branding: { product_name: "Sahayak AI", logo_url: null } };
  assert.deepEqual((await rag.updateSettings(changes)).sent, changes);
  assert.equal(fetch.calls.at(-1).init.method, "PATCH");
  assert.equal((await rag.publicBranding()).product_name, "Sahayak AI");
  assert.equal(fetch.calls.at(-1).url.pathname, "/api/v1/public/branding");
});

test("smtp status and test e-mail", async () => {
  const fetch = mockFetch({
    "GET /api/v1/admin/smtp": () => json({ provider: "log", configured: true }),
    "POST /api/v1/admin/smtp/test": (_u, init) => json({ sent: true, provider: "log", ...JSON.parse(init.body) }),
  });
  const rag = new CobRag({ apiKey: "admin", fetch });
  assert.equal((await rag.smtpStatus()).provider, "log");
  const res = await rag.sendTestEmail("me@example.com");
  assert.equal(res.to, "me@example.com");
  assert.equal(fetch.calls.at(-1).init.method, "POST");
  assert.equal(fetch.calls.at(-1).url.pathname, "/api/v1/admin/smtp/test");
});

test("tenant lifecycle calls (accounts A4)", async () => {
  const none = () => new Response(null, { status: 204 });
  const fetch = mockFetch({
    "GET /api/v1/admin/tenants": () => json({ items: [{ id: "t1" }], count: 1, total: 1 }),
    "POST /api/v1/admin/tenants/t1/activate": () => json({ status: "active" }),
    "POST /api/v1/admin/tenants/t1/suspend": () => json({ status: "suspended" }),
    "POST /api/v1/admin/tenants/t1/reactivate": () => json({ status: "active" }),
    "POST /api/v1/admin/tenants/t1/decline": none,
    "DELETE /api/v1/admin/tenants/t1": none,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  assert.deepEqual(await rag.listTenants({ status: "pending", search: "shree" }), [{ id: "t1" }]);
  assert.deepEqual(Object.fromEntries(fetch.calls[0].url.searchParams), {
    status: "pending", search: "shree", offset: "0", limit: "100",
  });
  await rag.activateTenant("t1", "paid via UPI");
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), { note: "paid via UPI" });
  assert.equal((await rag.suspendTenant("t1", "Shree Labs")).status, "suspended");
  assert.deepEqual(JSON.parse(fetch.calls[2].init.body), { confirm_name: "Shree Labs" });
  await rag.reactivateTenant("t1");
  assert.equal(await rag.declineTenant("t1"), null);
  assert.deepEqual(JSON.parse(fetch.calls[4].init.body), {});
  assert.equal(await rag.deleteTenant("t1", "Shree Labs"), null);
  assert.equal(fetch.calls[5].url.searchParams.get("confirm_name"), "Shree Labs");
  await rag.listTenants({ isOperator: false });
  assert.equal(fetch.calls[6].url.searchParams.get("is_operator"), "false");
  await rag.deleteTenant("t1", "My workspace", { confirmOperatorWorkspace: true });
  assert.equal(fetch.calls[7].url.searchParams.get("confirm_operator_workspace"), "true");
});

test("plan calls (accounts A6)", async () => {
  const none = () => new Response(null, { status: 204 });
  const echo = (_u, init) => json(init.body ? JSON.parse(init.body) : {});
  const fetch = mockFetch({
    "GET /api/v1/admin/plans": () => json({ items: [{ id: "p1" }], count: 1 }),
    "POST /api/v1/admin/plans": echo,
    "PATCH /api/v1/admin/plans/p1": echo,
    "DELETE /api/v1/admin/plans/p1": none,
    "POST /api/v1/admin/tenants/t1/plan": echo,
    "GET /api/v1/admin/tenants/t1/plan": () => json({ plan: { id: "p1" } }),
    "GET /api/v1/tenants/me/plan": () => json({ usage: { questions: 412 } }),
    "POST /api/v1/admin/tenants/t1/activate": echo,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  assert.deepEqual(await rag.listPlans(), [{ id: "p1" }]);
  assert.equal(fetch.calls[0].url.searchParams.get("include_inactive"), "true");
  await rag.listPlans({ includeInactive: false });
  assert.equal(fetch.calls[1].url.searchParams.get("include_inactive"), "false");
  assert.deepEqual(await rag.createPlan("Starter", { monthly_questions: 1000 }), {
    name: "Starter", monthly_questions: 1000,
  });
  assert.deepEqual(await rag.updatePlan("p1", { monthly_questions: null }), { monthly_questions: null });
  assert.equal(await rag.deletePlan("p1"), null);
  assert.deepEqual(await rag.setTenantPlan("t1", "p1"), { plan_id: "p1" });
  assert.deepEqual(await rag.tenantPlan("t1"), { plan: { id: "p1" } });
  assert.equal((await rag.myPlan()).usage.questions, 412);
  assert.deepEqual(await rag.activateTenant("t1", { planId: "p1", note: "paid" }), {
    plan_id: "p1", note: "paid",
  });
});

test("the question cap is a CobRagError with status 429 and its detail", async () => {
  const fetch = mockFetch({
    "POST /api/v1/query": () => json({
      detail: "Monthly question limit reached — contact support@shree.example",
      code: "question_cap_reached",
      limit: 2,
      used: 2,
      resets_at: "2026-10-01T00:00:00Z",
    }, 429),
  });
  const rag = new CobRag({ apiKey: "ck_t", fetch });
  await assert.rejects(rag.query("hi"), (err) => {
    assert.ok(err instanceof CobRagError);
    assert.equal(err.status, 429);
    assert.match(String(err.detail), /Monthly question limit reached/);
    return true;
  });
});

test("team calls", async () => {
  const none = () => new Response(null, { status: 204 });
  const fetch = mockFetch({
    "GET /api/v1/tenants/me/members": () => json({ members: [], invitations: [] }),
    "POST /api/v1/tenants/me/members": (_u, init) => json({ id: "i1", ...JSON.parse(init.body) }, 201),
    "DELETE /api/v1/tenants/me/invitations/i1": none,
    "POST /api/v1/tenants/me/invitations/i1/resend": () => json({ id: "i1" }),
    "PATCH /api/v1/tenants/me/members/u1": (_u, init) => json({ user_id: "u1", ...JSON.parse(init.body) }),
    "DELETE /api/v1/tenants/me/members/u1": none,
    "GET /api/v1/admin/tenants/t1/members": () => json({ members: [], invitations: [] }),
    "POST /api/v1/admin/tenants/t1/invitations": (_u, init) =>
      json({ id: "i2", accept_url: "http://d/invite#token=inv_x", ...JSON.parse(init.body) }, 201),
  });
  const rag = new CobRag({ apiKey: "tk_x", fetch });
  assert.deepEqual(await rag.team(), { members: [], invitations: [] });
  assert.equal((await rag.invite("ravi@example.com")).role, "member");
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), { email: "ravi@example.com", role: "member" });
  assert.equal(await rag.revokeInvitation("i1"), null);
  assert.equal((await rag.resendInvitation("i1")).id, "i1");
  assert.equal((await rag.setMemberRole("u1", "owner")).role, "owner");
  assert.equal(await rag.removeMember("u1"), null);
  assert.deepEqual(await rag.tenantTeam("t1"), { members: [], invitations: [] });
  const made = await rag.inviteToTenant("t1", "asha@example.com");
  assert.equal(made.role, "owner");
  assert.ok(made.accept_url.includes("#token="));
  assert.deepEqual(
    fetch.calls.map((c) => `${c.init.method} ${c.url.pathname}`),
    [
      "GET /api/v1/tenants/me/members",
      "POST /api/v1/tenants/me/members",
      "DELETE /api/v1/tenants/me/invitations/i1",
      "POST /api/v1/tenants/me/invitations/i1/resend",
      "PATCH /api/v1/tenants/me/members/u1",
      "DELETE /api/v1/tenants/me/members/u1",
      "GET /api/v1/admin/tenants/t1/members",
      "POST /api/v1/admin/tenants/t1/invitations",
    ],
  );
});

test("audit log and admin team calls (accounts A8)", async () => {
  const none = () => new Response(null, { status: 204 });
  const fetch = mockFetch({
    "GET /api/v1/admin/audit": (u) => json({ items: [], total: 0, offset: 0, limit: 50, params: Object.fromEntries(u.searchParams) }),
    "GET /api/v1/admin/plans/p1": () => json({ id: "p1", tenant_count: 2 }),
    "POST /api/v1/admin/tenants/t1/rotate-key": () => json({ api_key: "tk_new" }),
    "DELETE /api/v1/admin/tenants/t1/invitations/i1": none,
    "POST /api/v1/admin/tenants/t1/invitations/i1/resend": () => json({ id: "i1", accept_url: "http://d/invite#token=inv_y" }),
    "PATCH /api/v1/admin/tenants/t1/members/u1": (_u, init) => json({ user_id: "u1", ...JSON.parse(init.body) }),
    "DELETE /api/v1/admin/tenants/t1/members/u1": none,
  });
  const rag = new CobRag({ apiKey: "admin", fetch });
  assert.deepEqual((await rag.auditLog()).params, { offset: "0", limit: "50" });
  const filtered = await rag.auditLog({ tenantId: "t1", action: "tenant.", actor: "asha", start: "2026-09-01", limit: 10 });
  assert.deepEqual(filtered.params, { tenant_id: "t1", action: "tenant.", actor: "asha", start: "2026-09-01", offset: "0", limit: "10" });
  assert.equal((await rag.getPlan("p1")).tenant_count, 2);
  assert.equal((await rag.rotateTenantKey("t1")).api_key, "tk_new");
  assert.equal(await rag.revokeTenantInvitation("t1", "i1"), null);
  assert.ok((await rag.resendTenantInvitation("t1", "i1")).accept_url.includes("#token="));
  assert.equal((await rag.setTenantMemberRole("t1", "u1", "owner")).role, "owner");
  assert.equal(await rag.removeTenantMember("t1", "u1"), null);
  assert.deepEqual(
    fetch.calls.map((c) => `${c.init.method} ${c.url.pathname}`),
    [
      "GET /api/v1/admin/audit",
      "GET /api/v1/admin/audit",
      "GET /api/v1/admin/plans/p1",
      "POST /api/v1/admin/tenants/t1/rotate-key",
      "DELETE /api/v1/admin/tenants/t1/invitations/i1",
      "POST /api/v1/admin/tenants/t1/invitations/i1/resend",
      "PATCH /api/v1/admin/tenants/t1/members/u1",
      "DELETE /api/v1/admin/tenants/t1/members/u1",
    ],
  );
});

test("assistant calls", async () => {
  const echo = (_u, init) => json(JSON.parse(init.body));
  const ok = () => json({});
  const fetch = mockFetch({
    "GET /api/v1/corpora/c1/assistant": ok,
    "PUT /api/v1/corpora/c1/assistant/draft": echo,
    "PATCH /api/v1/corpora/c1/assistant/draft": echo,
    "POST /api/v1/corpora/c1/assistant/draft/from-template": echo,
    "DELETE /api/v1/corpora/c1/assistant/draft": () => new Response(null, { status: 204 }),
    "POST /api/v1/corpora/c1/assistant/publish": echo,
    "GET /api/v1/corpora/c1/assistant/versions": ok,
    "GET /api/v1/corpora/c1/assistant/versions/2": ok,
    "POST /api/v1/corpora/c1/assistant/versions/2/restore": ok,
    "POST /api/v1/corpora/c1/assistant/enabled": echo,
    "GET /api/v1/corpora/c1/assistant/analytics": ok,
    "GET /api/v1/assistant/analytics": ok,
    "POST /api/v1/corpora/c1/assistant/compile": echo,
    "GET /api/v1/assistant/templates": () => json([]),
    "GET /api/v1/assistant/templates/clinic": ok,
  });
  const rag = new CobRag({ apiKey: "tk_x", fetch });
  await rag.getAssistant("c1");
  assert.deepEqual(await rag.putAssistantDraft("c1", { voice: { emoji: "light" } }, { baseRevision: 2 }), {
    config: { voice: { emoji: "light" } },
    base_revision: 2,
  });
  assert.deepEqual(await rag.putAssistantDraft("c1", {}), { config: {} });
  assert.deepEqual(await rag.patchAssistantDraft("c1", { identity: { business_name: "Shree Labs" } }), {
    patch: { identity: { business_name: "Shree Labs" } },
  });
  assert.deepEqual(await rag.assistantDraftFromTemplate("c1", "clinic", { keepIdentity: false }), {
    template: "clinic",
    keep_identity: false,
  });
  assert.equal(await rag.discardAssistantDraft("c1"), null);
  assert.deepEqual(await rag.publishAssistant("c1", { baseRevision: 3, note: "Diwali hours" }), {
    base_revision: 3,
    note: "Diwali hours",
  });
  await rag.assistantVersions("c1", { limit: 5 });
  assert.equal(fetch.calls.at(-1).url.searchParams.get("limit"), "5");
  assert.equal(fetch.calls.at(-1).url.searchParams.get("offset"), "0");
  await rag.assistantVersion("c1", 2);
  await rag.restoreAssistantVersion("c1", 2);
  assert.deepEqual(await rag.setAssistantEnabled("c1", false), { enabled: false });
  await rag.assistantAnalytics("c1", { from: "2026-10-01T00:00:00Z" });
  assert.equal(fetch.calls.at(-1).url.pathname, "/api/v1/corpora/c1/assistant/analytics");
  assert.equal(fetch.calls.at(-1).url.searchParams.get("from"), "2026-10-01T00:00:00Z");
  await rag.assistantAnalytics(null);
  assert.equal(fetch.calls.at(-1).url.pathname, "/api/v1/assistant/analytics");
  assert.deepEqual(await rag.compileAssistant("c1", { sample: { language: "hi-Latn" } }), {
    sample: { language: "hi-Latn" },
  });
  assert.deepEqual(await rag.compileAssistant("c1"), {});
  assert.deepEqual(await rag.assistantTemplates(), []);
  await rag.assistantTemplate("clinic");
});

test("guided setup calls", async () => {
  const echo = (_u, init) => json(init.body ? JSON.parse(init.body) : {});
  const base = "/api/v1/corpora/c1/assistant/setup";
  const fetch = mockFetch({
    [`POST ${base}`]: echo,
    [`GET ${base}/s1`]: echo,
    [`POST ${base}/s1/propose`]: echo,
    [`GET ${base}/s1/questions`]: echo,
    [`POST ${base}/s1/answers`]: echo,
    [`POST ${base}/s1/generate`]: echo,
    [`POST ${base}/s1/tests`]: echo,
    [`GET ${base}/s1/gaps`]: echo,
    [`POST ${base}/s1/apply`]: echo,
  });
  const rag = new CobRag({ apiKey: "tk_x", fetch });
  assert.deepEqual(await rag.startSetup("c1", { businessType: "clinic", websiteUrl: "https://shreelabs.in" }), {
    business_type: "clinic",
    website_url: "https://shreelabs.in",
  });
  assert.deepEqual(await rag.startSetup("c1"), {});
  await rag.getSetup("c1", "s1");
  const pages = [{ url: "https://shreelabs.in/", title: "Home", text: "Blood tests" }];
  assert.deepEqual(await rag.proposeSetup("c1", "s1", pages, { crawlJobId: "job-1" }), { pages, crawl_job_id: "job-1" });
  await rag.setupQuestions("c1", "s1");
  assert.deepEqual(
    await rag.answerSetup("c1", "s1", { tone: "warm" }, { businessType: "clinic", profile: { phone: "+919822012345" } }),
    { answers: { tone: "warm" }, business_type: "clinic", profile: { phone: "+919822012345" } },
  );
  assert.deepEqual(await rag.answerSetup("c1", "s1", { tone: "warm" }, { partial: true }), {
    answers: { tone: "warm" },
    partial: true,
  });
  assert.deepEqual(await rag.generateSetup("c1", "s1", { wording: "template" }), { wording: "template" });
  assert.deepEqual(await rag.generateSetup("c1", "s1", { baseRevision: 2 }), { wording: "ai", base_revision: 2 });
  await rag.runSetupTests("c1", "s1");
  assert.equal(fetch.calls.at(-1).init.body, undefined);
  await rag.setupGaps("c1", "s1");
  assert.deepEqual(await rag.applySetup("c1", "s1", 3, { note: "Guided setup" }), {
    base_revision: 3,
    note: "Guided setup",
  });
});

// The engine refuses a field a request model does not declare (every request body is
// extra="forbid", additionalProperties:false in the committed OpenAPI spec). Checked last, over
// every call the tests above made.
function unknownFields(spec, schema, value, path, out) {
  while (schema.$ref) schema = spec.components.schemas[schema.$ref.split("/").pop()];
  const branches = [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])];
  if (branches.length) {
    let best = null;
    for (const b of branches) {
      const found = [];
      unknownFields(spec, b, value, path, found);
      if (!found.length) return;
      if (!best || found.length < best.length) best = found;
    }
    out.push(...(best ?? []));
    return;
  }
  for (const part of schema.allOf ?? []) unknownFields(spec, part, value, path, out);
  if (Array.isArray(value)) {
    if (schema.items) value.forEach((v, i) => unknownFields(spec, schema.items, v, `${path}.${i}`, out));
  } else if (value && typeof value === "object") {
    const props = schema.properties ?? {};
    for (const [k, v] of Object.entries(value)) {
      if (props[k]) unknownFields(spec, props[k], v, `${path}.${k}`, out);
      else if (schema.additionalProperties === false) out.push(`${path}.${k}`);
    }
  }
}

test("assistant options are sent with the engine's names and its events pass through", async () => {
  let sent;
  const fetch = mockFetch({
    "POST /api/v1/query/stream": (_u, init) => {
      sent = JSON.parse(init.body);
      return streamOf([
        'event: stage\ndata: {"stage":"understand","elapsed_ms":1}\n\n',
        'event: meta\ndata: {"label":"smalltalk","route":"smalltalk","language":"en"}\n\n',
        'event: stage\ndata: {"stage":"write","elapsed_ms":3}\n\nevent: token\ndata: {"content":"Hi"}\n\n',
        'event: quick_replies\ndata: {"items":[{"label":"Prices","message":"Prices?"}]}\n\n',
        'event: done\ndata: {"label":"smalltalk","gate_threshold":0.5}\n\n',
      ])();
    },
    "POST /api/v1/query": (_u, init) => json({ answer: "ok", citations: [], echo: JSON.parse(init.body).assistant }),
  });
  const rag = new CobRag({ baseUrl: "http://engine", apiKey: "ck_x", fetch });
  const names = [];
  const assistant = { useDraft: true, conversationId: "c1", pageUrl: "https://x.in/p", simulate: { officeHours: "closed" } };
  for await (const { event } of rag.queryStreamEvents("hi", { assistant })) names.push(event);
  assert.deepEqual(names, ["stage", "meta", "stage", "token", "quick_replies", "done"]);
  assert.deepEqual(sent.assistant, {
    conversation_id: "c1",
    page_url: "https://x.in/p",
    use_draft: true,
    simulate: { office_hours: "closed" },
  });
  const tokens = [];
  for await (const t of rag.queryStream("hi", { assistant: { debug: true } })) tokens.push(t);
  assert.deepEqual(tokens, ["Hi"]);
  assert.deepEqual(sent.assistant, { debug: true });
  const res = await rag.query("hi", { assistant: { debug: true } });
  assert.deepEqual(res.echo, { debug: true });
});

test("no assistant option sends no field", async () => {
  let sent;
  const fetch = mockFetch({ "POST /api/v1/query": (_u, init) => { sent = JSON.parse(init.body); return json({ answer: "ok" }); } });
  await new CobRag({ fetch }).query("hi");
  assert.equal("assistant" in sent, false);
});

test("golden sets, criteria and settings calls", async () => {
  const fetch = mockFetch({
    "GET /api/v1/corpora/c1/golden-sets": () => json({ items: [{ id: "s1" }], count: 1 }),
    "POST /api/v1/corpora/c1/golden-sets": (_u, init) => json({ id: "s2", ...JSON.parse(init.body) }, 201),
    "POST /api/v1/corpora/c1/golden-sets/from-template": () => json({ id: "s3" }, 201),
    "GET /api/v1/corpora/c1/golden-sets/s1": () => json({ id: "s1", pairs: [] }),
    "PATCH /api/v1/corpora/c1/golden-sets/s1": () => json({ id: "s1" }),
    "POST /api/v1/corpora/c1/golden-sets/s1/pairs": () => json([], 201),
    "DELETE /api/v1/corpora/c2/golden-sets/s1": () => new Response(null, { status: 204 }),
    "GET /api/v1/corpora/c1/eval/criteria": () => json({ items: [{ id: "k1" }] }),
    "POST /api/v1/corpora/c1/eval/criteria": () => json({ id: "k2" }, 201),
    "PATCH /api/v1/corpora/c1/eval/criteria/k1": () => json({ id: "k1" }),
    "DELETE /api/v1/corpora/c2/eval/criteria/k1": () => new Response(null, { status: 204 }),
    "GET /api/v1/corpora/c1/eval/settings": () => json({ judge_has_key: false }),
    "PUT /api/v1/corpora/c1/eval/settings": () => json({ judge_has_key: true }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  assert.equal((await rag.listGoldenSets("c1"))[0].id, "s1");
  await rag.createGoldenSet("c1", "Basics", [{ query: "Hi", expected_behaviour: "smalltalk" }]);
  await rag.goldenSetFromTemplate("c1", "clinic");
  await rag.getGoldenSet("c1", "s1");
  await rag.renameGoldenSet("c1", "s1", "Renamed");
  await rag.addGoldenPairs("c1", "s1", [{ query: "Fees?", expected_answer: "150" }]);
  assert.equal(await rag.deleteGoldenSet("c2", "s1"), null);
  assert.equal((await rag.listEvalCriteria("c1"))[0].id, "k1");
  await rag.createEvalCriterion("c1", "mentions-fee", "States the fee", 0.8);
  await rag.updateEvalCriterion("c1", "k1", { enabled: false });
  assert.equal(await rag.deleteEvalCriterion("c2", "k1"), null);
  await rag.getEvalSettings("c1");
  await rag.setEvalSettings("c1", { judge_model: "judge-x", thresholds: { answer_correctness: 0.8 } });
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), {
    name: "Basics", pairs: [{ query: "Hi", expected_behaviour: "smalltalk" }],
  });
  assert.deepEqual(JSON.parse(fetch.calls[2].init.body), { template: "clinic" });
  assert.deepEqual(JSON.parse(fetch.calls[8].init.body), { name: "mentions-fee", definition: "States the fee", threshold: 0.8 });
});

test("startEvalRun posts the snapshot and the cost confirmation", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora/c1/eval/runs": (_u, init) => json({ id: "r1", ...JSON.parse(init.body) }, 202),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  await rag.startEvalRun("c1", "s1", { mode: "full", confirmCostUsd: 0.11, settings: { assistant: "draft" } });
  assert.deepEqual(JSON.parse(fetch.calls[0].init.body), {
    set_id: "s1", mode: "full", settings: { assistant: "draft" }, confirm_cost_usd: 0.11,
  });
  await rag.startEvalRun("c1", "s1");
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), { set_id: "s1", mode: "fast" });
});

test("evaluation estimates, runs, samples and comparison", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora/c1/eval/estimate": (_u, init) => json({ total_usd: 0.11, ...JSON.parse(init.body) }),
    "GET /api/v1/corpora/c1/eval/runs": () => json({ items: [{ id: "r1" }], count: 1 }),
    "GET /api/v1/corpora/c1/eval/runs/r1": () => json({ id: "r1", metrics: [] }),
    "GET /api/v1/corpora/c1/eval/runs/r1/samples": () => json({ items: [], count: 0, total: 0 }),
    "PATCH /api/v1/corpora/c1/eval/samples/m1": () => json({ id: "m1", rating: "poor" }),
    "POST /api/v1/corpora/c1/eval/compare": () => json({ runs: [], metrics: [], questions: [], warnings: [] }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  const est = await rag.estimateEvalRun("c1", "s1", { settings: { assistant: 3 } });
  assert.equal(est.total_usd, 0.11);
  assert.deepEqual(JSON.parse(fetch.calls[0].init.body), { set_id: "s1", mode: "full", settings: { assistant: 3 } });
  assert.equal((await rag.listEvalRuns("c1", { setId: "s1", kind: "golden" }))[0].id, "r1");
  assert.equal(fetch.calls[1].url.searchParams.get("set_id"), "s1");
  await rag.getEvalRun("c1", "r1");
  await rag.listEvalSamples("c1", "r1", { failedOnly: true, metric: "answer_correctness", below: 0.7, tag: "difficulty:hard" });
  const q = fetch.calls[3].url.searchParams;
  assert.equal(q.get("failed_only"), "true");
  assert.equal(q.get("metric"), "answer_correctness");
  assert.equal(q.get("below"), "0.7");
  assert.equal(q.get("tag"), "difficulty:hard");
  assert.equal(q.get("limit"), "50");
  assert.equal((await rag.rateEvalSample("c1", "m1", "poor")).rating, "poor");
  assert.deepEqual(JSON.parse(fetch.calls[4].init.body), { rating: "poor" });
  await rag.compareEvalRuns("c1", ["r1", "r2"]);
  assert.deepEqual(JSON.parse(fetch.calls[5].init.body), { run_ids: ["r1", "r2"] });
});

test("review items and actions", async () => {
  const fetch = mockFetch({
    "GET /api/v1/corpora/c1/assistant/review": () => json({ items: [{ item_key: "k" }], count: 1 }),
    "POST /api/v1/corpora/c1/assistant/review/actions": (_u, init) => json({ ...JSON.parse(init.body) }, 201),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  const items = await rag.reviewItems("c1", { kind: "no_answer", since: "2026-09-01T00:00:00Z" });
  assert.equal(items[0].item_key, "k");
  assert.equal(fetch.calls[0].url.searchParams.get("from"), "2026-09-01T00:00:00Z");
  assert.equal(fetch.calls[0].url.searchParams.get("kind"), "no_answer");
  await rag.reviewAction("c1", "k".repeat(32), "added_answer", {
    base_revision: 0, answer: { topic_name: "MRI", question: "MRI?", answer: "No." },
  });
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), {
    item_key: "k".repeat(32), action: "added_answer", base_revision: 0,
    answer: { topic_name: "MRI", question: "MRI?", answer: "No." },
  });
});

test("leads methods", async () => {
  const fetch = mockFetch({
    "GET /api/v1/leads": (u) => json({ items: [], total: 0, offset: 0, limit: Number(u.searchParams.get("limit")) }),
    "GET /api/v1/leads/l1": () => json({ id: "l1" }),
    "GET /api/v1/leads/export.csv": () =>
      new Response("\ufeffcreated_at\n", { status: 200, headers: { "content-type": "text/csv" } }),
    "PATCH /api/v1/leads/l1": (_u, init) => json({ id: "l1", ...JSON.parse(init.body) }),
    "DELETE /api/v1/leads/l1": () => new Response(null, { status: 204 }),
    "POST /api/v1/public/leads/withdraw": () => json({ status: "withdrawn" }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  const res = await rag.listLeads({ corpusId: "c1", status: "new", q: "9876543210", limit: 10 });
  assert.equal(res.limit, 10);
  const params = Object.fromEntries(fetch.calls[0].url.searchParams);
  assert.deepEqual(params, { offset: "0", limit: "10", corpus_id: "c1", status: "new", q: "9876543210" });
  assert.equal((await rag.getLead("l1")).id, "l1");
  const csv = await rag.exportLeads({ corpusId: "c1", status: "won" });
  assert.ok(csv.startsWith("\ufeffcreated_at"));
  assert.deepEqual(Object.fromEntries(fetch.calls.at(-1).url.searchParams), { corpus_id: "c1", status: "won" });
  const updated = await rag.updateLead("l1", { status: "contacted", assignee_user_id: null });
  assert.deepEqual(updated, { id: "l1", status: "contacted", assignee_user_id: null });
  assert.equal(await rag.deleteLead("l1"), null);
  assert.deepEqual(await rag.withdrawLeadConsent("t".repeat(30)), { status: "withdrawn" });
  assert.equal(fetch.calls.at(-1).init.headers["X-API-Key"], "tk_t");
});

test("handoff methods", async () => {
  const fetch = mockFetch({
    "GET /api/v1/handoffs": (u) => json({ items: [], total: 0, offset: 0, limit: Number(u.searchParams.get("limit")) }),
    "GET /api/v1/handoffs/h1": () => json({ id: "h1", summary: null }),
    "PATCH /api/v1/handoffs/h1": (_u, init) => json({ id: "h1", ...JSON.parse(init.body) }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  const res = await rag.listHandoffs({ corpusId: "c1", status: "new", channel: "whatsapp", from: "2026-10-01", limit: 10 });
  assert.equal(res.limit, 10);
  assert.deepEqual(Object.fromEntries(fetch.calls[0].url.searchParams), {
    offset: "0",
    limit: "10",
    corpus_id: "c1",
    status: "new",
    channel: "whatsapp",
    from: "2026-10-01",
  });
  assert.equal((await rag.getHandoff("h1")).id, "h1");
  assert.deepEqual(await rag.updateHandoff("h1", "handled"), { id: "h1", status: "handled" });
  assert.equal(fetch.calls.at(-1).init.method, "PATCH");
});

test("knowledge-base webhook methods", async () => {
  const fetch = mockFetch({
    "GET /api/v1/corpora/c2/webhooks": () => json({ webhooks: [{ id: "w1" }], count: 1 }),
    "POST /api/v1/corpora/c2/webhooks": (_u, init) => json({ id: "w2", ...JSON.parse(init.body) }, 201),
    "PATCH /api/v1/corpora/c2/webhooks/w1": (_u, init) => json({ id: "w1", ...JSON.parse(init.body) }),
    "DELETE /api/v1/corpora/c2/webhooks/w1": () => new Response(null, { status: 204 }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  assert.deepEqual(await rag.listCorpusWebhooks("c2"), [{ id: "w1" }]);
  const made = await rag.createCorpusWebhook("c2", { url: "https://crm.example.in/h", events: ["lead.created"], secret: "s".repeat(20) });
  assert.equal(made.url, "https://crm.example.in/h");
  assert.deepEqual(await rag.updateCorpusWebhook("c2", "w1", { enabled: false }), { id: "w1", enabled: false });
  assert.equal(await rag.deleteCorpusWebhook("c2", "w1"), null);
});

test("an export refused for a member carries the engine's code", async () => {
  const fetch = mockFetch({
    "GET /api/v1/leads/export.csv": () => json({ detail: "Only a workspace owner can do this", code: "owner_required" }, 403),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  await assert.rejects(rag.exportLeads({ corpusId: "c1" }), (e) => e.status === 403 && e.code === "owner_required");
});

test("every body the client sent only has fields the engine declares", async () => {
  const { readFileSync } = await import("node:fs");
  const spec = JSON.parse(readFileSync(new URL("../../../docs/openapi.json", import.meta.url), "utf8"));
  const templates = Object.entries(spec.paths).map(([p, ops]) => ({
    rx: new RegExp("^" + p.replace(/\{[^}]+\}/g, "[^/]+") + "$"),
    params: (p.match(/\{/g) ?? []).length,
    ops,
  }));
  let checked = 0;
  const problems = [];
  for (const { url, init } of allCalls) {
    if (typeof init.body !== "string") continue;
    const method = init.method.toLowerCase();
    const hit = templates
      .filter((t) => t.rx.test(url.pathname) && t.ops[method])
      .sort((a, b) => a.params - b.params)[0];
    const schema = hit?.ops[method].requestBody?.content?.["application/json"]?.schema;
    if (!schema) continue;
    checked += 1;
    const found = [];
    unknownFields(spec, schema, JSON.parse(init.body), "body", found);
    problems.push(...found.map((f) => `${init.method} ${url.pathname}: ${f}`));
  }
  assert.ok(checked > 5, "the check saw almost no bodies");
  assert.deepEqual(problems, []);
});

test("improveAssistantRule posts the rule", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora/c1/assistant/rules/improve": () =>
      json({ improved: "Always greet in Hindi.", conflicts: [], notes: "" }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "ck_t", fetch });
  const out = await rag.improveAssistantRule("c1", { instruction: "greet hindi", type: "communication" });
  assert.equal(out.improved, "Always greet in Hindi.");
  assert.deepEqual(JSON.parse(fetch.calls[0].init.body), { instruction: "greet hindi", type: "communication" });
  await rag.improveAssistantRule("c1", { instruction: "x", type: "sales", ruleId: "r1" });
  assert.equal(JSON.parse(fetch.calls[1].init.body).rule_id, "r1");
});

// BIL1 provider balances
test("providerBalances reads the admin providers list", async () => {
  const fetch = mockFetch({
    "GET /api/v1/admin/billing/providers": () => json({ items: [], worst: null }),
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  assert.deepEqual(await rag.providerBalances(), { items: [], worst: null });
  assert.equal(fetch.calls[0].url.pathname, "/api/v1/admin/billing/providers");
});
// /BIL1 provider balances

// BIL2 pricing and accounts
test("billing pricing and account methods call the admin and owner routes", async () => {
  const ok = () => json({ ok: true });
  const fetch = mockFetch({
    "GET /api/v1/admin/billing/plans/p1/pricing": ok,
    "PUT /api/v1/admin/billing/plans/p1/pricing": ok,
    "GET /api/v1/admin/billing/accounts": ok,
    "GET /api/v1/admin/billing/accounts/t1": ok,
    "PATCH /api/v1/admin/billing/accounts/t1": ok,
    "GET /api/v1/billing/account": ok,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  await rag.getPlanPricing("p1");
  await rag.setPlanPricing("p1", { usage_mode: "per_question", per_question_minor: 200 });
  await rag.listBillingAccounts({ q: "shree", paymentType: "postpaid", offset: 25, limit: 10 });
  await rag.getBillingAccount("t1");
  await rag.updateBillingAccount("t1", { overrides: { per_question_minor: 150, payment_type: null } });
  await rag.billingAccount();
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), { usage_mode: "per_question", per_question_minor: 200 });
  const params = fetch.calls[2].url.searchParams;
  assert.equal(params.get("q"), "shree");
  assert.equal(params.get("payment_type"), "postpaid");
  assert.equal(params.get("offset"), "25");
  assert.deepEqual(JSON.parse(fetch.calls[4].init.body), {
    overrides: { per_question_minor: 150, payment_type: null },
  });
  assert.equal(fetch.calls[5].url.pathname, "/api/v1/billing/account");
});
// /BIL2 pricing and accounts

// BIL3 ledger
test("ledger methods call the admin and owner routes", async () => {
  const ok = () => json({ items: [], total: 0 });
  const fetch = mockFetch({
    "GET /api/v1/admin/billing/accounts/t1/ledger": ok,
    "POST /api/v1/admin/billing/accounts/t1/ledger": ok,
    "GET /api/v1/billing/ledger": ok,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  await rag.listLedger("t1", { entryType: "top_up", start: "2026-10-01", end: "2026-10-31", offset: 50, limit: 10 });
  await rag.addLedgerEntry("t1", { entry_type: "top_up", amount_minor: 50000, idempotency_key: "abcdefgh12345678" });
  await rag.billingLedger({ limit: 5 });
  const params = fetch.calls[0].url.searchParams;
  assert.equal(params.get("entry_type"), "top_up");
  assert.equal(params.get("start"), "2026-10-01");
  assert.equal(params.get("end"), "2026-10-31");
  assert.equal(params.get("offset"), "50");
  assert.equal(params.get("limit"), "10");
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), {
    entry_type: "top_up",
    amount_minor: 50000,
    idempotency_key: "abcdefgh12345678",
  });
  assert.equal(fetch.calls[2].url.pathname, "/api/v1/billing/ledger");
  assert.equal(fetch.calls[2].url.searchParams.get("limit"), "5");
});
// /BIL3 ledger

// BIL5 add-ons
test("add-on methods call the admin routes", async () => {
  const ok = () => json({ items: [] });
  const fetch = mockFetch({
    "GET /api/v1/admin/billing/add-ons": ok,
    "POST /api/v1/admin/billing/add-ons": ok,
    "PATCH /api/v1/admin/billing/add-ons/a1": ok,
    "DELETE /api/v1/admin/billing/add-ons/a1": () => new Response(null, { status: 204 }),
    "GET /api/v1/admin/billing/accounts/t1/grants": ok,
    "POST /api/v1/admin/billing/accounts/t1/grants": ok,
    "POST /api/v1/admin/billing/accounts/t1/grants/g1/revoke": ok,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  await rag.listAddOns({ includeInactive: true });
  await rag.createAddOn({ kind: "question_pack", name: "500", currency: "INR", price_minor: 29900, questions: 500 });
  await rag.updateAddOn("a1", { is_active: false });
  await rag.deleteAddOn("a1");
  await rag.listGrants("t1", { activeOnly: true });
  await rag.applyAddOn("t1", { add_on_id: "a1", idempotency_key: "abcdefgh12345678", payment: "external" });
  await rag.revokeGrant("t1", "g1", { memo: "refunded" });
  assert.equal(fetch.calls[0].url.searchParams.get("include_inactive"), "true");
  assert.equal(JSON.parse(fetch.calls[1].init.body).questions, 500);
  assert.deepEqual(JSON.parse(fetch.calls[2].init.body), { is_active: false });
  assert.equal(fetch.calls[4].url.searchParams.get("active_only"), "true");
  assert.equal(JSON.parse(fetch.calls[5].init.body).payment, "external");
  assert.deepEqual(JSON.parse(fetch.calls[6].init.body), { memo: "refunded" });
});
// /BIL5 add-ons

// BIL6 statements
test("statement methods call the admin and owner routes, as an object or as text", async () => {
  const fetch = mockFetch({
    "GET /api/v1/admin/billing/accounts/t1/statements": () => json({ items: [] }),
    "GET /api/v1/admin/billing/accounts/t1/statements/2026-10": (u) =>
      u.searchParams.get("format") === "csv"
        ? new Response("section,date\r\n", { headers: { "content-type": "text/csv" } })
        : json({ month: "2026-10-01" }),
    "GET /api/v1/billing/statements": () => json({ items: [] }),
    "GET /api/v1/billing/statements/2026-09": (u) =>
      u.searchParams.get("format") === "html"
        ? new Response("<h1>Statement</h1>", { headers: { "content-type": "text/html" } })
        : json({ month: "2026-09-01" }),
    "POST /api/v1/billing/top-up-requests": () => json({ sent: true }, 202),
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  await rag.listStatements("t1");
  assert.equal((await rag.getStatement("t1", "2026-10")).month, "2026-10-01");
  assert.equal(await rag.getStatement("t1", "2026-10", { format: "csv" }), "section,date\r\n");
  await rag.billingStatements();
  assert.equal((await rag.billingStatement("2026-09")).month, "2026-09-01");
  assert.equal(await rag.billingStatement("2026-09", { format: "html" }), "<h1>Statement</h1>");
  assert.deepEqual(await rag.requestTopUp({ amountMinor: 500000, note: "UPI" }), { sent: true });
  await rag.requestTopUp();
  assert.equal(fetch.calls[1].url.searchParams.get("format"), null);
  assert.equal(fetch.calls[2].url.searchParams.get("format"), "csv");
  assert.deepEqual(JSON.parse(fetch.calls[6].init.body), { amount_minor: 500000, note: "UPI" });
  assert.deepEqual(JSON.parse(fetch.calls[7].init.body), {});
});

test("a second top-up request is an error with its code", async () => {
  const fetch = mockFetch({
    "POST /api/v1/billing/top-up-requests": () =>
      json({ detail: "too soon", code: "topup_request_too_soon" }, 429),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  await assert.rejects(rag.requestTopUp({ amountMinor: 100 }), (err) => {
    assert.equal(err.status, 429);
    assert.equal(err.code, "topup_request_too_soon");
    return true;
  });
});
// /BIL6 statements

// BIZ3 home and articles
test("home and article methods call the business routes", async () => {
  const fetch = mockFetch({
    "GET /api/v1/home": () => json({ kb_id: "kb1" }),
    "POST /api/v1/corpora/kb1/articles": () => json({ id: "a1", status: "queued" }, 202),
    "GET /api/v1/corpora/kb1/articles/d1": () => json({ id: "a1", body: "From Rs 4,500." }),
    "PUT /api/v1/corpora/kb1/articles/d1": () => json({ id: "a1", status: "queued" }, 202),
    "GET /api/v1/documents": () => json({ items: [], count: 0 }),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  assert.equal((await rag.home()).kb_id, "kb1");
  await rag.home({ corpus_id: "kb1" });
  await rag.createArticle("kb1", { kind: "faq", title: "Do you open on Sunday?", body: "Yes, 10 am to 2 pm." });
  assert.equal((await rag.getArticle("kb1", "d1")).body, "From Rs 4,500.");
  await rag.updateArticle("kb1", "d1", { kind: "faq", title: "Do you open on Sunday?", body: "Yes, 10 am to 1 pm." });
  await rag.listDocuments({ corpus_id: "kb1", source: "articles" });
  assert.equal(fetch.calls[1].url.searchParams.get("corpus_id"), "kb1");
  assert.deepEqual(JSON.parse(fetch.calls[2].init.body), {
    kind: "faq",
    title: "Do you open on Sunday?",
    body: "Yes, 10 am to 2 pm.",
  });
  assert.equal(fetch.calls[5].url.searchParams.get("source"), "articles");
});
// /BIZ3 home and articles

// BIZ1 website learning
test("website learning methods call the owner routes", async () => {
  const fetch = mockFetch({
    "GET /api/v1/corpora/kb1/website": () => json({ sources: [] }),
    "PATCH /api/v1/corpora/kb1/website": () => json({ sources: [] }),
    "POST /api/v1/corpora/kb1/website/learn": () => json({ id: "s1" }, 202),
    "POST /api/v1/corpora/kb1/website/reread": () => json({ queued: 1 }, 202),
    "DELETE /api/v1/corpora/kb1/website/sources/s1": () => new Response(null, { status: 204 }),
    "GET /api/v1/corpora/kb1/website/pages": () => json({ items: [], total: 0, counts: {} }),
    "POST /api/v1/corpora/kb1/website/pages/p1/reread": () => json({ id: "p1" }, 202),
    "PATCH /api/v1/corpora/kb1/website/pages/p1": () => json({ id: "p1" }),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  assert.deepEqual(await rag.website("kb1"), { sources: [] });
  await rag.updateWebsite("kb1", { enabled: false, exclude_patterns: ["/blog/*", "/careers"] });
  await rag.updateWebsite("kb1", { enabled: true });
  await rag.learnWebsite("kb1", "https://shreedental.in");
  assert.deepEqual(await rag.rereadWebsite("kb1"), { queued: 1 });
  await rag.removeWebsiteSource("kb1", "s1");
  await rag.websitePages("kb1", { status: "skipped", q: "cart", limit: 20 });
  await rag.rereadWebsitePage("kb1", "p1");
  await rag.updateWebsitePage("kb1", "p1", true);
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), { enabled: false, exclude_patterns: ["/blog/*", "/careers"] });
  assert.deepEqual(JSON.parse(fetch.calls[2].init.body), { enabled: true }); // unset fields are not sent
  assert.deepEqual(JSON.parse(fetch.calls[3].init.body), { origin: "https://shreedental.in" });
  assert.equal(fetch.calls[6].url.searchParams.get("status"), "skipped");
  assert.equal(fetch.calls[6].url.searchParams.get("limit"), "20");
  assert.deepEqual(JSON.parse(fetch.calls[8].init.body), { excluded: true });
});

test("a second whole-site re-read is an error with its code", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora/kb1/website/reread": () =>
      json({ detail: "Wait.", code: "website_reread_too_soon", retry_after: 540 }, 429, { "Retry-After": "540" }),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  await assert.rejects(rag.rereadWebsite("kb1"), (e) => e.status === 429 && e.code === "website_reread_too_soon");
});
// /BIZ1 website learning

// BIZ2 freshness
test("the schedule and the push link", async () => {
  const fetch = mockFetch({
    "PATCH /api/v1/corpora/kb1/website": () => json({ sources: [] }),
    "POST /api/v1/corpora/kb1/website/learn-key": () =>
      json({ key: "lk_x", push_url: "https://rag.example.in/api/v1/public/learn/lk_x", hint: "lk_x" }, 201),
    "DELETE /api/v1/corpora/kb1/website/learn-key": () => new Response(null, { status: 204 }),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  await rag.updateWebsite("kb1", { refresh_mode: "weekly", adaptive: false });
  const made = await rag.createLearnKey("kb1");
  assert.equal(await rag.deleteLearnKey("kb1"), null);
  assert.deepEqual(JSON.parse(fetch.calls[0].init.body), { refresh_mode: "weekly", adaptive: false });
  assert.equal(made.key, "lk_x");
  assert.equal(fetch.calls[2].init.method, "DELETE");
});
// /BIZ2 freshness

// BIZ4 avatar
test("uploadAvatar sends the picture as a multipart file and returns the engine's path", async () => {
  const fetch = mockFetch({
    "POST /api/v1/corpora/kb1/assistant/avatar": () =>
      json({ avatar_url: "/api/v1/public/avatars/6f0a1b2c-3d4e-4f50-8a6b-7c8d9e0f1a2b.png" }, 201),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  const out = await rag.uploadAvatar("kb1", { name: "asha.png", data: new Uint8Array([1, 2, 3]) });
  assert.match(out.avatar_url, /^\/api\/v1\/public\/avatars\//);
  const body = fetch.calls[0].init.body;
  assert.ok(body instanceof FormData);
  assert.equal(body.get("file").name, "asha.png");
});
// /BIZ4 avatar

// PIP1 profile
test("pipeline profile calls (PIP1)", async () => {
  const none = () => new Response(null, { status: 204 });
  const echo = (_u, init) => json(init.body ? JSON.parse(init.body) : {});
  const fetch = mockFetch({
    "GET /api/v1/admin/pipelines": () => json({ items: [], default_id: null }),
    "POST /api/v1/admin/pipelines": echo,
    "GET /api/v1/admin/pipelines/pf1": () => json({ id: "pf1" }),
    "PATCH /api/v1/admin/pipelines/pf1": echo,
    "DELETE /api/v1/admin/pipelines/pf1": none,
    "GET /api/v1/admin/pipelines/tenants/t1": () => json({ effective: { id: "pf1" } }),
    "PUT /api/v1/admin/pipelines/tenants/t1": echo,
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  assert.deepEqual(await rag.listPipelineProfiles(), { items: [], default_id: null });
  const made = await rag.createPipelineProfile("Fast", "Economy", { config: { priority: "low" } });
  assert.deepEqual(made, { name: "Fast", label: "Economy", config: { priority: "low" } });
  assert.deepEqual(await rag.updatePipelineProfile("pf1", { label: "Basic" }), { label: "Basic" });
  assert.deepEqual(await rag.getPipelineProfile("pf1"), { id: "pf1" });
  await rag.deletePipelineProfile("pf1");
  assert.deepEqual(await rag.setTenantPipeline("t1", null), { profile_id: null });
  assert.deepEqual(await rag.getTenantPipeline("t1"), { effective: { id: "pf1" } });
});
// /PIP1 profile

// PIP3 fair use
test("fair use calls (PIP3)", async () => {
  const echo = (_u, init) => json(init.body ? JSON.parse(init.body) : {});
  const fetch = mockFetch({
    "GET /api/v1/admin/fair-use": () => json({ items: [], total: 0 }),
    "GET /api/v1/admin/fair-use/tenants/t1": () => json({ state: "ok" }),
    "PUT /api/v1/admin/fair-use/tenants/t1": echo,
    "GET /api/v1/fair-use": () => json({ answer_speed: "Standard", state: "ok", metrics: [] }),
  });
  const rag = new CobRag({ apiKey: "adm", fetch });
  assert.deepEqual(await rag.listFairUse({ state: "stopped" }), { items: [], total: 0 });
  assert.deepEqual(await rag.getFairUse("t1"), { state: "ok" });
  const set = await rag.setFairUse("t1", { limits: { questions_per_hour: 100, questions_per_day: null }, clear: ["storage_mb"] });
  assert.deepEqual(set, { limits: { questions_per_hour: 100, questions_per_day: null }, policy: {}, clear: ["storage_mb"] });
  assert.deepEqual(await rag.fairUse(), { answer_speed: "Standard", state: "ok", metrics: [] });
});
// /PIP3 fair use

// TV4 value report
test("value report methods call the value routes", async () => {
  const fetch = mockFetch({
    "GET /api/v1/value/settings": () => json({ saved: false }),
    "PUT /api/v1/value/settings": () => json({ saved: true }),
    "GET /api/v1/value/report": () => json({ month: "2026-10" }),
    "GET /api/v1/value/reports": () => json({ items: [] }),
    "GET /api/v1/admin/tenants/t1/value-report": () => json({ month: "2026-09" }),
  });
  const rag = new CobRag({ apiKey: "tk", fetch });
  assert.equal((await rag.valueSettings()).saved, false);
  await rag.updateValueSettings({
    leadValueMinor: 150000,
    handoffValueMinor: 80000,
    staffHourMinor: 25000,
    minutesPerAnswer: 3,
    industryDefaults: "clinic",
  });
  assert.deepEqual(JSON.parse(fetch.calls[1].init.body), {
    lead_value_minor: 150000,
    handoff_value_minor: 80000,
    staff_hour_minor: 25000,
    minutes_per_answer: 3,
    email_enabled: true,
    industry_defaults: "clinic",
  });
  assert.equal((await rag.valueReport()).month, "2026-10");
  await rag.valueReport("2026-09");
  assert.equal(fetch.calls[3].url.searchParams.get("month"), "2026-09");
  await rag.valueReports();
  assert.equal((await rag.adminValueReport("t1", "2026-09")).month, "2026-09");
});
// /TV4 value report

// TV2 reference
test("lookupReference sends the reference as written", async () => {
  const fetch = mockFetch({
    "GET /api/v1/handoffs/lookup": (u) => json({ ref: u.searchParams.get("ref"), matches: [] }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  const res = await rag.lookupReference("sd4f7k");
  assert.equal(res.ref, "sd4f7k");
  assert.equal(fetch.calls.at(-1).init.method, "GET");
});
// /TV2 reference

test("WhatsApp methods", async () => {
  const fetch = mockFetch({
    "GET /api/v1/corpora/c1/whatsapp": () => json({ available: true, status: "active" }),
    "PUT /api/v1/corpora/c1/whatsapp/manual": (_u, init) => json({ status: "active", ...JSON.parse(init.body) }),
    "PATCH /api/v1/corpora/c1/whatsapp": (_u, init) => json({ status: "active", ...JSON.parse(init.body) }),
    "PUT /api/v1/corpora/c1/whatsapp/documents": (_u, init) => json(JSON.parse(init.body)),
    "DELETE /api/v1/corpora/c1/whatsapp": () => new Response(null, { status: 204 }),
    "GET /api/v1/corpora/c1/whatsapp/threads": (u) => json({ items: [], total: 0, q: Object.fromEntries(u.searchParams) }),
    "GET /api/v1/corpora/c1/whatsapp/threads/t1": () => json({ id: "t1", messages: [] }),
    "POST /api/v1/corpora/c1/whatsapp/threads/t1/reply": (_u, init) => json(JSON.parse(init.body), 201),
    "PATCH /api/v1/corpora/c1/whatsapp/threads/t1": (_u, init) => json({ id: "t1", ...JSON.parse(init.body) }),
  });
  const rag = new CobRag({ baseUrl: "http://engine/", apiKey: "tk_t", fetch });
  assert.equal((await rag.whatsapp("c1")).status, "active");
  const manual = await rag.setWhatsappManual("c1", {
    phoneNumberId: "1234567890",
    wabaId: "5550001",
    accessToken: "EAAG-token",
    metaBilling: "deployer",
  });
  assert.deepEqual(
    { id: manual.phone_number_id, waba: manual.waba_id, billing: manual.meta_billing },
    { id: "1234567890", waba: "5550001", billing: "deployer" },
  );
  assert.equal((await rag.updateWhatsapp("c1", { human_pause_hours: 6 })).human_pause_hours, 6);
  assert.deepEqual(await rag.setWhatsappDocuments("c1", ["d1"]), { document_ids: ["d1"] });
  assert.deepEqual((await rag.whatsappThreads("c1", { unread: true, limit: 20 })).q, { offset: "0", limit: "20", unread: "true" });
  assert.equal((await rag.whatsappThread("c1", "t1")).id, "t1");
  assert.deepEqual(await rag.whatsappReply("c1", "t1", "We open at 9."), { text: "We open at 9." });
  assert.equal((await rag.updateWhatsappThread("c1", "t1", { botPaused: false })).bot_paused, false);
  assert.equal(await rag.disconnectWhatsapp("c1"), null);
});
